"""
University Timetabling – Flask Backend
=======================================
Server web per la gestione dati e generazione orario.

Endpoints:
  GET  /              → Interfaccia web principale
  GET  /api/db        → Restituisce il database corrente (JSON)
  POST /api/db        → Salva il database (JSON body)
  POST /api/schedule  → Esegue il solver e restituisce l'orario generato
  GET  /api/schedule  → Restituisce l'ultimo orario generato (cache)
  GET  /api/export/flat → Esporta il DB in formato flat per il solver
  GET  /api/export/pdf  → Genera PDF via LaTeX (timetable compilato)
"""

import json
import os
import time
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response, make_response

import scheduler
import latex_export

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DB_FILE = DATA_DIR / 'database.json'
SCHEDULE_FILE = DATA_DIR / 'last_schedule.json'

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database JSON su disco
# ---------------------------------------------------------------------------
DEFAULT_DB = {
    "meta": {
        "version": 1,
        "timeModel": {
            "dayStart": 8, "dayEnd": 19,
            "lunchStart": 13, "lunchEnd": 14,
            "granularityHours": 1
        },
        "note": "DB per timetabling dipartimentale."
    },
    "rooms": [],
    "teachers": [],
    "programs": [],
    "curricula": [],
    "courses": [],
    "unavailability": [],
    "softPolicy": {
        "weights": {
            "patternViolation": 1000,
            "curriculumGapPerHour": 10,
            "teacherConsecutiveOver3PerHour": 30,
            "teacherDailyOver5PerHour": 20,
            "lateStartPenalty": 3,
            "earlyStartPenalty": 2,
            "lunchOverlapPenalty": 200
        },
        "preferredPatterns": {
            "twoEvents": ["Mon-Wed", "Wed-Fri", "Tue-Thu"],
            "threeEvents": ["Mon-Wed-Fri"]
        }
    }
}


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_db():
    """Carica il database da disco. Se non esiste, usa il default."""
    _ensure_data_dir()
    if DB_FILE.exists():
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return json.loads(json.dumps(DEFAULT_DB))


def save_db(data):
    """Salva il database su disco."""
    _ensure_data_dir()
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_schedule():
    """Carica l'ultimo schedule generato."""
    if SCHEDULE_FILE.exists():
        try:
            with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_schedule(result):
    """Salva il risultato dello scheduling su disco."""
    _ensure_data_dir()
    with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/db', methods=['GET'])
def get_db():
    db = load_db()
    return jsonify(db)


@app.route('/api/db', methods=['POST'])
def post_db():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Payload deve essere un oggetto JSON.'}), 400
    save_db(data)
    return jsonify({'ok': True, 'message': 'Database salvato.'})


@app.route('/api/schedule', methods=['POST'])
def generate_schedule():
    """Esegue il motore di scheduling e restituisce il risultato."""
    db = load_db()

    # Parametri opzionali dal body
    body = request.get_json(silent=True) or {}
    time_limit = body.get('timeLimitSeconds', 30)
    semester = body.get('semester', None)  # 1, 2 o None (tutti)

    # Filtra corsi per semestre se specificato
    if semester in (1, 2):
        filtered_course_ids = set()
        db['courses'] = [c for c in db.get('courses', []) if c.get('semester') == semester]
        filtered_course_ids = {c['id'] for c in db['courses']}
        # Filtra anche le indisponibilità solo per i docenti coinvolti
        active_teacher_ids = set()
        for c in db['courses']:
            active_teacher_ids.update(c.get('teacherIds', []))
        db['unavailability'] = [
            u for u in db.get('unavailability', [])
            if u.get('teacherId') in active_teacher_ids
        ]

    t0 = time.time()
    try:
        result = scheduler.solve(db, time_limit_s=time_limit)
    except Exception as e:
        result = {
            'assignments': [],
            'status': 'error',
            'message': f'Errore nel solver: {str(e)}',
        }
    elapsed = round(time.time() - t0, 2)
    result['solveTimeSeconds'] = elapsed
    result['solverBackend'] = 'or-tools CP-SAT' if scheduler.HAS_ORTOOLS else 'greedy heuristic'
    result['timestamp'] = time.strftime('%Y-%m-%d %H:%M:%S')
    result['semester'] = semester  # None = tutti, 1 o 2

    save_schedule(result)
    return jsonify(result)


@app.route('/api/schedule', methods=['GET'])
def get_schedule():
    """Restituisce l'ultimo schedule generato (se esiste)."""
    result = load_schedule()
    if result is None:
        return jsonify({
            'assignments': [],
            'status': 'none',
            'message': 'Nessun orario generato. Clicca "Genera Orario".'
        })
    return jsonify(result)


@app.route('/api/export/flat', methods=['GET'])
def export_flat():
    """Esporta il DB in formato flat (riferimenti risolti) per uso esterno."""
    db = load_db()
    rooms_by_id = {r['id']: r for r in db.get('rooms', [])}
    teachers_by_id = {t['id']: t for t in db.get('teachers', [])}
    programs_by_id = {p['id']: p for p in db.get('programs', [])}
    curricula_by_id = {c['id']: c for c in db.get('curricula', [])}

    flat = {
        'meta': db.get('meta'),
        'rooms': db.get('rooms'),
        'teachers': db.get('teachers'),
        'programs': db.get('programs'),
        'curricula': db.get('curricula'),
        'courses': [{
            'id': c['id'],
            'name': c.get('name', ''),
            'program': programs_by_id.get(c.get('programId', ''), {'id': c.get('programId', '')}),
            'curricula': [curricula_by_id.get(cid, {'id': cid}) for cid in c.get('curriculaIds', [])],
            'teachers': [teachers_by_id.get(tid, {'id': tid}) for tid in c.get('teacherIds', [])],
            'expectedStudents': c.get('expectedStudents', 0),
            'roomType': c.get('roomType', 'lecture'),
            'weeklyEvents': c.get('weeklyEvents', []),
            'patternPref': c.get('patternPref', ''),
        } for c in db.get('courses', [])],
        'unavailability': [{
            'teacher': teachers_by_id.get(u.get('teacherId', ''), {'id': u.get('teacherId', '')}),
            'day': u.get('day', ''),
            'hours': u.get('hours', []),
        } for u in db.get('unavailability', [])],
        'softPolicy': db.get('softPolicy'),
    }
    return jsonify(flat)


@app.route('/api/export/pdf', methods=['GET'])
def export_pdf():
    """Genera l'orario in PDF compilato da LaTeX."""
    schedule = load_schedule()
    if not schedule or schedule.get('status') in ('none', None):
        return jsonify({'error': 'Nessun orario generato. Genera prima l\'orario.'}), 400

    db = load_db()

    # Filtri opzionali via query string
    filters = {
        'curriculum': request.args.get('curriculum', ''),
        'teacher': request.args.get('teacher', ''),
        'room': request.args.get('room', ''),
    }

    try:
        pdf_bytes = latex_export.export_pdf(schedule, db, filters)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500

    semester = schedule.get('semester', '')
    fname = f'orario_lm18_sem{semester}.pdf' if semester else 'orario_lm18.pdf'

    resp = make_response(pdf_bytes)
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


@app.route('/api/export/tex', methods=['GET'])
def export_tex():
    """Restituisce il sorgente LaTeX (utile per personalizzazione manuale)."""
    schedule = load_schedule()
    if not schedule or schedule.get('status') in ('none', None):
        return jsonify({'error': 'Nessun orario generato.'}), 400

    db = load_db()
    filters = {
        'curriculum': request.args.get('curriculum', ''),
        'teacher': request.args.get('teacher', ''),
        'room': request.args.get('room', ''),
    }

    tex = latex_export.generate_latex(schedule, db, filters)

    resp = make_response(tex)
    resp.headers['Content-Type'] = 'application/x-tex; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename="orario.tex"'
    return resp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    _ensure_data_dir()
    print(f"╔════════════════════════════════════════════════════╗")
    print(f"║  University Timetabling System                    ║")
    print(f"║  Server: http://127.0.0.1:5000                   ║")
    print(f"║  Solver: {'OR-Tools CP-SAT' if scheduler.HAS_ORTOOLS else 'Greedy (installa ortools per CP-SAT)':40s} ║")
    print(f"║  Data:   {str(DATA_DIR):40s}   ║")
    print(f"╚════════════════════════════════════════════════════╝")
    app.run(debug=True, host='0.0.0.0', port=5000)
