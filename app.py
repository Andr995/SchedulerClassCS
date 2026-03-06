"""
University Timetabling – Flask Backend
=======================================
Server web per la gestione dati e generazione orario.

Endpoints:
    GET  /              → Interfaccia pubblica (sola visualizzazione orario)
    GET  /admin         → Interfaccia amministrativa (protetta da password)
  GET  /api/db        → Restituisce il database corrente (JSON)
  POST /api/db        → Salva il database (JSON body)
  POST /api/schedule  → Esegue il solver e restituisce l'orario generato
  GET  /api/schedule  → Restituisce l'ultimo orario generato (cache)
    GET  /api/export/flat → Esporta il DB in formato flat per il solver (admin)
    GET  /api/export/pdf  → Genera PDF senza LaTeX
"""

import json
import os
import time
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, request, jsonify, make_response, session, redirect, url_for

import scheduler
import pdf_export

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DB_FILE = DATA_DIR / 'database.json'
SCHEDULE_FILE = DATA_DIR / 'last_schedule.json'

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'change-me-in-production')

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')


def is_admin_logged():
    return bool(session.get('is_admin'))


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_admin_logged():
            return jsonify({'error': 'Accesso amministratore richiesto.'}), 401
        return func(*args, **kwargs)
    return wrapper

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


def _normalize_schedule_payload(payload, db, base_schedule=None, source='manual'):
    """Normalizza un payload orario e ricalcola il report vincoli hard."""
    if not isinstance(payload, dict):
        raise ValueError('Payload orario non valido: atteso oggetto JSON.')

    assignments_in = payload.get('assignments')
    if not isinstance(assignments_in, list):
        raise ValueError('Payload orario non valido: manca lista assignments.')

    tm = db.get('meta', {}).get('timeModel', {})
    ds = int(tm.get('dayStart', 8))
    de = int(tm.get('dayEnd', 19))

    rooms_by_id = {r.get('id', ''): r for r in db.get('rooms', [])}
    courses_by_id = {c.get('id', ''): c for c in db.get('courses', [])}
    teachers_by_id = {t.get('id', ''): t for t in db.get('teachers', [])}

    normalized_assignments = []
    for i, a in enumerate(assignments_in):
        if not isinstance(a, dict):
            continue

        event_id = a.get('eventId') or f'MAN-{i + 1}'
        course_id = a.get('courseId', '')
        course = courses_by_id.get(course_id, {})
        course_name = a.get('courseName') or course.get('name', course_id)

        day = a.get('day', 'N/A')
        if day not in scheduler.DAYS:
            day = 'N/A'

        duration = int(a.get('duration', 1) or 1)
        if duration < 1:
            duration = 1

        start_hour = int(a.get('startHour', -1) or -1)
        if day == 'N/A':
            start_hour = -1

        if start_hour >= 0:
            start_hour = max(ds, min(start_hour, de - 1))
            end_hour = start_hour + duration
        else:
            end_hour = -1

        room_id = a.get('roomId', 'N/A')
        room = rooms_by_id.get(room_id, {})
        room_name = a.get('roomName') or room.get('name', room_id)

        teacher_ids = a.get('teacherIds') or course.get('teacherIds', [])
        teacher_names = a.get('teacherNames') or [
            teachers_by_id.get(tid, {}).get('name', tid) for tid in teacher_ids
        ]

        normalized_assignments.append({
            'eventId': event_id,
            'courseId': course_id,
            'courseName': course_name,
            'day': day,
            'dayIt': scheduler.DAY_NAMES_IT.get(day, 'N/A'),
            'startHour': start_hour,
            'endHour': end_hour,
            'duration': duration,
            'roomId': room_id,
            'roomName': room_name,
            'teacherIds': teacher_ids,
            'teacherNames': teacher_names,
            'curriculaIds': a.get('curriculaIds') or course.get('curriculaIds', []),
            'programId': a.get('programId') or course.get('programId', ''),
            'color': a.get('color') or scheduler._course_color(course_id),
        })

    report = scheduler._validate_hard_constraints(normalized_assignments, db)

    out = dict(base_schedule or {})
    out.update(payload)
    out['assignments'] = normalized_assignments
    out['hardConstraintReport'] = report
    out['timestamp'] = time.strftime('%Y-%m-%d %H:%M:%S')
    out['status'] = payload.get('status') or ('manual' if source == 'manual' else 'imported')
    out['message'] = payload.get('message') or (
        'Orario modificato manualmente.' if source == 'manual' else 'Orario importato da JSON.'
    )
    out['solverBackend'] = out.get('solverBackend') or 'Manual editing'
    out['algorithm'] = out.get('algorithm') or ('Manual' if source == 'manual' else 'Imported')
    out['algorithmLabel'] = out.get('algorithmLabel') or (
        'Manual Editing' if source == 'manual' else 'Imported Schedule JSON'
    )
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/admin', methods=['GET'])
def admin_page():
    if not is_admin_logged():
        return render_template('admin_login.html')
    return render_template('admin.html')


@app.route('/admin/login', methods=['POST'])
def admin_login():
    password = request.form.get('password', '')
    if password == ADMIN_PASSWORD:
        session['is_admin'] = True
        return redirect(url_for('admin_page'))
    return render_template('admin_login.html', error='Password non corretta.')


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_page'))


@app.route('/api/db', methods=['GET'])
@admin_required
def get_db():
    db = load_db()
    return jsonify(db)


@app.route('/api/db', methods=['POST'])
@admin_required
def post_db():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Payload deve essere un oggetto JSON.'}), 400
    save_db(data)
    return jsonify({'ok': True, 'message': 'Database salvato.'})


@app.route('/api/schedule', methods=['POST'])
@admin_required
def generate_schedule():
    """Esegue il motore di scheduling e restituisce il risultato."""
    db = load_db()

    # Parametri opzionali dal body
    body = request.get_json(silent=True) or {}
    time_limit = body.get('timeLimitSeconds', 30)
    semester = body.get('semester', None)  # 1, 2 o None (tutti)
    requested_algorithm = (body.get('algorithm', 'auto') or 'auto').strip().lower()

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
        result = scheduler.solve(db, time_limit_s=time_limit, algorithm=requested_algorithm)
    except Exception as e:
        result = {
            'assignments': [],
            'status': 'error',
            'message': f'Errore nel solver: {str(e)}',
        }
    elapsed = round(time.time() - t0, 2)
    result['solveTimeSeconds'] = elapsed
    result['requestedAlgorithm'] = requested_algorithm
    result['solverBackend'] = result.get('algorithmLabel') or (
        'Google OR-Tools CP-SAT' if scheduler.HAS_ORTOOLS else 'Greedy Heuristic Fallback'
    )
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


@app.route('/api/schedule/import', methods=['POST'])
@admin_required
def import_schedule_json():
    """Importa un JSON orario e lo salva come ultimo schedule."""
    payload = request.get_json(force=True)
    db = load_db()
    try:
        normalized = _normalize_schedule_payload(payload, db, base_schedule=load_schedule(), source='import')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    save_schedule(normalized)
    return jsonify({'ok': True, 'message': 'Orario importato con successo.', 'schedule': normalized})


@app.route('/api/schedule/manual-update', methods=['POST'])
@admin_required
def manual_update_schedule():
    """Salva modifiche manuali alle assegnazioni orario."""
    payload = request.get_json(force=True)
    db = load_db()
    base = load_schedule() or {}
    try:
        normalized = _normalize_schedule_payload(payload, db, base_schedule=base, source='manual')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    save_schedule(normalized)
    return jsonify({'ok': True, 'message': 'Orario aggiornato manualmente.', 'schedule': normalized})


@app.route('/api/export/schedule-json', methods=['GET'])
@admin_required
def export_schedule_json():
    """Esporta l'ultimo orario generato in JSON."""
    schedule = load_schedule()
    if not schedule:
        return jsonify({'error': 'Nessun orario generato da esportare.'}), 400

    content = json.dumps(schedule, ensure_ascii=False, indent=2)
    resp = make_response(content)
    resp.headers['Content-Type'] = 'application/json; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename="orario_generato.json"'
    return resp


@app.route('/api/public/timetable', methods=['GET'])
def get_public_timetable():
    """Dati minimali per la vista pubblica (senza accesso al DB completo)."""
    db = load_db()
    schedule = load_schedule()
    if schedule is None:
        schedule = {
            'assignments': [],
            'status': 'none',
            'message': 'Nessun orario generato. '
                       'Un amministratore deve prima generare l\'orario.',
        }

    courses_by_id = {c['id']: c for c in db.get('courses', [])}
    curricula_by_id = {c['id']: c for c in db.get('curricula', [])}

    curriculum_tables = []
    for curriculum in db.get('curricula', []):
        cid = curriculum.get('id', '')
        rows = []
        for a in schedule.get('assignments', []):
            if a.get('day') == 'N/A':
                continue
            course = courses_by_id.get(a.get('courseId', ''), {})
            if cid in course.get('curriculaIds', []):
                rows.append(a)
        rows.sort(key=lambda x: (x.get('day', ''), x.get('startHour', 0), x.get('courseName', '')))
        curriculum_tables.append({
            'curriculumId': cid,
            'curriculumName': curriculum.get('name', cid),
            'yearCohort': curriculum.get('yearCohort', ''),
            'rows': rows,
        })

    return jsonify({
        'schedule': schedule,
        'curriculumTables': curriculum_tables,
        'curricula': [
            {
                'id': c.get('id', ''),
                'name': c.get('name', ''),
                'yearCohort': c.get('yearCohort', ''),
                'programId': c.get('programId', ''),
            }
            for c in db.get('curricula', [])
        ],
        'meta': db.get('meta', {}),
        'algorithm': {
            'available': 'Google OR-Tools CP-SAT' if scheduler.HAS_ORTOOLS else 'Greedy Heuristic Fallback',
            'engine': 'CP-SAT' if scheduler.HAS_ORTOOLS else 'Greedy',
        },
        'courseCurricula': {
            cid: course.get('curriculaIds', [])
            for cid, course in courses_by_id.items()
        },
        'curriculaById': {
            cid: {
                'name': c.get('name', cid),
                'yearCohort': c.get('yearCohort', ''),
            }
            for cid, c in curricula_by_id.items()
        }
    })


@app.route('/api/export/flat', methods=['GET'])
@admin_required
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
    """Genera l'orario in PDF senza LaTeX."""
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
        pdf_bytes = pdf_export.export_pdf(schedule, db, filters)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500

    semester = schedule.get('semester', '')
    fname = f'orario_lm18_sem{semester}.pdf' if semester else 'orario_lm18.pdf'

    resp = make_response(pdf_bytes)
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
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
    print(f"║  Admin password env var: ADMIN_PASSWORD           ║")
    print(f"║  Data:   {str(DATA_DIR):40s}   ║")
    print(f"╚════════════════════════════════════════════════════╝")
    app.run(debug=True, host='0.0.0.0', port=5000)
