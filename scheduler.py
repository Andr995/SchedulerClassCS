"""
University Timetable Scheduler
===============================
Motore di scheduling per la generazione automatica dell'orario delle lezioni.

Utilizza Google OR-Tools CP-SAT solver per ottimizzazione vincolata.
Fallback su algoritmo greedy euristico se OR-Tools non è disponibile.

Vincoli HARD (obbligatori):
  - Nessun docente può insegnare in due aule contemporaneamente
  - Nessuna aula può ospitare due lezioni contemporaneamente
  - Nessun gruppo curricolare può avere lezioni simultanee (studenti)
  - Capacità aula >= studenti attesi
  - Tipo aula deve corrispondere al tipo richiesto dal corso
  - Rispetto indisponibilità docenti
  - Pausa pranzo 13:00-14:00 (nessuna lezione)
  - Eventi dello stesso corso su giorni diversi
  - Le lezioni devono rientrare nella finestra giornaliera

Vincoli SOFT (ottimizzati):
  - Minimizzare buchi tra lezioni dello stesso curriculum/giorno
  - Rispettare preferenze docenti (evita 8:00, evita tardi)
  - Penalizzare lezioni troppo presto o troppo tardi
  - Penalizzare ore consecutive eccessive per docente
"""

import json
import random
from collections import defaultdict

try:
    from ortools.sat.python import cp_model
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
DAY_NAMES_IT = {'Mon': 'Lunedì', 'Tue': 'Martedì', 'Wed': 'Mercoledì',
                'Thu': 'Giovedì', 'Fri': 'Venerdì'}
DAY_INDEX = {d: i for i, d in enumerate(DAYS)}
FLAT_MUL = 100  # fattore moltiplicativo per tempo "flat" (giorno*100+ora)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def solve(data, time_limit_s=30, algorithm='auto'):
    """Genera l'orario ottimale.

    Args:
        data: dizionario con chiavi meta, rooms, teachers, programs,
              curricula, courses, unavailability, softPolicy.
        time_limit_s: tempo massimo in secondi per il solver.
          algorithm: 'auto', 'cp-sat' oppure 'greedy'.

    Returns:
        dict con 'assignments' (lista di assegnazioni), 'status', 'objective'.
    """
    raw_alg = (algorithm or 'auto').strip().lower()
    aliases = {
        'auto': 'auto',
        'cp-sat': 'cp-sat',
        'constraint-programming': 'cp-sat',
        'constraint_programming': 'cp-sat',
        'constraints': 'cp-sat',
        'cp': 'cp-sat',
        'greedy': 'greedy',
        'genetic': 'genetic',
        'genetic-algorithm': 'genetic',
        'genetic_algorithm': 'genetic',
        'tabu': 'tabu',
        'tabu-search': 'tabu',
        'tabu_search': 'tabu',
        'linear': 'linear',
        'linear-programming': 'linear',
        'linear_programming': 'linear',
        'lp': 'linear',
        'mip': 'linear',
    }
    alg = aliases.get(raw_alg)

    if alg is None:
        return {
            'assignments': [],
            'status': 'error',
            'objective': None,
            'algorithm': 'unknown',
            'algorithmLabel': 'Unknown',
            'message': f"Algoritmo non supportato: {algorithm}",
        }

    if alg == 'greedy':
        return _solve_greedy(data)

    if alg == 'genetic':
        return _solve_genetic(data, time_limit_s=time_limit_s)

    if alg == 'tabu':
        return _solve_tabu(data, time_limit_s=time_limit_s)

    if alg == 'linear':
        return _solve_linear(data, time_limit_s=time_limit_s)

    if alg == 'cp-sat':
        if not HAS_ORTOOLS:
            return {
                'assignments': [],
                'status': 'error',
                'objective': None,
                'algorithm': 'CP-SAT',
                'algorithmLabel': 'Google OR-Tools CP-SAT',
                'message': 'CP-SAT richiesto ma OR-Tools non e disponibile. Installa ortools o usa greedy.',
            }
        return _solve_cpsat(data, time_limit_s)

    # auto
    if HAS_ORTOOLS:
        return _solve_cpsat(data, time_limit_s)
    return _solve_greedy(data)


def _hard_violations_count(result):
    report = result.get('hardConstraintReport', {}) if isinstance(result, dict) else {}
    checks = report.get('checks', []) if isinstance(report, dict) else []
    return sum(int(c.get('violations', 0)) for c in checks)


def _result_rank(result):
    assignments = result.get('assignments', []) if isinstance(result, dict) else []
    unplaced = sum(1 for a in assignments if a.get('day') == 'N/A' or a.get('error'))
    hard_viol = _hard_violations_count(result)
    obj = result.get('objective')
    obj_val = obj if isinstance(obj, (int, float)) else 0
    return (unplaced, hard_viol, obj_val)


def _perturb_data_for_metaheuristic(data, seed):
    """Copia/mescola i dati per creare candidati diversi in metaeuristiche."""
    rnd = random.Random(seed)
    d = json.loads(json.dumps(data))
    rnd.shuffle(d['courses'])
    rnd.shuffle(d['rooms'])
    for course in d.get('courses', []):
        if 'weeklyEvents' in course and isinstance(course['weeklyEvents'], list):
            rnd.shuffle(course['weeklyEvents'])
    return d


def _solve_genetic(data, time_limit_s=30):
    """Approccio genetic-style: multi-start evolutivo su base greedy."""
    max_seconds = max(2, int(time_limit_s))
    population = 6
    generations = max(2, min(10, max_seconds // 2))

    seeds = [101 + i for i in range(population)]
    best = None
    best_rank = (10**9, 10**9, 10**9)

    for g in range(generations):
        evaluated = []
        for s in seeds:
            cand_data = _perturb_data_for_metaheuristic(data, seed=s + g * 997)
            cand = _solve_greedy(cand_data)
            rank = _result_rank(cand)
            evaluated.append((rank, s, cand))
            if rank < best_rank:
                best_rank = rank
                best = cand

        evaluated.sort(key=lambda x: x[0])
        elites = [evaluated[0][1], evaluated[1][1]] if len(evaluated) > 1 else [evaluated[0][1]]

        # Evoluzione semplice: mantieni elite e genera mutazioni dei seed migliori.
        new_seeds = list(elites)
        rnd = random.Random(7000 + g)
        while len(new_seeds) < population:
            parent = rnd.choice(elites)
            child = parent + rnd.randint(-50, 50) + g * 13
            new_seeds.append(child)
        seeds = new_seeds

    if best is None:
        best = _solve_greedy(data)
        best_rank = _result_rank(best)

    best['algorithm'] = 'Genetic'
    best['algorithmLabel'] = 'Genetic Algorithm (Metaheuristic)'
    best['message'] = (
        best.get('message', '') +
        f' | Ricerca genetica completata: {generations} generazioni, popolazione {population}, rank={best_rank}.'
    ).strip()
    return best


def _solve_tabu(data, time_limit_s=30):
    """Tabu search light: esplora vicinato di perturbazioni evitando seed tabu."""
    max_seconds = max(2, int(time_limit_s))
    iterations = max(5, min(40, max_seconds * 2))
    neighborhood = 5

    best = _solve_greedy(data)
    best_rank = _result_rank(best)
    current_seed = 300
    tabu_queue = []
    tabu_set = set()
    tabu_size = 12

    for it in range(iterations):
        local_best = None
        local_rank = (10**9, 10**9, 10**9)
        local_seed = None

        for n in range(neighborhood):
            seed = current_seed + it * 41 + n * 7
            if seed in tabu_set:
                continue
            cand_data = _perturb_data_for_metaheuristic(data, seed=seed)
            cand = _solve_greedy(cand_data)
            rank = _result_rank(cand)
            if rank < local_rank:
                local_rank = rank
                local_best = cand
                local_seed = seed

        if local_best is None:
            continue

        current_seed = local_seed
        tabu_queue.append(local_seed)
        tabu_set.add(local_seed)
        if len(tabu_queue) > tabu_size:
            old = tabu_queue.pop(0)
            tabu_set.discard(old)

        if local_rank < best_rank:
            best = local_best
            best_rank = local_rank

    best['algorithm'] = 'Tabu'
    best['algorithmLabel'] = 'Tabu Search (Metaheuristic)'
    best['message'] = (
        best.get('message', '') +
        f' | Tabu search completata: {iterations} iterazioni, vicinato {neighborhood}, rank={best_rank}.'
    ).strip()
    return best


def _solve_linear(data, time_limit_s=30):
    """Programmazione lineare intera: usa CP-SAT come backend lineare intero."""
    if HAS_ORTOOLS:
        result = _solve_cpsat(data, time_limit_s=time_limit_s)
        result['algorithm'] = 'Linear'
        result['algorithmLabel'] = 'Linear Integer Programming (via OR-Tools CP-SAT)'
        result['message'] = (
            result.get('message', '') +
            ' | Modello risolto come programma lineare intero con vincoli interi.'
        ).strip()
        return result

    result = _solve_greedy(data)
    result['algorithm'] = 'Linear'
    result['algorithmLabel'] = 'Linear Programming Requested (fallback Greedy)'
    result['message'] = (
        result.get('message', '') +
        ' | OR-Tools non disponibile: fallback su greedy.'
    ).strip()
    return result


# ---------------------------------------------------------------------------
# Raccolta eventi
# ---------------------------------------------------------------------------
def _collect_events(data):
    """Estrae tutti gli eventi schedulabili dai corsi."""
    events = []
    teachers_by_id = {t['id']: t for t in data.get('teachers', [])}

    def _event_duration_hours(evt):
        """Compatibilita: accetta sia durationHours (nuovo) sia duration (legacy)."""
        raw = evt.get('durationHours', evt.get('duration', 1))
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 1

    for course in data.get('courses', []):
        for order_idx, evt in enumerate(course.get('weeklyEvents', [])):
            teacher_prefs = {}
            for tid in course.get('teacherIds', []):
                t = teachers_by_id.get(tid, {})
                prefs = t.get('preferences', {})
                teacher_prefs[tid] = prefs

            events.append({
                'idx': len(events),
                'eventId': evt.get('id', f"E-{len(events)}"),
                'eventOrder': order_idx,
                'courseId': course['id'],
                'courseName': course.get('name', ''),
                'duration': _event_duration_hours(evt),
                'teacherIds': course.get('teacherIds', []),
                'curriculaIds': course.get('curriculaIds', []),
                'programId': course.get('programId', ''),
                'studyYear': course.get('year', course.get('studyYear', course.get('anno'))),
                'mutuationGroup': str(course.get('mutuationGroup') or course.get('sharedWithCourseId') or '').strip(),
                'expectedStudents': course.get('expectedStudents', 0),
                'roomType': course.get('roomType', 'lecture'),
                'teacherPrefs': teacher_prefs,
                'preferredSlotHours': int(course.get('preferredSlotHours', 0) or 0),
            })
    return events


def _valid_starts(duration, ds, de, ls, le):
    """Restituisce le ore di inizio valide per un evento di durata data,
    escludendo lezioni che sovrappongono la pausa pranzo."""
    starts = []
    for h in range(ds, de):
        end = h + duration
        if end > de:
            break
        # Controlla sovrapposizione con pranzo [ls, le)
        if h < le and end > ls:
            continue
        starts.append(h)
    return starts


def _parse_day_pattern(pattern):
    """Converte pattern come 'Mon-Wed' in tuple ordinata di indici giorno."""
    if not isinstance(pattern, str):
        return None
    parts = [p.strip() for p in pattern.split('-') if p.strip()]
    if not parts:
        return None
    try:
        day_idx = [DAY_INDEX[p] for p in parts]
    except KeyError:
        return None
    if len(set(day_idx)) != len(day_idx):
        return None
    return tuple(sorted(day_idx))


def _course_allowed_patterns(course, preferred_patterns):
    """Restituisce i pattern ammessi per un corso (tuple di day index ordinati)."""
    weekly_events = course.get('weeklyEvents', [])
    n_events = len(weekly_events)
    if n_events < 2:
        return []

    raw_patterns = []
    explicit = (course.get('patternPref') or '').strip()
    if explicit:
        raw_patterns = [explicit]
    elif n_events == 2:
        raw_patterns = preferred_patterns.get('twoEvents', [])
    elif n_events == 3:
        raw_patterns = preferred_patterns.get('threeEvents', [])

    out = []
    for p in raw_patterns:
        parsed = _parse_day_pattern(p)
        if parsed and len(parsed) == n_events and parsed not in out:
            out.append(parsed)
    return out


# ---------------------------------------------------------------------------
# Colori per i corsi (usati anche lato client, ma calcolati qui per coerenza)
# ---------------------------------------------------------------------------
def _course_color(course_id, alpha=0.7):
    """Genera un colore HSL deterministico dato l'ID del corso."""
    h = 0
    for ch in course_id:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hue = h % 360
    return f"hsla({hue}, 65%, 55%, {alpha})"


def _event_overlaps_lunch(start_hour, end_hour, lunch_start, lunch_end):
    return start_hour < lunch_end and end_hour > lunch_start


def _validate_hard_constraints(assignments, data):
    """Valida i principali vincoli hard sul risultato prodotto."""
    tm = data.get('meta', {}).get('timeModel', {})
    ds = tm.get('dayStart', 8)
    de = tm.get('dayEnd', 19)
    ls = tm.get('lunchStart', 13)
    le = tm.get('lunchEnd', 14)

    rooms_by_id = {r['id']: r for r in data.get('rooms', [])}
    courses_by_id = {c['id']: c for c in data.get('courses', [])}
    unav_by_teacher = defaultdict(set)
    for u in data.get('unavailability', []):
        day = u.get('day', '')
        for h in u.get('hours', []):
            unav_by_teacher[u.get('teacherId', '')].add((day, h))

    room_unav_by_room = defaultdict(set)
    for ru in data.get('roomUnavailability', []):
        day = ru.get('day', '')
        for h in ru.get('hours', []):
            room_unav_by_room[ru.get('roomId', '')].add((day, h))

    placed = [a for a in assignments if a.get('day') and a.get('day') != 'N/A']

    teacher_slot_owner = {}
    room_slot_owner = {}
    curr_slot_owner = {}
    by_course_days = defaultdict(set)

    violations = {
        'teacherOverlap': 0,
        'roomOverlap': 0,
        'curriculumOverlap': 0,
        'roomCapacity': 0,
        'roomType': 0,
        'teacherUnavailability': 0,
        'roomReserved': 0,
        'lunchBreak': 0,
        'courseDifferentDays': 0,
        'timeWindow': 0,
        'mutuationSync': 0,
    }

    for a in placed:
        day = a.get('day', '')
        start = int(a.get('startHour', -1))
        end = int(a.get('endHour', -1))
        if start < ds or end > de or start >= end:
            violations['timeWindow'] += 1
        if _event_overlaps_lunch(start, end, ls, le):
            violations['lunchBreak'] += 1

        course = courses_by_id.get(a.get('courseId', ''), {})
        room = rooms_by_id.get(a.get('roomId', ''), {})
        expected = int(course.get('expectedStudents', 0))
        room_cap = int(room.get('capacity', 0)) if room else 0
        if room and room_cap < expected:
            violations['roomCapacity'] += 1

        course_room_type = course.get('roomType', 'lecture')
        room_type = room.get('type', 'lecture') if room else ''
        if room and course_room_type and room_type != course_room_type:
            violations['roomType'] += 1

        if a.get('courseId'):
            by_course_days[a['courseId']].add(day)

        curricula = a.get('curriculaIds') or course.get('curriculaIds', [])
        teachers = a.get('teacherIds', [])

        for h in range(start, end):
            for tid in teachers:
                key = (tid, day, h)
                if key in teacher_slot_owner:
                    violations['teacherOverlap'] += 1
                else:
                    teacher_slot_owner[key] = a.get('eventId', '')
                if (day, h) in unav_by_teacher.get(tid, set()):
                    violations['teacherUnavailability'] += 1

            room_key = (a.get('roomId', ''), day, h)
            if room_key in room_slot_owner:
                violations['roomOverlap'] += 1
            else:
                room_slot_owner[room_key] = a.get('eventId', '')

            if (day, h) in room_unav_by_room.get(a.get('roomId', ''), set()):
                violations['roomReserved'] += 1

            for cid in curricula:
                ckey = (cid, day, h)
                if ckey in curr_slot_owner:
                    violations['curriculumOverlap'] += 1
                else:
                    curr_slot_owner[ckey] = a.get('eventId', '')

    for course in data.get('courses', []):
        cid = course.get('id', '')
        n_events = len(course.get('weeklyEvents', []))
        if n_events > 1:
            if len(by_course_days.get(cid, set())) != n_events:
                violations['courseDifferentDays'] += 1

    mut_group_by_course = {}
    for course in data.get('courses', []):
        cid = course.get('id', '')
        mg = str(course.get('mutuationGroup') or course.get('sharedWithCourseId') or '').strip()
        if cid and mg:
            mut_group_by_course[cid] = mg

    slots_by_course = defaultdict(set)
    for a in placed:
        cid = a.get('courseId', '')
        if not cid:
            continue
        slots_by_course[cid].add((a.get('day', ''), int(a.get('startHour', -1)), int(a.get('endHour', -1))))

    groups = defaultdict(list)
    for cid, mg in mut_group_by_course.items():
        groups[mg].append(cid)

    for _, members in groups.items():
        canonical = None
        for cid in members:
            slots = slots_by_course.get(cid, set())
            if slots:
                canonical = slots
                break
        if not canonical:
            continue
        for cid in members:
            slots = slots_by_course.get(cid, set())
            if slots and slots != canonical:
                violations['mutuationSync'] += 1

    checks = [
        {
            'id': 'teacherOverlap',
            'label': 'No sovrapposizioni docenti',
            'respected': violations['teacherOverlap'] == 0,
            'violations': violations['teacherOverlap'],
        },
        {
            'id': 'roomOverlap',
            'label': 'No sovrapposizioni aule',
            'respected': violations['roomOverlap'] == 0,
            'violations': violations['roomOverlap'],
        },
        {
            'id': 'curriculumOverlap',
            'label': 'No sovrapposizioni curricula',
            'respected': violations['curriculumOverlap'] == 0,
            'violations': violations['curriculumOverlap'],
        },
        {
            'id': 'roomCapacity',
            'label': 'Capienza aula sufficiente',
            'respected': violations['roomCapacity'] == 0,
            'violations': violations['roomCapacity'],
        },
        {
            'id': 'roomType',
            'label': 'Tipo aula coerente con corso',
            'respected': violations['roomType'] == 0,
            'violations': violations['roomType'],
        },
        {
            'id': 'teacherUnavailability',
            'label': 'Rispetto indisponibilita docenti',
            'respected': violations['teacherUnavailability'] == 0,
            'violations': violations['teacherUnavailability'],
        },
        {
            'id': 'roomReserved',
            'label': 'Rispetto prenotazioni aule esistenti',
            'respected': violations['roomReserved'] == 0,
            'violations': violations['roomReserved'],
        },
        {
            'id': 'lunchBreak',
            'label': 'Nessuna lezione in pausa pranzo',
            'respected': violations['lunchBreak'] == 0,
            'violations': violations['lunchBreak'],
        },
        {
            'id': 'courseDifferentDays',
            'label': 'Eventi stesso corso in giorni diversi',
            'respected': violations['courseDifferentDays'] == 0,
            'violations': violations['courseDifferentDays'],
        },
        {
            'id': 'timeWindow',
            'label': 'Lezioni nella finestra oraria',
            'respected': violations['timeWindow'] == 0,
            'violations': violations['timeWindow'],
        },
        {
            'id': 'mutuationSync',
            'label': 'Mutuazioni sincronizzate (vincolo forte)',
            'respected': violations['mutuationSync'] == 0,
            'violations': violations['mutuationSync'],
        },
    ]

    respected = sum(1 for c in checks if c['respected'])
    return {
        'checks': checks,
        'respectedCount': respected,
        'totalChecks': len(checks),
        'allHardConstraintsRespected': respected == len(checks),
        'placedEvents': len(placed),
        'totalEvents': len(assignments),
    }


def _evaluate_soft_constraints(assignments, data):
    """Valuta i vincoli soft e calcola un punteggio per docente.

    Il report e indipendente dal solver usato, quindi valido anche per
    schedule importati o modificati manualmente.
    """
    tm = data.get('meta', {}).get('timeModel', {})
    ds = int(tm.get('dayStart', 8))
    de = int(tm.get('dayEnd', 19))

    weights = data.get('softPolicy', {}).get('weights', {})
    gap_w = int(weights.get('curriculumGapPerHour', 10) or 0)
    early_w = int(weights.get('earlyStartPenalty', 2) or 0)
    late_w = int(weights.get('lateStartPenalty', 3) or 0)
    consec_w = int(weights.get('teacherConsecutiveOver3PerHour', 30) or 0)
    daily_w = int(weights.get('teacherDailyOver5PerHour', 20) or 0)
    pattern_w = int(weights.get('patternViolation', 1000) or 0)
    room_change_w = int(weights.get('curriculumRoomChangePenalty', 6) or 0)
    preferred_patterns = data.get('softPolicy', {}).get('preferredPatterns', {})

    placed = [a for a in assignments if a.get('day') in DAYS and int(a.get('startHour', -1)) >= 0]
    courses_by_id = {c.get('id', ''): c for c in data.get('courses', [])}
    teachers_by_id = {t.get('id', ''): t for t in data.get('teachers', [])}

    teacher_day_hours = defaultdict(lambda: defaultdict(set))  # tid -> di -> set(hours)
    teacher_daily_total = defaultdict(lambda: defaultdict(int))
    teacher_pref_early_hits = defaultdict(int)
    teacher_pref_late_hits = defaultdict(int)
    teacher_pref_early_total = defaultdict(int)
    teacher_pref_late_total = defaultdict(int)
    course_days = defaultdict(list)
    curriculum_day_events = defaultdict(list)  # (curriculum, day_idx) -> events

    for a in placed:
        day_name = a.get('day', '')
        di = DAY_INDEX.get(day_name, -1)
        if di < 0:
            continue

        start = int(a.get('startHour', -1))
        end = int(a.get('endHour', -1))
        if end <= start:
            continue

        duration = max(1, end - start)
        course_id = a.get('courseId', '')
        course_days[course_id].append(di)

        curricula = list(a.get('curriculaIds') or courses_by_id.get(course_id, {}).get('curriculaIds', []))
        for cid in curricula:
            curriculum_day_events[(cid, di)].append(a)

        teacher_ids = list(a.get('teacherIds') or courses_by_id.get(course_id, {}).get('teacherIds', []))
        for tid in teacher_ids:
            teacher_daily_total[tid][di] += duration
            for h in range(start, end):
                teacher_day_hours[tid][di].add(h)

            t = teachers_by_id.get(tid, {})
            prefs = t.get('preferences', {}) if isinstance(t, dict) else {}
            if prefs.get('avoidEarly'):
                teacher_pref_early_total[tid] += 1
                if start == ds:
                    teacher_pref_early_hits[tid] += 1
            if prefs.get('avoidLate'):
                teacher_pref_late_total[tid] += 1
                if start >= 17:
                    teacher_pref_late_hits[tid] += 1

    # 1) Gap curriculum (ore buco tra lezioni nello stesso giorno)
    gap_hours = 0
    for (_, _), evts in curriculum_day_events.items():
        if len(evts) < 2:
            continue
        intervals = []
        for e in evts:
            s = int(e.get('startHour', -1))
            f = int(e.get('endHour', -1))
            if s >= 0 and f > s:
                intervals.append((s, f))
        if len(intervals) < 2:
            continue
        intervals.sort(key=lambda x: (x[0], x[1]))
        merged = []
        for s, f in intervals:
            if not merged or s > merged[-1][1]:
                merged.append([s, f])
            else:
                merged[-1][1] = max(merged[-1][1], f)
        for i in range(1, len(merged)):
            gap = max(0, merged[i][0] - merged[i - 1][1])
            gap_hours += gap

    gap_penalty = gap_hours * gap_w

    # 2) Early / Late start con preferenze docenti
    early_hits = sum(teacher_pref_early_hits.values())
    late_hits = sum(teacher_pref_late_hits.values())
    early_penalty = early_hits * max(1, early_w)
    late_penalty = late_hits * max(1, late_w)

    # 3) Ore consecutive docente oltre 3
    consecutive_excess_hours = 0
    daily_over5_hours = 0
    teacher_max_consecutive = defaultdict(int)
    teacher_days_taught = defaultdict(set)

    for tid, by_day in teacher_day_hours.items():
        for di, hour_set in by_day.items():
            if not hour_set:
                continue
            teacher_days_taught[tid].add(di)
            hours = sorted(hour_set)
            block = 1
            max_block = 1
            for idx in range(1, len(hours)):
                if hours[idx] == hours[idx - 1] + 1:
                    block += 1
                else:
                    max_block = max(max_block, block)
                    block = 1
            max_block = max(max_block, block)
            teacher_max_consecutive[tid] = max(teacher_max_consecutive[tid], max_block)
            if max_block > 3:
                consecutive_excess_hours += (max_block - 3)

    for tid, by_day in teacher_daily_total.items():
        for _, total_h in by_day.items():
            if total_h > 5:
                daily_over5_hours += (total_h - 5)

    consecutive_penalty = consecutive_excess_hours * consec_w
    daily_penalty = daily_over5_hours * daily_w

    # 4) Violazioni pattern distribuzione settimanale
    pattern_violations = 0
    for cid, days in course_days.items():
        course = courses_by_id.get(cid, {})
        expected_events = len(course.get('weeklyEvents', []))
        if expected_events not in (2, 3):
            continue
        allowed = _course_allowed_patterns(course, preferred_patterns)
        if not allowed:
            continue
        unique_days = sorted(set(days))
        if len(unique_days) != expected_events:
            pattern_violations += 1
            continue
        if tuple(unique_days) not in allowed:
            pattern_violations += 1
    pattern_penalty = pattern_violations * pattern_w

    # 5) Cambio aula curriculum stesso giorno
    room_changes = 0
    for (_, _), evts in curriculum_day_events.items():
        if len(evts) < 2:
            continue
        ordered = sorted(
            evts,
            key=lambda e: (int(e.get('startHour', -1)), int(e.get('endHour', -1)))
        )
        prev_room = None
        for e in ordered:
            room_id = e.get('roomId', '')
            if prev_room is not None and room_id and room_id != prev_room:
                room_changes += 1
            prev_room = room_id or prev_room
    room_change_penalty = room_changes * room_change_w

    checks = [
        {
            'id': 'curriculumGapPerHour',
            'label': 'Buchi curriculum (stesso giorno)',
            'violations': gap_hours,
            'weight': gap_w,
            'penalty': gap_penalty,
            'respected': gap_hours == 0,
        },
        {
            'id': 'teacherAvoidEarly',
            'label': 'Preferenze docenti: evitare slot iniziale',
            'violations': early_hits,
            'weight': early_w,
            'penalty': early_penalty,
            'respected': early_hits == 0,
        },
        {
            'id': 'teacherAvoidLate',
            'label': 'Preferenze docenti: evitare slot tardi',
            'violations': late_hits,
            'weight': late_w,
            'penalty': late_penalty,
            'respected': late_hits == 0,
        },
        {
            'id': 'teacherConsecutiveOver3PerHour',
            'label': 'Docenti con blocchi consecutivi oltre 3h',
            'violations': consecutive_excess_hours,
            'weight': consec_w,
            'penalty': consecutive_penalty,
            'respected': consecutive_excess_hours == 0,
        },
        {
            'id': 'teacherDailyOver5PerHour',
            'label': 'Docenti con ore giornaliere oltre 5h',
            'violations': daily_over5_hours,
            'weight': daily_w,
            'penalty': daily_penalty,
            'respected': daily_over5_hours == 0,
        },
        {
            'id': 'patternViolation',
            'label': 'Distribuzione giorni non in pattern preferito',
            'violations': pattern_violations,
            'weight': pattern_w,
            'penalty': pattern_penalty,
            'respected': pattern_violations == 0,
        },
        {
            'id': 'curriculumRoomChangePenalty',
            'label': 'Cambi aula nello stesso curriculum/giorno',
            'violations': room_changes,
            'weight': room_change_w,
            'penalty': room_change_penalty,
            'respected': room_changes == 0,
        },
    ]

    total_penalty = sum(int(c.get('penalty', 0) or 0) for c in checks)
    respected_count = sum(1 for c in checks if c.get('respected'))

    # Punti docenti: premia rispetto preferenze/limiti giornalieri e consecutivi.
    teacher_points = []
    for tid, teacher in teachers_by_id.items():
        days_taught = teacher_days_taught.get(tid, set())
        taught_days_count = len(days_taught)
        if taught_days_count == 0 and teacher_pref_early_total.get(tid, 0) == 0 and teacher_pref_late_total.get(tid, 0) == 0:
            continue

        early_total = teacher_pref_early_total.get(tid, 0)
        early_ok = max(0, early_total - teacher_pref_early_hits.get(tid, 0))
        late_total = teacher_pref_late_total.get(tid, 0)
        late_ok = max(0, late_total - teacher_pref_late_hits.get(tid, 0))

        daily_ok_days = 0
        consecutive_ok_days = 0
        for di in days_taught:
            day_total = teacher_daily_total.get(tid, {}).get(di, 0)
            if day_total <= 5:
                daily_ok_days += 1

            day_hours = sorted(teacher_day_hours.get(tid, {}).get(di, set()))
            max_block = 0
            if day_hours:
                block = 1
                max_block = 1
                for idx in range(1, len(day_hours)):
                    if day_hours[idx] == day_hours[idx - 1] + 1:
                        block += 1
                    else:
                        max_block = max(max_block, block)
                        block = 1
                max_block = max(max_block, block)
            if max_block <= 3:
                consecutive_ok_days += 1

        points = early_ok + late_ok + (daily_ok_days * 2) + (consecutive_ok_days * 2)
        max_points = early_total + late_total + (taught_days_count * 2) + (taught_days_count * 2)
        if max_points <= 0:
            percentage = 100
        else:
            percentage = round((points / max_points) * 100, 1)

        teacher_points.append({
            'teacherId': tid,
            'teacherName': teacher.get('name', tid),
            'points': points,
            'maxPoints': max_points,
            'percentage': percentage,
            'details': {
                'respectAvoidEarly': {'ok': early_ok, 'total': early_total},
                'respectAvoidLate': {'ok': late_ok, 'total': late_total},
                'dailyLoadWithin5hDays': {'ok': daily_ok_days, 'total': taught_days_count},
                'consecutiveLoadWithin3hDays': {'ok': consecutive_ok_days, 'total': taught_days_count},
                'maxConsecutiveHours': teacher_max_consecutive.get(tid, 0),
            },
        })

    teacher_points.sort(key=lambda x: (-x.get('percentage', 0), -x.get('points', 0), x.get('teacherName', '')))

    return {
        'checks': checks,
        'respectedCount': respected_count,
        'totalChecks': len(checks),
        'allSoftConstraintsRespected': respected_count == len(checks),
        'totalPenalty': total_penalty,
        'teacherPoints': teacher_points,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CP-SAT Solver (primario)
# ═══════════════════════════════════════════════════════════════════════════
def _solve_cpsat(data, time_limit_s=30):
    model = cp_model.CpModel()

    # Parametri temporali
    tm = data.get('meta', {}).get('timeModel', {})
    ds = tm.get('dayStart', 8)
    de = tm.get('dayEnd', 19)
    ls = tm.get('lunchStart', 13)
    le = tm.get('lunchEnd', 14)

    events = _collect_events(data)
    rooms = data.get('rooms', [])
    n_rooms = len(rooms)
    courses_by_id = {c['id']: c for c in data.get('courses', [])}

    if not events:
        return {'assignments': [], 'status': 'no_events',
                'message': 'Nessun evento da schedulare.'}

    # Se non ci sono aule, crea un'aula virtuale
    if not rooms:
        rooms = [{'id': 'VIRTUAL', 'name': 'Aula Virtuale',
                  'capacity': 9999, 'type': 'lecture'}]
        n_rooms = 1

    room_idx_by_id = {r.get('id', ''): idx for idx, r in enumerate(rooms)}

    # -------------------------------------------------------------------
    # Variabili decisionali
    # -------------------------------------------------------------------
    day_v = {}      # giorno (0-4)
    start_v = {}    # ora inizio
    room_v = {}     # indice aula
    flat_v = {}     # tempo "flat" = giorno * 100 + ora
    interval_v = {} # intervallo per NoOverlap
    compat_rooms = {}  # indice -> lista indici aule compatibili

    for e in events:
        i = e['idx']
        vs = _valid_starts(e['duration'], ds, de, ls, le)
        if not vs:
            continue

        day_v[i] = model.NewIntVar(0, len(DAYS) - 1, f'd{i}')
        start_v[i] = model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(vs), f's{i}')

        # Tempo flat
        flat_v[i] = model.NewIntVar(0, 4 * FLAT_MUL + de, f'f{i}')
        model.Add(flat_v[i] == day_v[i] * FLAT_MUL + start_v[i])

        # Intervallo (per NoOverlap)
        interval_v[i] = model.NewFixedSizeIntervalVar(
            flat_v[i], e['duration'], f'iv{i}')

        # Aule compatibili: tipo + capienza
        cr = [j for j, r in enumerate(rooms)
              if r.get('type', 'lecture') == e['roomType']
              and r.get('capacity', 0) >= e['expectedStudents']]
        if not cr:
            cr = [j for j, r in enumerate(rooms)
                  if r.get('capacity', 0) >= e['expectedStudents']]
        if not cr:
            cr = list(range(n_rooms))
        compat_rooms[i] = cr

        room_v[i] = model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(cr), f'r{i}')

    scheduled = sorted(day_v.keys())
    if not scheduled:
        return {'assignments': [], 'status': 'no_valid_slots',
                'message': 'Nessun evento può essere collocato (durate troppo lunghe per la finestra oraria).'}

    # -------------------------------------------------------------------
    # Raggruppamenti per risorsa
    # -------------------------------------------------------------------
    by_teacher = defaultdict(list)
    by_curriculum = defaultdict(list)
    by_course = defaultdict(list)

    for i in scheduled:
        e = events[i]
        for t in e['teacherIds']:
            by_teacher[t].append(i)
        for c in e['curriculaIds']:
            by_curriculum[c].append(i)
        by_course[e['courseId']].append(i)

    # -------------------------------------------------------------------
    # VINCOLI HARD
    # -------------------------------------------------------------------

    # 1. No-overlap docenti (stesso docente non può avere due lezioni contemporanee)
    for t, evts in by_teacher.items():
        if len(evts) > 1:
            model.AddNoOverlap([interval_v[i] for i in evts])

    # 2. No-overlap curricula (stessi studenti non possono avere due lezioni contemporanee)
    for c, evts in by_curriculum.items():
        if len(evts) > 1:
            model.AddNoOverlap([interval_v[i] for i in evts])

    # 3. No-overlap aule (stessa aula non può ospitare due lezioni contemporanee)
    #    Usa intervalli opzionali: presente solo se l'evento è assegnato a quell'aula
    room_opt_intervals = defaultdict(list)
    for i in scheduled:
        for r in compat_rooms[i]:
            is_r = model.NewBoolVar(f'ir{i}_{r}')
            model.Add(room_v[i] == r).OnlyEnforceIf(is_r)
            model.Add(room_v[i] != r).OnlyEnforceIf(is_r.Not())
            opt_iv = model.NewOptionalFixedSizeIntervalVar(
                flat_v[i], events[i]['duration'], is_r, f'oiv{i}_{r}')
            room_opt_intervals[r].append(opt_iv)

    for r in range(n_rooms):
        ivs = room_opt_intervals.get(r, [])
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # 4. Eventi dello stesso corso su giorni diversi
    for cid, evts in by_course.items():
        if len(evts) > 1:
            model.AddAllDifferent([day_v[i] for i in evts])

    # 4b. Corsi mutuati: stesso gruppo, stessa collocazione per evento omologo.
    mut_by_group_order = defaultdict(list)
    for i in scheduled:
        mg = events[i].get('mutuationGroup', '')
        if not mg:
            continue
        key = (mg, int(events[i].get('eventOrder', 0)))
        mut_by_group_order[key].append(i)

    for _, evts in mut_by_group_order.items():
        if len(evts) < 2:
            continue
        base = evts[0]
        for other in evts[1:]:
            model.Add(day_v[other] == day_v[base])
            model.Add(start_v[other] == start_v[base])
            model.Add(room_v[other] == room_v[base])

    # 5. Indisponibilità docenti
    for unav in data.get('unavailability', []):
        tid = unav.get('teacherId', '')
        day_name = unav.get('day', '')
        hours = unav.get('hours', [])
        di = DAY_INDEX.get(day_name, -1)
        if di < 0:
            continue
        for i in by_teacher.get(tid, []):
            on_day = model.NewBoolVar(f'ud{i}_{tid}_{day_name}')
            model.Add(day_v[i] == di).OnlyEnforceIf(on_day)
            model.Add(day_v[i] != di).OnlyEnforceIf(on_day.Not())
            for h in hours:
                # Evento [start, start+dur) NON deve contenere h
                # => start >= h+1 OPPURE start+dur <= h
                ab = model.NewBoolVar(f'ua{i}_{h}_{tid}')
                model.Add(start_v[i] >= h + 1).OnlyEnforceIf([on_day, ab])
                model.Add(
                    start_v[i] + events[i]['duration'] <= h
                ).OnlyEnforceIf([on_day, ab.Not()])

    # 6. Prenotazioni aule già esistenti (blocchi room/day/hour)
    room_unav_by_idx = defaultdict(set)
    for ru in data.get('roomUnavailability', []):
        room_id = ru.get('roomId', '')
        di = DAY_INDEX.get(ru.get('day', ''), -1)
        ri = room_idx_by_id.get(room_id, -1)
        if di < 0 or ri < 0:
            continue
        for h in ru.get('hours', []):
            room_unav_by_idx[ri].add((di, h))

    for i in scheduled:
        dur = events[i]['duration']
        for ri, blocked_slots in room_unav_by_idx.items():
            if ri not in compat_rooms[i]:
                continue

            on_room = model.NewBoolVar(f'rr{i}_{ri}')
            model.Add(room_v[i] == ri).OnlyEnforceIf(on_room)
            model.Add(room_v[i] != ri).OnlyEnforceIf(on_room.Not())

            for di, h in blocked_slots:
                on_day = model.NewBoolVar(f'rrd{i}_{ri}_{di}_{h}')
                model.Add(day_v[i] == di).OnlyEnforceIf(on_day)
                model.Add(day_v[i] != di).OnlyEnforceIf(on_day.Not())

                ab = model.NewBoolVar(f'rra{i}_{ri}_{di}_{h}')
                model.Add(start_v[i] >= h + 1).OnlyEnforceIf([on_room, on_day, ab])
                model.Add(start_v[i] + dur <= h).OnlyEnforceIf([on_room, on_day, ab.Not()])

    # -------------------------------------------------------------------
    # VINCOLI SOFT (obiettivo da minimizzare)
    # -------------------------------------------------------------------
    weights = data.get('softPolicy', {}).get('weights', {})
    gap_w = weights.get('curriculumGapPerHour', 10)
    early_w = weights.get('earlyStartPenalty', 2)
    late_w = weights.get('lateStartPenalty', 3)
    consec_w = weights.get('teacherConsecutiveOver3PerHour', 30)
    daily_w = weights.get('teacherDailyOver5PerHour', 20)
    pattern_w = weights.get('patternViolation', 1000)
    room_change_w = weights.get('curriculumRoomChangePenalty', 6)
    preferred_patterns = data.get('softPolicy', {}).get('preferredPatterns', {})

    obj_parts = []

    # a) Gap tra lezioni dello stesso curriculum nello stesso giorno
    for c, evts in by_curriculum.items():
        for a in range(len(evts)):
            for b in range(a + 1, len(evts)):
                i, j = evts[a], evts[b]
                sd = model.NewBoolVar(f'gsd_{c}_{i}_{j}')
                model.Add(day_v[i] == day_v[j]).OnlyEnforceIf(sd)
                model.Add(day_v[i] != day_v[j]).OnlyEnforceIf(sd.Not())

                bf = model.NewBoolVar(f'gbf_{c}_{i}_{j}')
                model.Add(start_v[i] <= start_v[j]).OnlyEnforceIf([sd, bf])
                model.Add(start_v[i] > start_v[j]).OnlyEnforceIf([sd, bf.Not()])

                max_gap = de - ds
                gap = model.NewIntVar(0, max_gap, f'gap_{c}_{i}_{j}')

                # i prima di j: gap = start_j - (start_i + dur_i)
                diff_ij = model.NewIntVar(-max_gap, max_gap, f'd1_{c}_{i}_{j}')
                model.Add(diff_ij == start_v[j] - start_v[i] - events[i]['duration'])

                # j prima di i: gap = start_i - (start_j + dur_j)
                diff_ji = model.NewIntVar(-max_gap, max_gap, f'd2_{c}_{i}_{j}')
                model.Add(diff_ji == start_v[i] - start_v[j] - events[j]['duration'])

                model.Add(gap >= diff_ij).OnlyEnforceIf([sd, bf])
                model.Add(gap >= diff_ji).OnlyEnforceIf([sd, bf.Not()])
                model.Add(gap == 0).OnlyEnforceIf(sd.Not())

                obj_parts.append(gap * gap_w)

    # b) Penalità lezione alle 8:00
    for i in scheduled:
        prefs = events[i].get('teacherPrefs', {})
        w = early_w
        # Se il docente ha preferenza avoidEarly, peso maggiore
        for tid, p in prefs.items():
            if p.get('avoidEarly'):
                w = max(w, early_w * 5)
        is_early = model.NewBoolVar(f'e{i}')
        model.Add(start_v[i] == ds).OnlyEnforceIf(is_early)
        model.Add(start_v[i] != ds).OnlyEnforceIf(is_early.Not())
        obj_parts.append(is_early * w)

    # c) Penalità lezione tardi (>=17)
    for i in scheduled:
        prefs = events[i].get('teacherPrefs', {})
        w = late_w
        for tid, p in prefs.items():
            if p.get('avoidLate'):
                w = max(w, late_w * 5)
        is_late = model.NewBoolVar(f'l{i}')
        model.Add(start_v[i] >= 17).OnlyEnforceIf(is_late)
        model.Add(start_v[i] < 17).OnlyEnforceIf(is_late.Not())
        obj_parts.append(is_late * w)

    # d) Penalità ore consecutive docente > 3
    for t, evts in by_teacher.items():
        if len(evts) < 2:
            continue
        for a in range(len(evts)):
            for b in range(a + 1, len(evts)):
                i, j = evts[a], evts[b]
                # Se sullo stesso giorno, penalizza se entrambi coprono > 3 ore consecutive
                sd = model.NewBoolVar(f'tsd_{t}_{i}_{j}')
                model.Add(day_v[i] == day_v[j]).OnlyEnforceIf(sd)
                model.Add(day_v[i] != day_v[j]).OnlyEnforceIf(sd.Not())

                # Span totale = max(end_i, end_j) - min(start_i, start_j)
                # Se span > 3 e sono adiacenti, penalizza
                span = model.NewIntVar(0, de - ds, f'tsp_{t}_{i}_{j}')
                max_end = model.NewIntVar(ds, de, f'tme_{t}_{i}_{j}')
                min_start = model.NewIntVar(ds, de, f'tms_{t}_{i}_{j}')
                model.AddMaxEquality(max_end, [
                    start_v[i] + events[i]['duration'],
                    start_v[j] + events[j]['duration']
                ])
                model.AddMinEquality(min_start, [start_v[i], start_v[j]])
                model.Add(span == max_end - min_start)

                over3 = model.NewBoolVar(f'to3_{t}_{i}_{j}')
                model.Add(span > 4).OnlyEnforceIf([sd, over3])
                model.Add(span <= 4).OnlyEnforceIf([sd, over3.Not()])
                # Inactive when different day
                excess = model.NewIntVar(0, de - ds, f'tex_{t}_{i}_{j}')
                model.Add(excess >= span - 4).OnlyEnforceIf([sd, over3])
                model.Add(excess == 0).OnlyEnforceIf(sd.Not())
                model.Add(excess == 0).OnlyEnforceIf([sd, over3.Not()])
                obj_parts.append(excess * consec_w)

    # d2) Penalità cambio aula per lo stesso curriculum nello stesso giorno
    for c, evts in by_curriculum.items():
        if len(evts) < 2 or room_change_w <= 0:
            continue
        for a in range(len(evts)):
            for b in range(a + 1, len(evts)):
                i, j = evts[a], evts[b]

                sd = model.NewBoolVar(f'crsd_{c}_{i}_{j}')
                model.Add(day_v[i] == day_v[j]).OnlyEnforceIf(sd)
                model.Add(day_v[i] != day_v[j]).OnlyEnforceIf(sd.Not())

                same_room = model.NewBoolVar(f'crsr_{c}_{i}_{j}')
                model.Add(room_v[i] == room_v[j]).OnlyEnforceIf(same_room)
                model.Add(room_v[i] != room_v[j]).OnlyEnforceIf(same_room.Not())

                room_change = model.NewBoolVar(f'crchg_{c}_{i}_{j}')
                model.Add(room_change == 0).OnlyEnforceIf(sd.Not())
                model.Add(room_change == 0).OnlyEnforceIf(same_room)
                model.Add(room_change == 1).OnlyEnforceIf([sd, same_room.Not()])
                obj_parts.append(room_change * room_change_w)

    # e) Penalità ore giornaliere docente oltre 5
    for t, evts in by_teacher.items():
        for d in range(len(DAYS)):
            on_day_terms = []
            for i in evts:
                on_day = model.NewBoolVar(f'tday_{t}_{i}_{d}')
                model.Add(day_v[i] == d).OnlyEnforceIf(on_day)
                model.Add(day_v[i] != d).OnlyEnforceIf(on_day.Not())
                on_day_terms.append(on_day * events[i]['duration'])

            day_hours = model.NewIntVar(0, de - ds, f'thours_{t}_{d}')
            model.Add(day_hours == sum(on_day_terms))

            excess_day = model.NewIntVar(0, de - ds, f'tover_{t}_{d}')
            model.Add(excess_day >= day_hours - 5)
            obj_parts.append(excess_day * daily_w)

    # f) Penalità violazione pattern preferito distribuzione settimanale
    for cid, evts in by_course.items():
        n_evts = len(evts)
        if n_evts not in (2, 3):
            continue

        course = courses_by_id.get(cid, {})
        allowed = _course_allowed_patterns(course, preferred_patterns)
        if not allowed:
            continue

        min_day = model.NewIntVar(0, len(DAYS) - 1, f'pmin_{cid}')
        max_day = model.NewIntVar(0, len(DAYS) - 1, f'pmax_{cid}')
        model.AddMinEquality(min_day, [day_v[i] for i in evts])
        model.AddMaxEquality(max_day, [day_v[i] for i in evts])

        sum_day = None
        if n_evts == 3:
            sum_day = model.NewIntVar(0, 3 * (len(DAYS) - 1), f'psum_{cid}')
            model.Add(sum_day == sum(day_v[i] for i in evts))

        match_bools = []
        for p in allowed:
            key = '_'.join(str(x) for x in p)
            eq_min = model.NewBoolVar(f'peqmin_{cid}_{key}')
            eq_max = model.NewBoolVar(f'peqmax_{cid}_{key}')
            model.Add(min_day == p[0]).OnlyEnforceIf(eq_min)
            model.Add(min_day != p[0]).OnlyEnforceIf(eq_min.Not())
            model.Add(max_day == p[-1]).OnlyEnforceIf(eq_max)
            model.Add(max_day != p[-1]).OnlyEnforceIf(eq_max.Not())

            if n_evts == 2:
                match = model.NewBoolVar(f'pmatch_{cid}_{key}')
                model.Add(match <= eq_min)
                model.Add(match <= eq_max)
                model.Add(match >= eq_min + eq_max - 1)
                match_bools.append(match)
            else:
                eq_sum = model.NewBoolVar(f'peqsum_{cid}_{key}')
                model.Add(sum_day == sum(p)).OnlyEnforceIf(eq_sum)
                model.Add(sum_day != sum(p)).OnlyEnforceIf(eq_sum.Not())

                match = model.NewBoolVar(f'pmatch_{cid}_{key}')
                model.Add(match <= eq_min)
                model.Add(match <= eq_max)
                model.Add(match <= eq_sum)
                model.Add(match >= eq_min + eq_max + eq_sum - 2)
                match_bools.append(match)

        if match_bools:
            any_match = model.NewBoolVar(f'pany_{cid}')
            model.AddMaxEquality(any_match, match_bools)
            violation = model.NewBoolVar(f'pviol_{cid}')
            model.Add(violation + any_match == 1)
            obj_parts.append(violation * pattern_w)

    # Funzione obiettivo
    if obj_parts:
        model.Minimize(sum(obj_parts))

    # -------------------------------------------------------------------
    # Risoluzione
    # -------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 4
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        teachers_by_id = {t['id']: t for t in data.get('teachers', [])}
        assignments = []
        for i in scheduled:
            di = solver.Value(day_v[i])
            si = solver.Value(start_v[i])
            ri = solver.Value(room_v[i])
            e = events[i]

            teacher_names = [teachers_by_id.get(tid, {}).get('name', tid)
                             for tid in e['teacherIds']]

            assignments.append({
                'eventId': e['eventId'],
                'courseId': e['courseId'],
                'courseName': e['courseName'],
                'studyYear': e.get('studyYear'),
                'mutuationGroup': e.get('mutuationGroup', ''),
                'day': DAYS[di],
                'dayIt': DAY_NAMES_IT.get(DAYS[di], DAYS[di]),
                'startHour': si,
                'endHour': si + e['duration'],
                'duration': e['duration'],
                'roomId': rooms[ri]['id'] if ri < len(rooms) else 'N/A',
                'roomName': rooms[ri].get('name', '') if ri < len(rooms) else 'N/A',
                'teacherIds': e['teacherIds'],
                'teacherNames': teacher_names,
                'curriculaIds': e['curriculaIds'],
                'programId': e['programId'],
                'color': _course_color(e['courseId']),
            })

        hard_report = _validate_hard_constraints(assignments, data)
        soft_report = _evaluate_soft_constraints(assignments, data)

        return {
            'assignments': assignments,
            'status': 'optimal' if status == cp_model.OPTIMAL else 'feasible',
            'objective': solver.ObjectiveValue() if obj_parts else 0,
            'wallTime': round(solver.WallTime(), 2),
            'algorithm': 'CP-SAT',
            'algorithmLabel': 'Google OR-Tools CP-SAT',
            'hardConstraintReport': hard_report,
            'softConstraintReport': soft_report,
            'message': ('Soluzione ottimale trovata.' if status == cp_model.OPTIMAL
                        else 'Soluzione ammissibile trovata (non garantita ottimale).'),
        }
    else:
        return {
            'assignments': [],
            'status': 'infeasible',
            'objective': None,
            'algorithm': 'CP-SAT',
            'algorithmLabel': 'Google OR-Tools CP-SAT',
            'hardConstraintReport': {
                'checks': [],
                'respectedCount': 0,
                'totalChecks': 0,
                'allHardConstraintsRespected': False,
                'placedEvents': 0,
                'totalEvents': 0,
            },
            'softConstraintReport': {
                'checks': [],
                'respectedCount': 0,
                'totalChecks': 0,
                'allSoftConstraintsRespected': False,
                'totalPenalty': 0,
                'teacherPoints': [],
            },
            'message': ('Impossibile trovare una soluzione. Controlla i vincoli: '
                        'troppi corsi per le aule disponibili, indisponibilità '
                        'troppo restrittive, o eventi dello stesso corso che '
                        'superano i 5 giorni disponibili.'),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Greedy Solver (fallback se OR-Tools non disponibile)
# ═══════════════════════════════════════════════════════════════════════════
def _solve_greedy(data):
    """Solver greedy euristico. Prova a piazzare ogni evento
    nella migliore posizione disponibile, uno alla volta."""

    tm = data.get('meta', {}).get('timeModel', {})
    ds = tm.get('dayStart', 8)
    de = tm.get('dayEnd', 19)
    ls = tm.get('lunchStart', 13)
    le = tm.get('lunchEnd', 14)

    events = _collect_events(data)
    rooms = data.get('rooms', [])
    teachers_by_id = {t['id']: t for t in data.get('teachers', [])}
    courses_by_id = {c['id']: c for c in data.get('courses', [])}

    weights = data.get('softPolicy', {}).get('weights', {})
    gap_w = weights.get('curriculumGapPerHour', 10)
    early_w = weights.get('earlyStartPenalty', 2)
    late_w = weights.get('lateStartPenalty', 3)
    daily_w = weights.get('teacherDailyOver5PerHour', 20)
    pattern_w = weights.get('patternViolation', 1000)
    room_change_w = weights.get('curriculumRoomChangePenalty', 6)
    preferred_patterns = data.get('softPolicy', {}).get('preferredPatterns', {})
    allowed_patterns_by_course = {
        cid: _course_allowed_patterns(course, preferred_patterns)
        for cid, course in courses_by_id.items()
    }

    if not events:
        return {'assignments': [], 'status': 'no_events',
                'message': 'Nessun evento da schedulare.'}

    if not rooms:
        rooms = [{'id': 'VIRTUAL', 'name': 'Aula Virtuale',
                  'capacity': 9999, 'type': 'lecture'}]

    # Stato di occupazione
    teacher_slots = defaultdict(set)    # tid -> set di (day_idx, hour)
    curriculum_slots = defaultdict(set)  # cid -> set di (day_idx, hour)
    room_slots = defaultdict(set)        # room_idx -> set di (day_idx, hour)
    course_days = defaultdict(set)       # courseId -> set di day_idx
    assigned_by_mut_order = {}           # (mutGroup, eventOrder) -> (day,start,room)

    # Indisponibilità pre-calcolate
    unav_lookup = defaultdict(set)  # tid -> set di (day_idx, hour)
    for unav in data.get('unavailability', []):
        tid = unav.get('teacherId', '')
        di = DAY_INDEX.get(unav.get('day', ''), -1)
        if di < 0:
            continue
        for h in unav.get('hours', []):
            unav_lookup[tid].add((di, h))

    room_unav_lookup = defaultdict(set)  # room_id -> set di (day_idx, hour)
    for ru in data.get('roomUnavailability', []):
        rid = ru.get('roomId', '')
        di = DAY_INDEX.get(ru.get('day', ''), -1)
        if di < 0:
            continue
        for h in ru.get('hours', []):
            room_unav_lookup[rid].add((di, h))

    def _slots(day_idx, start, duration):
        """Restituisce l'insieme di (day, hour) occupati."""
        return {(day_idx, start + h) for h in range(duration)}

    def is_valid(event, day_idx, start, room_idx):
        dur = event['duration']
        slots = _slots(day_idx, start, dur)
        room_id = rooms[room_idx].get('id', '')

        # Stesso corso su giorno diverso
        if day_idx in course_days[event['courseId']]:
            return False

        # Conflitto docenti
        for tid in event['teacherIds']:
            if slots & teacher_slots[tid]:
                return False
            if slots & unav_lookup[tid]:
                return False

        # Conflitto curricula
        for cid in event['curriculaIds']:
            if slots & curriculum_slots[cid]:
                return False

        # Conflitto aula
        if slots & room_slots[room_idx]:
            return False

        # Aula già prenotata da orari precedenti
        if slots & room_unav_lookup.get(room_id, set()):
            return False

        return True

    def score(event, day_idx, start):
        """Punteggio: più basso = migliore."""
        sc = 0
        dur = event['duration']

        # Penalizza buchi con lezioni dello stesso curriculum sullo stesso giorno
        for cid in event['curriculaIds']:
            existing = sorted([h for (d, h) in curriculum_slots[cid]
                               if d == day_idx])
            if existing:
                evt_hours = list(range(start, start + dur))
                all_hours = sorted(set(existing + evt_hours))
                # Conta buchi
                for k in range(1, len(all_hours)):
                    gap = all_hours[k] - all_hours[k - 1]
                    if gap > 1:
                        # Non contare pausa pranzo come buco
                        if all_hours[k - 1] < ls and all_hours[k] >= le:
                            gap -= (le - ls)
                        sc += max(0, gap - 1) * gap_w

        # Preferenze orarie
        if start == ds:
            sc += early_w
            for tid in event['teacherIds']:
                t = teachers_by_id.get(tid, {})
                if t.get('preferences', {}).get('avoidEarly'):
                    sc += early_w * 5
        if start >= 17:
            sc += late_w
            for tid in event['teacherIds']:
                t = teachers_by_id.get(tid, {})
                if t.get('preferences', {}).get('avoidLate'):
                    sc += late_w * 5

        # Penalità ore giornaliere oltre soglia per docente
        for tid in event['teacherIds']:
            current_day_hours = sum(1 for (d, _) in teacher_slots[tid] if d == day_idx)
            projected = current_day_hours + dur
            if projected > 5:
                sc += (projected - 5) * daily_w

        # Penalità cambio aula per curriculum nello stesso giorno.
        if room_change_w > 0:
            for cid in event['curriculaIds']:
                rooms_same_day = set()
                for other in assignments:
                    if other.get('day') == DAYS[day_idx] and cid in (other.get('curriculaIds') or []):
                        rid = other.get('roomId', '')
                        if rid and rid != 'N/A':
                            rooms_same_day.add(rid)
                if rooms_same_day:
                    # Stima minima: qualsiasi cambio rispetto alla prima aula vista.
                    pref_room = next(iter(rooms_same_day))
                    # La room precisa viene valutata più avanti quando disponibile.
                    sc += room_change_w if pref_room else 0

        # Penalità violazione pattern preferito quando la distribuzione è completa
        allowed = allowed_patterns_by_course.get(event['courseId'], [])
        expected_events = len(courses_by_id.get(event['courseId'], {}).get('weeklyEvents', []))
        if allowed and expected_events in (2, 3):
            projected_days = set(course_days[event['courseId']]) | {day_idx}
            if len(projected_days) == expected_events:
                if tuple(sorted(projected_days)) not in allowed:
                    sc += pattern_w

        # Preferisci lezioni la mattina (9-12) o primo pomeriggio (14-16)
        if 9 <= start <= 11:
            sc -= 1
        if 14 <= start <= 15:
            sc -= 1

        return sc

    def assign(event, day_idx, start, room_idx):
        dur = event['duration']
        slots = _slots(day_idx, start, dur)
        for tid in event['teacherIds']:
            teacher_slots[tid] |= slots
        for cid in event['curriculaIds']:
            curriculum_slots[cid] |= slots
        room_slots[room_idx] |= slots
        course_days[event['courseId']].add(day_idx)

    # Ordina eventi: più vincolati prima (più docenti, più curricula, durata maggiore)
    sorted_events = sorted(events, key=lambda e: -(
        len(e['teacherIds']) * 3 + len(e['curriculaIds']) * 2 + e['duration']))

    assignments = []
    for event in sorted_events:
        mg = event.get('mutuationGroup', '')
        mo = int(event.get('eventOrder', 0) or 0)
        mut_key = (mg, mo) if mg else None

        if mut_key and mut_key in assigned_by_mut_order:
            d, h, r = assigned_by_mut_order[mut_key]
            if is_valid(event, d, h, r):
                assign(event, d, h, r)
                teacher_names = [teachers_by_id.get(tid, {}).get('name', tid)
                                 for tid in event['teacherIds']]
                assignments.append({
                    'eventId': event['eventId'],
                    'courseId': event['courseId'],
                    'courseName': event['courseName'],
                    'studyYear': event.get('studyYear'),
                    'mutuationGroup': event.get('mutuationGroup', ''),
                    'day': DAYS[d],
                    'dayIt': DAY_NAMES_IT.get(DAYS[d], DAYS[d]),
                    'startHour': h,
                    'endHour': h + event['duration'],
                    'duration': event['duration'],
                    'roomId': rooms[r]['id'],
                    'roomName': rooms[r].get('name', ''),
                    'teacherIds': event['teacherIds'],
                    'teacherNames': teacher_names,
                    'curriculaIds': event['curriculaIds'],
                    'programId': event['programId'],
                    'color': _course_color(event['courseId']),
                })
                continue

        vs = _valid_starts(event['duration'], ds, de, ls, le)

        cr = [j for j, r in enumerate(rooms)
              if r.get('type', 'lecture') == event['roomType']
              and r.get('capacity', 0) >= event['expectedStudents']]
        if not cr:
            cr = [j for j, r in enumerate(rooms)
                  if r.get('capacity', 0) >= event['expectedStudents']]
        if not cr:
            cr = list(range(len(rooms)))

        best = None
        best_sc = float('inf')

        for d in range(5):
            for h in vs:
                for r in cr:
                    if is_valid(event, d, h, r):
                        sc = score(event, d, h)
                        if sc < best_sc:
                            best_sc = sc
                            best = (d, h, r)

        if best:
            d, h, r = best
            assign(event, d, h, r)
            if mut_key and mut_key not in assigned_by_mut_order:
                assigned_by_mut_order[mut_key] = (d, h, r)
            teacher_names = [teachers_by_id.get(tid, {}).get('name', tid)
                             for tid in event['teacherIds']]
            assignments.append({
                'eventId': event['eventId'],
                'courseId': event['courseId'],
                'courseName': event['courseName'],
                'studyYear': event.get('studyYear'),
                'mutuationGroup': event.get('mutuationGroup', ''),
                'day': DAYS[d],
                'dayIt': DAY_NAMES_IT.get(DAYS[d], DAYS[d]),
                'startHour': h,
                'endHour': h + event['duration'],
                'duration': event['duration'],
                'roomId': rooms[r]['id'],
                'roomName': rooms[r].get('name', ''),
                'teacherIds': event['teacherIds'],
                'teacherNames': teacher_names,
                'curriculaIds': event['curriculaIds'],
                'programId': event['programId'],
                'color': _course_color(event['courseId']),
            })
        else:
            assignments.append({
                'eventId': event['eventId'],
                'courseId': event['courseId'],
                'courseName': event['courseName'],
                'studyYear': event.get('studyYear'),
                'mutuationGroup': event.get('mutuationGroup', ''),
                'day': 'N/A',
                'dayIt': 'N/A',
                'startHour': -1,
                'endHour': -1,
                'duration': event['duration'],
                'roomId': 'N/A',
                'roomName': 'N/A',
                'teacherIds': event['teacherIds'],
                'teacherNames': [],
                'curriculaIds': event['curriculaIds'],
                'programId': event['programId'],
                'color': '#888',
                'error': 'Impossibile piazzare questo evento con i vincoli attuali.',
            })

    unplaced = sum(1 for a in assignments if a.get('error'))
    hard_report = _validate_hard_constraints(assignments, data)
    soft_report = _evaluate_soft_constraints(assignments, data)
    return {
        'assignments': assignments,
        'status': 'feasible' if unplaced == 0 else 'partial',
        'objective': None,
        'unplaced': unplaced,
        'algorithm': 'Greedy',
        'algorithmLabel': 'Greedy Heuristic Fallback',
        'hardConstraintReport': hard_report,
        'softConstraintReport': soft_report,
        'message': (f'Scheduling completato. {len(assignments) - unplaced}/{len(assignments)} '
                    f'eventi piazzati.' +
                    (f' {unplaced} eventi non piazzabili.' if unplaced else '')),
    }


# ---------------------------------------------------------------------------
# Test stand-alone
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            data = json.load(f)
    else:
        print("Uso: python scheduler.py <database.json>")
        print(f"OR-Tools disponibile: {HAS_ORTOOLS}")
        sys.exit(0)

    result = solve(data)
    print(json.dumps(result, indent=2, ensure_ascii=False))
