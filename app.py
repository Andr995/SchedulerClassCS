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
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, make_response, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import requests
from bs4 import BeautifulSoup

import scheduler
import pdf_export

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DB_FILE = DATA_DIR / 'database.json'
SCHEDULE_FILE = DATA_DIR / 'last_schedule.json'
USERS_FILE = DATA_DIR / 'users.json'

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'change-me-in-production')

DEFAULT_ADMIN_USERNAME = os.environ.get('DEFAULT_ADMIN_USERNAME', 'admin')
DEFAULT_ADMIN_PASSWORD = os.environ.get('DEFAULT_ADMIN_PASSWORD', 'admin')


def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_username(value):
    return str(value or '').strip().lower()


def _public_user(user):
    return {
        'username': user.get('username', ''),
        'role': user.get('role', 'admin'),
        'isActive': bool(user.get('isActive', True)),
        'createdAt': user.get('createdAt', ''),
        'lastLoginAt': user.get('lastLoginAt', ''),
    }


def _validate_password_strength(password, allow_weak_default=False):
    pwd = str(password or '')

    if allow_weak_default and pwd == 'admin':
        return True, ''

    if len(pwd) < 12:
        return False, 'La password deve avere almeno 12 caratteri.'
    if not any(ch.islower() for ch in pwd):
        return False, 'La password deve includere almeno una lettera minuscola.'
    if not any(ch.isupper() for ch in pwd):
        return False, 'La password deve includere almeno una lettera maiuscola.'
    if not any(ch.isdigit() for ch in pwd):
        return False, 'La password deve includere almeno un numero.'
    if not any(not ch.isalnum() for ch in pwd):
        return False, 'La password deve includere almeno un simbolo.'

    return True, ''


def _hash_password(password):
    # scrypt e robusto e supportato da Werkzeug moderno.
    return generate_password_hash(password, method='scrypt')


def _find_user(users, username):
    u = _normalize_username(username)
    for item in users:
        if _normalize_username(item.get('username', '')) == u:
            return item
    return None


def _active_admin_count(users):
    return sum(1 for u in users if u.get('role') == 'admin' and u.get('isActive', True))


def load_users():
    _ensure_data_dir()
    users = []

    if USERS_FILE.exists():
        try:
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    users = raw
        except (json.JSONDecodeError, IOError):
            users = []

    created = False
    if not users:
        users = [{
            'username': _normalize_username(DEFAULT_ADMIN_USERNAME) or 'admin',
            'passwordHash': _hash_password(DEFAULT_ADMIN_PASSWORD or 'admin'),
            'role': 'admin',
            'isActive': True,
            'createdAt': _utc_now_iso(),
            'lastLoginAt': '',
        }]
        created = True

    # Garantisce che esista sempre almeno un admin attivo.
    if _active_admin_count(users) == 0:
        users.append({
            'username': 'admin',
            'passwordHash': _hash_password('admin'),
            'role': 'admin',
            'isActive': True,
            'createdAt': _utc_now_iso(),
            'lastLoginAt': '',
        })
        created = True

    if created:
        save_users(users)

    return users


def save_users(users):
    _ensure_data_dir()
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


def get_current_user():
    username = _normalize_username(session.get('username', ''))
    if not username:
        return None
    users = load_users()
    user = _find_user(users, username)
    if not user or not user.get('isActive', True):
        return None
    return user


def is_admin_logged():
    user = get_current_user()
    return bool(user and user.get('role') == 'admin')


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
            "lunchOverlapPenalty": 200,
            "curriculumRoomChangePenalty": 6
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
    db = None
    if DB_FILE.exists():
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                db = json.load(f)
        except (json.JSONDecodeError, IOError):
            db = None

    if db is None:
        db = json.loads(json.dumps(DEFAULT_DB))

    db = _ensure_db_shape(db)
    db, _, sync_stats = _ensure_unique_teacher_ids(db)
    if DB_FILE.exists() and (sync_stats.get('teacherIdsChanged', 0) > 0 or sync_stats.get('referenceUpdates', 0) > 0):
        save_db(db)

    return db


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
    programs_by_id = {p.get('id', ''): p for p in db.get('programs', [])}

    def _to_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

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

        duration = _to_int(a.get('duration', 1), 1)
        if duration < 1:
            duration = 1

        start_hour = _to_int(a.get('startHour', -1), -1)
        if day == 'N/A':
            start_hour = -1

        if start_hour >= 0:
            # Se la durata supera la finestra giornaliera, marca l'evento come non collocato.
            if duration > (de - ds):
                day = 'N/A'
                start_hour = -1
                end_hour = -1
            else:
                latest_start = max(ds, de - duration)
                start_hour = max(ds, min(start_hour, latest_start))
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

        program_id = a.get('programId') or course.get('programId', '')

        normalized_assignments.append({
            'eventId': event_id,
            'courseId': course_id,
            'courseName': course_name,
            'studyYear': _course_study_year(course),
            'mutuationGroup': _course_mutuation_group(course),
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
            'programId': program_id,
            'programName': a.get('programName') or programs_by_id.get(program_id, {}).get('name', program_id),
            'color': a.get('color') or scheduler._course_color(course_id),
        })

    report = scheduler._validate_hard_constraints(normalized_assignments, db)
    soft_report = scheduler._evaluate_soft_constraints(normalized_assignments, db)

    out = dict(base_schedule or {})
    out.update(payload)
    out['assignments'] = normalized_assignments
    out['hardConstraintReport'] = report
    out['softConstraintReport'] = soft_report
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


def _course_study_year(course):
    """Estrae l'anno di studio del corso (1,2,3,...) da campi noti."""
    candidates = [
        course.get('year'),
        course.get('studyYear'),
        course.get('anno'),
    ]
    for raw in candidates:
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            digits = ''.join(ch for ch in raw if ch.isdigit())
            if digits:
                try:
                    return int(digits)
                except ValueError:
                    continue
    return None


def _course_mutuation_group(course):
    """Restituisce la chiave normalizzata del gruppo di mutuazione del corso."""
    raw = course.get('mutuationGroup') or course.get('sharedWithCourseId') or ''
    return str(raw).strip()


def _build_room_reservations_from_assignments(assignments):
    """Converte assegnazioni esistenti in blocchi room/day/hour."""
    blocked = {}
    for a in assignments:
        day = a.get('day', '')
        room_id = a.get('roomId', '')
        start = int(a.get('startHour', -1) or -1)
        end = int(a.get('endHour', -1) or -1)
        if day not in scheduler.DAYS or start < 0 or end <= start or not room_id or room_id == 'N/A':
            continue

        key = (room_id, day)
        if key not in blocked:
            blocked[key] = set()
        for h in range(start, end):
            blocked[key].add(h)

    out = []
    for (room_id, day), hours in blocked.items():
        out.append({
            'roomId': room_id,
            'day': day,
            'hours': sorted(hours),
        })
    return out


def _solve_with_infeasible_fallback(db, time_limit, requested_algorithm):
    """Esegue il solver richiesto e, se infeasible, tenta fallback greedy."""
    try:
        result = scheduler.solve(db, time_limit_s=time_limit, algorithm=requested_algorithm)
    except Exception as e:
        return {
            'assignments': [],
            'status': 'error',
            'message': f'Errore nel solver: {str(e)}',
        }

    if result.get('status') != 'infeasible':
        return result

    try:
        fallback = scheduler.solve(db, time_limit_s=time_limit, algorithm='greedy')
    except Exception:
        return result

    if fallback.get('status') in ('partial', 'feasible', 'optimal') and isinstance(fallback.get('assignments'), list):
        base_msg = result.get('message', '')
        fb_msg = fallback.get('message', '')
        fallback['message'] = (
            f"{base_msg} Fallback automatico su Greedy attivato. {fb_msg}"
        ).strip()
        fallback['fallbackFromAlgorithm'] = requested_algorithm
        return fallback

    return result


def _ensure_db_shape(data):
    if not isinstance(data, dict):
        data = {}
    for key, default in (
        ('rooms', []), ('teachers', []), ('programs', []), ('curricula', []),
        ('courses', []), ('unavailability', []),
    ):
        if not isinstance(data.get(key), list):
            data[key] = list(default)
    if not isinstance(data.get('meta'), dict):
        data['meta'] = json.loads(json.dumps(DEFAULT_DB['meta']))
    if not isinstance(data.get('softPolicy'), dict):
        data['softPolicy'] = json.loads(json.dumps(DEFAULT_DB['softPolicy']))
    if not isinstance(data.get('softPolicy', {}).get('weights'), dict):
        data['softPolicy']['weights'] = {}
    for key, default_value in DEFAULT_DB.get('softPolicy', {}).get('weights', {}).items():
        data['softPolicy']['weights'].setdefault(key, default_value)
    if not isinstance(data.get('softPolicy', {}).get('preferredPatterns'), dict):
        data['softPolicy']['preferredPatterns'] = json.loads(
            json.dumps(DEFAULT_DB['softPolicy']['preferredPatterns'])
        )
    return data


def _normalize_for_id(value):
    text = str(value or '').strip().lower()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')[:36]


def _unique_id(prefix, base, existing_ids):
    clean = _normalize_for_id(base) or prefix.lower()
    candidate = f'{prefix}-{clean}'.upper()
    n = 2
    while candidate in existing_ids:
        candidate = f'{prefix}-{clean}-{n}'.upper()
        n += 1
    existing_ids.add(candidate)
    return candidate


def _ensure_unique_teacher_ids(data, schedule=None):
    """Garantisce ID docenti univoci e riallinea i riferimenti nel DB/orario."""
    teachers = data.get('teachers', []) if isinstance(data, dict) else []
    if not isinstance(teachers, list):
        return data, schedule, {'teacherIdsChanged': 0, 'referenceUpdates': 0}

    used_ids = set()
    canonical_by_old = {}
    teacher_id_changes = 0

    for i, teacher in enumerate(teachers):
        if not isinstance(teacher, dict):
            continue

        old_id = str(teacher.get('id', '')).strip()
        if old_id and old_id not in used_ids:
            new_id = old_id
            used_ids.add(new_id)
            canonical_by_old.setdefault(old_id, new_id)
        else:
            base = old_id or teacher.get('name') or f'teacher-{i + 1}'
            new_id = _unique_id('T', base, used_ids)

        if teacher.get('id') != new_id:
            teacher['id'] = new_id
            teacher_id_changes += 1

    def _canonical_teacher_id(value):
        tid = str(value or '').strip()
        if not tid:
            return ''
        return canonical_by_old.get(tid, tid)

    reference_updates = 0

    for course in data.get('courses', []):
        if not isinstance(course, dict):
            continue
        original = course.get('teacherIds', [])
        source_ids = original if isinstance(original, list) else []

        seen = set()
        normalized = []
        for tid in source_ids:
            mapped = _canonical_teacher_id(tid)
            if mapped and mapped not in seen:
                seen.add(mapped)
                normalized.append(mapped)

        if normalized != original:
            course['teacherIds'] = normalized
            reference_updates += 1

    for unav in data.get('unavailability', []):
        if not isinstance(unav, dict):
            continue
        original_tid = str(unav.get('teacherId', '')).strip()
        mapped_tid = _canonical_teacher_id(original_tid)
        if mapped_tid and mapped_tid != original_tid:
            unav['teacherId'] = mapped_tid
            reference_updates += 1

    if isinstance(schedule, dict):
        assignments = schedule.get('assignments', [])
        if isinstance(assignments, list):
            teachers_by_id = {
                str(t.get('id', '')).strip(): t
                for t in teachers
                if isinstance(t, dict)
            }

            for assignment in assignments:
                if not isinstance(assignment, dict):
                    continue

                original = assignment.get('teacherIds', [])
                source_ids = original if isinstance(original, list) else []

                seen = set()
                normalized = []
                for tid in source_ids:
                    mapped = _canonical_teacher_id(tid)
                    if mapped and mapped not in seen:
                        seen.add(mapped)
                        normalized.append(mapped)

                if normalized != original:
                    assignment['teacherIds'] = normalized
                    assignment['teacherNames'] = [
                        teachers_by_id.get(tid, {}).get('name', tid) for tid in normalized
                    ]
                    reference_updates += 1

    return data, schedule, {
        'teacherIdsChanged': teacher_id_changes,
        'referenceUpdates': reference_updates,
    }


def _first_valid_email(*values):
    for val in values:
        email = str(val or '').strip()
        if not email or email.lower() == 'altro':
            continue
        if '@' in email:
            return email
    return ''


def _fetch_soup(url):
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')


def _normalize_course_name_for_match(value):
    text = str(value or '').strip().lower()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    text = re.sub(r'^\s*\d+\s*-\s*', '', text)
    text = re.sub(r'[^a-z0-9 ]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_mutuation_source(text):
    raw = str(text or '').strip()
    if not raw:
        return ''

    patterns = [
        r'mutuat[oa]\s+da\s+([^;|\n\r]+)',
        r'mutuat[oa]\s+con\s+([^;|\n\r]+)',
        r'in\s+comune\s+con\s+([^;|\n\r]+)',
    ]
    lowered = raw.lower()
    for pat in patterns:
        m = re.search(pat, lowered, flags=re.IGNORECASE)
        if not m:
            continue
        start, end = m.span(1)
        candidate = raw[start:end].strip(' .,:;')
        if candidate:
            return candidate
    return ''


def _scrape_mutuations_from_program(url):
    soup = _fetch_soup(url)
    out = []

    for row in soup.find_all('tr'):
        cols = row.find_all('td')
        if len(cols) < 2:
            continue
        course_name = cols[0].get_text(' ', strip=True)
        details = ' | '.join(td.get_text(' ', strip=True) for td in cols[1:])
        if 'mutuat' not in details.lower() and 'comune con' not in details.lower():
            continue
        source_name = _extract_mutuation_source(details)
        if not course_name or not source_name:
            continue
        out.append({
            'courseName': course_name,
            'sourceCourseName': source_name,
            'sourceUrl': url,
        })

    return {'mutuations': out}


def _find_best_course_id_by_name(name, courses):
    needle = _normalize_course_name_for_match(name)
    if not needle:
        return ''

    best_id = ''
    best_score = -1

    for course in courses:
        cid = str(course.get('id', '')).strip()
        cname = _normalize_course_name_for_match(course.get('name', ''))
        scode = _normalize_course_name_for_match(course.get('sourceCode', ''))
        if not cid or not cname:
            continue

        score = 0
        if needle == cname:
            score = 100
        elif scode and needle == scode:
            score = 95
        elif needle in cname or cname in needle:
            score = 70
        else:
            nt = set(needle.split())
            ct = set(cname.split())
            if nt and ct:
                inter = len(nt & ct)
                union = len(nt | ct)
                if union > 0:
                    score = int(60 * (inter / union))

        if score > best_score:
            best_score = score
            best_id = cid

    return best_id if best_score >= 55 else ''


def _scrape_docenti(url='https://web.dmi.unict.it/docenti'):
    soup = _fetch_soup(url)
    out = []
    for row in soup.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) < 1:
            continue
        out.append({
            'nome_docente': cols[0].get_text(strip=True),
            'ruolo': cols[1].get_text(strip=True) if len(cols) > 1 else '',
            'ssd': cols[2].get_text(strip=True) if len(cols) > 2 else '',
        })
    return {'docenti': out}


def _scrape_assegnisti(url='https://web.dmi.unict.it/it/assegnisti-di-ricerca'):
    soup = _fetch_soup(url)
    out = []
    for row in soup.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) >= 2:
            out.append({
                'nome': cols[0].get_text(strip=True),
                'email': cols[1].get_text(strip=True),
            })
    return {'assegnisti': out}


def _scrape_contrattisti(url='https://web.dmi.unict.it/elenchi/contrattisti-di-ricerca'):
    soup = _fetch_soup(url)
    out = []
    for row in soup.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) >= 2:
            out.append({
                'nome': cols[0].get_text(strip=True),
                'email': cols[1].get_text(strip=True),
            })
    return {'contrattisti_di_ricerca': out}


def _scrape_dottorandi(url='https://web.dmi.unict.it/dottorandi'):
    soup = _fetch_soup(url)
    out = []
    for row in soup.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) >= 3:
            out.append({
                'nome_dottorandi': cols[0].get_text(strip=True),
                'email': cols[1].get_text(strip=True),
                'ciclo': cols[2].get_text(strip=True),
            })
    return {'dottorandi': out}


def _scrape_personale_ta(url='https://web.dmi.unict.it/personale-ta'):
    soup = _fetch_soup(url)
    out = []
    for row in soup.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) >= 3:
            out.append({
                'nome_ta': cols[0].get_text(strip=True),
                'email': cols[1].get_text(strip=True),
                'telefono': cols[2].get_text(strip=True),
            })
    return {'docenti': out}


def _scrape_corsi_laurea(url='https://web.dmi.unict.it/it/content/didattica'):
    soup = _fetch_soup(url)
    out = []
    for item in soup.find_all('li'):
        text = item.get_text(strip=True)
        if 'CdL' not in text:
            continue
        nome_match = re.search(r'in (.*?) \(', text)
        classe_match = re.search(r'\((.*?)\)', text)
        if not (nome_match and classe_match):
            continue
        nome = nome_match.group(1).strip()
        classe = classe_match.group(1).strip()
        tipo = 'magistrale' if 'magistrale' in text.lower() else 'triennale'
        out.append({'nome': nome, 'classe': classe, 'tipo': tipo})
    return {'corsi_laurea': out}


def _scrape_insegnamenti(url):
    def _extract_cfu(cols):
        # Cerca prima pattern espliciti tipo "6 CFU" nelle colonne dopo il nome.
        trailing = ' | '.join(td.get_text(' ', strip=True) for td in cols[1:])
        m = re.search(r'\b(\d{1,2})\s*cfu\b', trailing, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))

        # Fallback: colonna che contiene solo un numero plausibile di CFU.
        for td in cols[1:]:
            txt = td.get_text(' ', strip=True)
            m_num = re.fullmatch(r'(\d{1,2})', txt)
            if not m_num:
                continue
            val = int(m_num.group(1))
            if 1 <= val <= 24:
                return val
        return 0

    soup = _fetch_soup(url)
    found = {}
    for row in soup.find_all('tr')[1:]:
        cols = row.find_all('td')
        if not cols:
            continue
        text = cols[0].get_text(strip=True)
        match = re.match(r'(\d+)\s*-\s*(.*)', text)
        if not match:
            continue
        code = match.group(1).strip()
        name = match.group(2).strip()
        cfu = _extract_cfu(cols)
        if code and name:
            prev = found.get(code)
            if not prev:
                found[code] = {'nome_insegnamento': name, 'cfu': cfu}
            else:
                # Mantieni il nome più lungo e il CFU non nullo (se trovato successivamente).
                if len(name) > len(prev.get('nome_insegnamento', '')):
                    prev['nome_insegnamento'] = name
                if cfu > 0:
                    prev['cfu'] = cfu

    out = []
    for code in sorted(found.keys()):
        info = found[code]
        out.append({
            'codice': code,
            'nome_insegnamento': info.get('nome_insegnamento', ''),
            'cfu': int(info.get('cfu', 0) or 0),
        })
    return {'insegnamenti': out}


def _scrape_curriculum_l31(url='https://web.dmi.unict.it/it/corsi/l-31/piani-di-studio'):
    soup = _fetch_soup(url)
    names = set()
    for header in soup.find_all(['h1', 'h2', 'h3', 'h4']):
        text = header.get_text(strip=True)
        if 'CURRICULUM' not in text.upper():
            continue
        match = re.search(r'["“](.*?)["”]', text)
        if match:
            names.add(match.group(1).strip())
    return {'curriculum': sorted(names)}


def _scrape_curriculum_lm18(url='https://web.dmi.unict.it/it/corsi/lm-18/piani-di-studio'):
    soup = _fetch_soup(url)
    names = set()
    for li in soup.find_all('li'):
        text = li.get_text(strip=True)
        if not text:
            continue
        if 'curriculum' in text.lower() or (' - ' in text and len(text) <= 120):
            names.add(text)
    return {'curriculum_lm18': sorted(names)}


def _merge_external_payload_into_db(data, payload, source_name=''):
    data = _ensure_db_shape(data)
    stats = {
        'rooms': 0, 'programs': 0, 'teachers': 0, 'courses': 0, 'curricula': 0,
        'added': 0, 'updated': 0, 'duplicates': 0, 'skipped': 0,
        'notes': [],
    }

    room_ids = {x.get('id', '') for x in data.get('rooms', [])}
    prog_ids = {x.get('id', '') for x in data.get('programs', [])}
    teach_ids = {x.get('id', '') for x in data.get('teachers', [])}
    curr_ids = {x.get('id', '') for x in data.get('curricula', [])}
    course_ids = {x.get('id', '') for x in data.get('courses', [])}

    def _note(kind, label):
        if label and len(stats['notes']) < 50:
            stats['notes'].append(f'{kind}: {label}')

    def _infer_program_id_from_source(name):
        n = str(name or '').lower()
        if 'l31' in n or 'l-31' in n:
            return 'L-31'
        if 'lm18' in n or 'lm-18' in n:
            return 'LM-18'
        if 'l35' in n or 'l-35' in n:
            return 'L-35'
        if 'lm40' in n or 'lm-40' in n:
            return 'LM-40'
        return ''

    def _upsert_teacher(name, email='', role='', phone='', cycle=''):
        clean = str(name or '').strip()
        if not clean:
            stats['skipped'] += 1
            _note('scartato docente', '(nome mancante)')
            return

        teachers = data.get('teachers', [])
        existing = next((t for t in teachers if str(t.get('name', '')).strip().lower() == clean.lower()), None)
        if existing:
            stats['duplicates'] += 1
            changed = False
            new_email = _first_valid_email(existing.get('email', ''), email)
            if new_email and new_email != existing.get('email', ''):
                existing['email'] = new_email
                changed = True
            if role and role != existing.get('role', ''):
                existing['role'] = role
                changed = True
            if phone and phone != existing.get('phone', ''):
                existing['phone'] = phone
                changed = True
            if cycle and cycle != existing.get('phdCycle', ''):
                existing['phdCycle'] = cycle
                changed = True
            if changed:
                stats['updated'] += 1
            _note('duplicato docente', clean)
            stats['teachers'] += 1
            return

        tid = _unique_id('T', clean, teach_ids)
        teachers.append({
            'id': tid,
            'name': clean,
            'email': _first_valid_email(email),
            'preferences': {'avoidEarly': False, 'avoidLate': False},
            'role': role,
            'phone': phone,
            'phdCycle': cycle,
        })
        stats['added'] += 1
        stats['teachers'] += 1

    if isinstance(payload.get('docenti'), list):
        for d in payload['docenti']:
            _upsert_teacher(
                d.get('nome_docente') or d.get('nome_ta'),
                d.get('email') or d.get('Email'),
                d.get('ruolo') or ('TA' if d.get('nome_ta') else 'Docente'),
                d.get('telefono', ''),
            )

    if isinstance(payload.get('assegnisti'), list):
        for a in payload['assegnisti']:
            _upsert_teacher(a.get('nome'), a.get('email') or a.get('Email'), 'Assegnista')

    if isinstance(payload.get('contrattisti_di_ricerca'), list):
        for c in payload['contrattisti_di_ricerca']:
            _upsert_teacher(c.get('nome'), c.get('email') or c.get('Email'), 'Contrattista di ricerca')

    if isinstance(payload.get('dottorandi'), list):
        for d in payload['dottorandi']:
            _upsert_teacher(
                d.get('nome_dottorandi') or d.get('nome'),
                d.get('email') or d.get('Email'),
                'Dottorando',
                cycle=d.get('ciclo', ''),
            )

    if isinstance(payload.get('corsi_laurea'), list):
        for c in payload['corsi_laurea']:
            class_code = str(c.get('classe', '')).strip().upper()
            name = str(c.get('nome', '')).strip()
            if not class_code:
                stats['skipped'] += 1
                _note('scartato cds', name or '(classe mancante)')
                continue

            item = {
                'id': class_code,
                'name': f'{name} ({class_code})' if name else class_code,
                'department': 'DMI',
                'type': str(c.get('tipo', '')).strip(),
                'classCode': class_code,
            }
            programs = data.get('programs', [])
            existing = next((p for p in programs if p.get('id') == class_code), None)
            if existing:
                stats['duplicates'] += 1
                existing.update({k: v for k, v in item.items() if v or k in ('id',)})
                stats['updated'] += 1
                _note('duplicato cds', class_code)
            else:
                programs.append(item)
                prog_ids.add(class_code)
                stats['added'] += 1
            stats['programs'] += 1

    curricula_candidates = []
    if isinstance(payload.get('curriculum'), list):
        curricula_candidates.extend([(x, 'L-31') for x in payload['curriculum']])
    if isinstance(payload.get('curriculum_lm18'), list):
        curricula_candidates.extend([(x, 'LM-18') for x in payload['curriculum_lm18']])

    for curr_name, fallback_program in curricula_candidates:
        name = str(curr_name or '').strip()
        if not name:
            stats['skipped'] += 1
            _note('scartato curriculum', '(nome mancante)')
            continue

        program_id = _infer_program_id_from_source(source_name) or fallback_program
        if program_id and not any(p.get('id') == program_id for p in data.get('programs', [])):
            data['programs'].append({'id': program_id, 'name': program_id, 'department': 'DMI'})
            prog_ids.add(program_id)
            stats['added'] += 1

        existing = next((c for c in data.get('curricula', [])
                         if str(c.get('name', '')).strip().lower() == name.lower()
                         and (not program_id or c.get('programId', '') == program_id)), None)
        if existing:
            stats['duplicates'] += 1
            _note('duplicato curriculum', name)
        else:
            cid = _unique_id('CUR', f'{program_id}-{name}', curr_ids)
            data['curricula'].append({
                'id': cid,
                'programId': program_id,
                'name': name,
                'yearCohort': '',
            })
            stats['added'] += 1
        stats['curricula'] += 1

    if isinstance(payload.get('insegnamenti'), list):
        inferred_program = _infer_program_id_from_source(source_name)
        if inferred_program and not any(p.get('id') == inferred_program for p in data.get('programs', [])):
            data['programs'].append({'id': inferred_program, 'name': inferred_program, 'department': 'DMI'})
            prog_ids.add(inferred_program)
            stats['added'] += 1

        for ins in payload['insegnamenti']:
            code = str(ins.get('codice', '')).strip()
            name = str(ins.get('nome_insegnamento', '')).strip()
            cfu = int(ins.get('cfu', 0) or 0)
            if not code and not name:
                stats['skipped'] += 1
                _note('scartato insegnamento', '(codice e nome mancanti)')
                continue

            courses = data.get('courses', [])
            existing = next((c for c in courses
                             if (code and str(c.get('sourceCode', '')).strip() == code)
                             or (name and str(c.get('name', '')).strip().lower().endswith(name.lower()))), None)

            if existing:
                stats['duplicates'] += 1
                changed = False
                if code and existing.get('sourceCode') != code:
                    existing['sourceCode'] = code
                    changed = True
                target_name = f'{code} - {name}' if code and name else (name or code)
                if target_name and existing.get('name') != target_name:
                    existing['name'] = target_name
                    changed = True
                if inferred_program and not existing.get('programId'):
                    existing['programId'] = inferred_program
                    changed = True
                if not isinstance(existing.get('teacherIds'), list):
                    existing['teacherIds'] = []
                    changed = True
                if not isinstance(existing.get('curriculaIds'), list):
                    existing['curriculaIds'] = []
                    changed = True
                if not isinstance(existing.get('weeklyEvents'), list) or not existing.get('weeklyEvents'):
                    existing['weeklyEvents'] = [{'durationHours': 2}]
                    changed = True
                if cfu > 0 and int(existing.get('cfu', 0) or 0) != cfu:
                    existing['cfu'] = cfu
                    changed = True
                if changed:
                    stats['updated'] += 1
                _note('duplicato insegnamento', code or name)
            else:
                if code:
                    cid = code
                    if cid in course_ids:
                        cid = _unique_id('C', f'{code}-{name}', course_ids)
                    else:
                        course_ids.add(cid)
                else:
                    cid = _unique_id('C', name, course_ids)
                courses.append({
                    'id': cid,
                    'sourceCode': code,
                    'name': f'{code} - {name}' if code and name else (name or code),
                    'semester': 1,
                    'year': 1,
                    'programId': inferred_program,
                    'curriculaIds': [],
                    'teacherIds': [],
                    'expectedStudents': 0,
                    'cfu': cfu,
                    'weeklyHours': 2,
                    'preferredSlotHours': 2,
                    'mutuationGroup': '',
                    'roomType': 'lecture',
                    'weeklyEvents': [{'durationHours': 2}],
                })
                stats['added'] += 1
            stats['courses'] += 1

    if isinstance(payload.get('mutuations'), list):
        courses = data.get('courses', [])
        parent = {}

        def _find(x):
            parent.setdefault(x, x)
            if parent[x] != x:
                parent[x] = _find(parent[x])
            return parent[x]

        def _union(a, b):
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[rb] = ra

        linked = 0
        for row in payload.get('mutuations', []):
            target_name = row.get('courseName', '')
            source_name_mut = row.get('sourceCourseName', '')
            target_id = _find_best_course_id_by_name(target_name, courses)
            source_id = _find_best_course_id_by_name(source_name_mut, courses)

            if not target_id or not source_id:
                stats['skipped'] += 1
                _note('mutuazione non agganciata', f"{target_name} -> {source_name_mut}")
                continue

            _union(target_id, source_id)
            linked += 1

        groups = defaultdict(list)
        for c in courses:
            cid = c.get('id', '')
            if not cid or cid not in parent:
                continue
            groups[_find(cid)].append(c)

        for root, members in groups.items():
            if len(members) < 2:
                continue
            canonical = _normalize_for_id(members[0].get('name', root)) or _normalize_for_id(root) or 'mut'
            gid = f'MUT-{canonical}'.upper()[:36]
            for c in members:
                if c.get('mutuationGroup') != gid:
                    c['mutuationGroup'] = gid
                    stats['updated'] += 1
            stats['courses'] += len(members)

        if linked == 0:
            _note('mutuazioni', 'nessun legame applicato ai corsi locali')
        else:
            _note('mutuazioni', f'legami applicati: {linked}')

    return data, stats


def _scrape_payloads_for_section(section):
    if section == 'teachers':
        return [
            ('docenti', _scrape_docenti()),
            ('assegnisti', _scrape_assegnisti()),
            ('contrattisti', _scrape_contrattisti()),
            ('dottorandi', _scrape_dottorandi()),
            ('personale-ta', _scrape_personale_ta()),
        ]
    if section == 'programs':
        return [('corsi-laurea', _scrape_corsi_laurea())]
    if section == 'courses':
        return [
            ('insegnamenti_l31', _scrape_insegnamenti('https://web.dmi.unict.it/corsi/l-31/programmi')),
            ('insegnamenti_lm18', _scrape_insegnamenti('https://web.dmi.unict.it/corsi/lm-18/programmi')),
            ('insegnamenti_l35', _scrape_insegnamenti('https://web.dmi.unict.it/corsi/l-35/programmi')),
            ('insegnamenti_lm40', _scrape_insegnamenti('https://web.dmi.unict.it/corsi/lm-40/programmi')),
        ]
    if section == 'mutuations':
        return [
            ('mutuazioni_l31', _scrape_mutuations_from_program('https://web.dmi.unict.it/corsi/l-31/programmi')),
            ('mutuazioni_lm18', _scrape_mutuations_from_program('https://web.dmi.unict.it/corsi/lm-18/programmi')),
            ('mutuazioni_l35', _scrape_mutuations_from_program('https://web.dmi.unict.it/corsi/l-35/programmi')),
            ('mutuazioni_lm40', _scrape_mutuations_from_program('https://web.dmi.unict.it/corsi/lm-40/programmi')),
        ]
    if section == 'curricula':
        return [
            ('curriculum_l31', _scrape_curriculum_l31()),
            ('curriculum_lm18', _scrape_curriculum_lm18()),
        ]
    raise ValueError(f'Sezione non supportata per import da URL: {section}')

    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/admin', methods=['GET'])
def admin_page():
    load_users()  # bootstrap utente admin di default se mancante
    if not is_admin_logged():
        return render_template('admin_login.html')
    return render_template('admin.html')


@app.route('/admin/login', methods=['POST'])
def admin_login():
    username = _normalize_username(request.form.get('username', ''))
    password = request.form.get('password', '')

    users = load_users()
    user = _find_user(users, username)
    if user and user.get('isActive', True) and check_password_hash(user.get('passwordHash', ''), password):
        session['username'] = user.get('username', '')
        session['role'] = user.get('role', 'admin')
        session['is_admin'] = user.get('role') == 'admin'  # retro-compatibilita
        user['lastLoginAt'] = _utc_now_iso()
        save_users(users)
        return redirect(url_for('admin_page'))
    return render_template('admin_login.html', error='Credenziali non valide.')


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return redirect(url_for('admin_page'))


@app.route('/api/users', methods=['GET'])
@admin_required
def api_users_get():
    users = load_users()
    return jsonify({'users': [_public_user(u) for u in users]})


@app.route('/api/users', methods=['POST'])
@admin_required
def api_users_create():
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'Payload non valido.'}), 400

    username = _normalize_username(body.get('username', ''))
    password = str(body.get('password', ''))

    if not username or len(username) < 3:
        return jsonify({'error': 'Username non valido (minimo 3 caratteri).'}), 400
    if any(ch not in 'abcdefghijklmnopqrstuvwxyz0123456789._-' for ch in username):
        return jsonify({'error': 'Username contiene caratteri non consentiti.'}), 400

    ok_pwd, msg_pwd = _validate_password_strength(password)
    if not ok_pwd:
        return jsonify({'error': msg_pwd}), 400

    users = load_users()
    if _find_user(users, username):
        return jsonify({'error': 'Username gia esistente.'}), 409

    new_user = {
        'username': username,
        'passwordHash': _hash_password(password),
        'role': 'admin',
        'isActive': True,
        'createdAt': _utc_now_iso(),
        'lastLoginAt': '',
    }
    users.append(new_user)
    save_users(users)
    return jsonify({'ok': True, 'user': _public_user(new_user)})


@app.route('/api/users/<username>/password', methods=['POST'])
@admin_required
def api_users_reset_password(username):
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'Payload non valido.'}), 400

    new_password = str(body.get('password', ''))
    ok_pwd, msg_pwd = _validate_password_strength(new_password)
    if not ok_pwd:
        return jsonify({'error': msg_pwd}), 400

    users = load_users()
    user = _find_user(users, username)
    if not user:
        return jsonify({'error': 'Utente non trovato.'}), 404

    user['passwordHash'] = _hash_password(new_password)
    save_users(users)
    return jsonify({'ok': True})


@app.route('/api/users/<username>', methods=['DELETE'])
@admin_required
def api_users_delete(username):
    target = _normalize_username(username)
    current = _normalize_username(session.get('username', ''))
    if target == current:
        return jsonify({'error': 'Non puoi eliminare il tuo utente mentre sei loggato.'}), 400

    users = load_users()
    idx = -1
    for i, u in enumerate(users):
        if _normalize_username(u.get('username', '')) == target:
            idx = i
            break

    if idx < 0:
        return jsonify({'error': 'Utente non trovato.'}), 404

    deleting = users[idx]
    if deleting.get('role') == 'admin' and _active_admin_count(users) <= 1:
        return jsonify({'error': 'Deve esistere almeno un amministratore attivo.'}), 400

    users.pop(idx)
    save_users(users)
    return jsonify({'ok': True})


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
    data = _ensure_db_shape(data)
    schedule = load_schedule()
    data, schedule, _ = _ensure_unique_teacher_ids(data, schedule)
    save_db(data)
    if isinstance(schedule, dict):
        save_schedule(schedule)
    return jsonify({'ok': True, 'message': 'Database salvato.'})


@app.route('/api/import/url', methods=['POST'])
@admin_required
def import_from_url():
    body = request.get_json(silent=True) or {}
    section = str(body.get('section', '')).strip().lower()
    if not section:
        return jsonify({'error': 'Sezione mancante.'}), 400

    try:
        payloads = _scrape_payloads_for_section(section)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except requests.RequestException as e:
        return jsonify({'error': f'Errore di rete durante lo scraping: {str(e)}'}), 502

    db = _ensure_db_shape(load_db())
    merged = {
        'rooms': 0,
        'programs': 0,
        'teachers': 0,
        'courses': 0,
        'curricula': 0,
        'added': 0,
        'updated': 0,
        'duplicates': 0,
        'skipped': 0,
        'notes': [],
    }

    try:
        for source_name, payload in payloads:
            db, stats = _merge_external_payload_into_db(db, payload, source_name=source_name)
            for key in ('rooms', 'programs', 'teachers', 'courses', 'curricula',
                        'added', 'updated', 'duplicates', 'skipped'):
                merged[key] += int(stats.get(key, 0))
            for note in stats.get('notes', []):
                if len(merged['notes']) >= 80:
                    break
                merged['notes'].append(f'{source_name}: {note}')
    except requests.RequestException as e:
        return jsonify({'error': f'Errore di rete durante lo scraping: {str(e)}'}), 502
    except Exception as e:
        return jsonify({'error': f'Errore durante import da URL: {str(e)}'}), 500

    db, _, _ = _ensure_unique_teacher_ids(db)
    save_db(db)
    return jsonify({
        'ok': True,
        'section': section,
        'summary': merged,
        'message': 'Import da URL completato.',
    })


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
    result = _solve_with_infeasible_fallback(db, time_limit, requested_algorithm)
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


@app.route('/api/schedule/program-year', methods=['POST'])
@admin_required
def generate_schedule_program_year():
    """Genera orario solo per un CdL e anno di studio, preservando il resto.

    - Rigenera da zero il blocco target (CdL+anno), sovrascrivendo il precedente.
    - Mantiene le assegnazioni non target come blocchi di prenotazione aula.
    """
    db = load_db()
    body = request.get_json(silent=True) or {}

    program_id = str(body.get('programId', '')).strip()
    raw_year = body.get('year')
    requested_algorithm = (body.get('algorithm', 'auto') or 'auto').strip().lower()
    time_limit = body.get('timeLimitSeconds', 30)

    try:
        year = int(raw_year)
    except (TypeError, ValueError):
        return jsonify({'error': 'Anno non valido. Seleziona un anno di studio numerico.'}), 400

    if not program_id:
        return jsonify({'error': 'Programma non valido. Seleziona un corso di laurea.'}), 400

    courses = db.get('courses', [])
    target_courses = [
        c for c in courses
        if c.get('programId') == program_id and _course_study_year(c) == year
    ]
    if not target_courses:
        return jsonify({
            'error': (
                f'Nessun insegnamento trovato per CdL {program_id} anno {year}. '
                'Verifica che i corsi abbiano il campo year/studyYear/anno.'
            )
        }), 400

    target_course_ids = {c.get('id', '') for c in target_courses}

    # Assegnazioni precedenti: teniamo fisse quelle fuori target.
    previous = load_schedule() or {}
    previous_assignments = previous.get('assignments', []) if isinstance(previous, dict) else []
    kept_assignments = [
        a for a in previous_assignments
        if a.get('courseId', '') not in target_course_ids and a.get('day') not in ('N/A', None)
    ]

    # DB ridotto al blocco target per il solver.
    active_teacher_ids = set()
    active_curricula_ids = set()
    for c in target_courses:
        active_teacher_ids.update(c.get('teacherIds', []))
        active_curricula_ids.update(c.get('curriculaIds', []))

    db_subset = json.loads(json.dumps(db))
    db_subset['courses'] = target_courses
    db_subset['teachers'] = [t for t in db.get('teachers', []) if t.get('id', '') in active_teacher_ids]
    db_subset['curricula'] = [c for c in db.get('curricula', []) if c.get('id', '') in active_curricula_ids]
    db_subset['unavailability'] = [
        u for u in db.get('unavailability', [])
        if u.get('teacherId', '') in active_teacher_ids
    ]
    db_subset['roomUnavailability'] = _build_room_reservations_from_assignments(kept_assignments)

    t0 = time.time()
    result_new = _solve_with_infeasible_fallback(db_subset, time_limit, requested_algorithm)

    merged_assignments = kept_assignments + (result_new.get('assignments', []) or [])

    result = {
        **result_new,
        'assignments': merged_assignments,
        'solveTimeSeconds': round(time.time() - t0, 2),
        'requestedAlgorithm': requested_algorithm,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'generationMode': 'program-year',
        'targetProgramId': program_id,
        'targetYear': year,
        'keptAssignments': len(kept_assignments),
        'regeneratedAssignments': len(result_new.get('assignments', []) or []),
        'solverBackend': result_new.get('algorithmLabel') or (
            'Google OR-Tools CP-SAT' if scheduler.HAS_ORTOOLS else 'Greedy Heuristic Fallback'
        ),
    }

    # Ricalcola report hard sull'orario complessivo finale.
    result['hardConstraintReport'] = scheduler._validate_hard_constraints(merged_assignments, db)
    result['softConstraintReport'] = scheduler._evaluate_soft_constraints(merged_assignments, db)
    if result.get('status') not in ('error', 'infeasible'):
        result['message'] = (
            f"Rigenerato da zero il blocco {program_id} anno {year}. "
            f"Mantenute {len(kept_assignments)} assegnazioni esistenti fuori target."
        )

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

    hard_report = normalized.get('hardConstraintReport', {})
    violated = [
        c for c in hard_report.get('checks', [])
        if int(c.get('violations', 0) or 0) > 0
    ]
    if violated:
        return jsonify({
            'error': 'Modifica manuale rifiutata: violazione vincoli hard.',
            'violatedConstraints': violated,
            'hardConstraintReport': hard_report,
        }), 400

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
    programs_by_id = {p['id']: p for p in db.get('programs', [])}

    curriculum_tables = []
    for curriculum in db.get('curricula', []):
        cid = curriculum.get('id', '')
        rows = []
        for a in schedule.get('assignments', []):
            if a.get('day') == 'N/A':
                continue
            course = courses_by_id.get(a.get('courseId', ''), {})
            if cid in course.get('curriculaIds', []):
                row = dict(a)
                program_id = row.get('programId') or course.get('programId', '')
                row['programId'] = program_id
                row['programName'] = row.get('programName') or programs_by_id.get(program_id, {}).get('name', program_id)
                row['studyYear'] = _course_study_year(course)
                row['mutuationGroup'] = _course_mutuation_group(course)
                rows.append(row)
        rows.sort(key=lambda x: (x.get('day', ''), x.get('startHour', 0), x.get('courseName', '')))
        curriculum_program_id = curriculum.get('programId', '')
        curriculum_program_name = programs_by_id.get(curriculum_program_id, {}).get('name', curriculum_program_id)
        if not curriculum_program_name and rows:
            curriculum_program_name = rows[0].get('programName', '')
            curriculum_program_id = rows[0].get('programId', curriculum_program_id)

        curriculum_tables.append({
            'curriculumId': cid,
            'curriculumName': curriculum.get('name', cid),
            'yearCohort': curriculum.get('yearCohort', ''),
            'programId': curriculum_program_id,
            'programName': curriculum_program_name,
            'rows': rows,
        })

    assignments_by_course = {}
    for a in schedule.get('assignments', []):
        cid = a.get('courseId', '')
        if cid and cid not in assignments_by_course:
            assignments_by_course[cid] = a

    mut_group_members = {}
    for course in db.get('courses', []):
        mg = _course_mutuation_group(course)
        if not mg:
            continue
        mut_group_members.setdefault(mg, []).append(course)

    mutuation_view = []
    for mg, members in mut_group_members.items():
        entry = {
            'groupId': mg,
            'label': mg,
            'courses': [],
            'assignment': None,
        }
        for c in members:
            course_program_id = c.get('programId', '')
            entry['courses'].append({
                'courseId': c.get('id', ''),
                'courseName': c.get('name', ''),
                'programId': course_program_id,
                'programName': programs_by_id.get(course_program_id, {}).get('name', course_program_id),
                'studyYear': _course_study_year(c),
                'semester': c.get('semester'),
            })
            if not entry['assignment']:
                a = assignments_by_course.get(c.get('id', ''))
                if a and a.get('day') != 'N/A':
                    entry['assignment'] = {
                        'day': a.get('day'),
                        'dayIt': a.get('dayIt') or scheduler.DAY_NAMES_IT.get(a.get('day', ''), a.get('day', '')),
                        'startHour': a.get('startHour'),
                        'endHour': a.get('endHour'),
                        'roomName': a.get('roomName') or a.get('roomId', ''),
                    }
        mutuation_view.append(entry)
    mutuation_view.sort(key=lambda x: x.get('groupId', ''))

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
        'coursesById': {
            cid: {
                'id': cid,
                'name': c.get('name', ''),
                'programId': c.get('programId', ''),
                'programName': programs_by_id.get(c.get('programId', ''), {}).get('name', c.get('programId', '')),
                'studyYear': _course_study_year(c),
                'semester': c.get('semester'),
                'teacherIds': c.get('teacherIds', []),
                'roomType': c.get('roomType', ''),
                'cfu': c.get('cfu', 0),
                'weeklyHours': c.get('weeklyHours', 0),
                'preferredSlotHours': c.get('preferredSlotHours', 0),
                'mutuationGroup': _course_mutuation_group(c),
            }
            for cid, c in courses_by_id.items()
        },
        'mutuationView': mutuation_view,
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
            'year': c.get('year', c.get('studyYear', c.get('anno'))),
            'expectedStudents': c.get('expectedStudents', 0),
            'cfu': c.get('cfu', 0),
            'weeklyHours': c.get('weeklyHours', 0),
            'preferredSlotHours': c.get('preferredSlotHours', 0),
            'mutuationGroup': c.get('mutuationGroup', c.get('sharedWithCourseId', '')),
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
    load_users()
    print(f"╔════════════════════════════════════════════════════╗")
    print(f"║  University Timetabling System                    ║")
    print(f"║  Server: http://127.0.0.1:5000                   ║")
    print(f"║  Solver: {'OR-Tools CP-SAT' if scheduler.HAS_ORTOOLS else 'Greedy (installa ortools per CP-SAT)':40s} ║")
    print(f"║  Default admin user: {DEFAULT_ADMIN_USERNAME:28s} ║")
    print(f"║  Override env vars: DEFAULT_ADMIN_USERNAME/PASSWORD ║")
    print(f"║  Data:   {str(DATA_DIR):40s}   ║")
    print(f"╚════════════════════════════════════════════════════╝")
    app.run(debug=True, host='0.0.0.0', port=5000)
