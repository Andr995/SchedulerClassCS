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
def solve(data, time_limit_s=30):
    """Genera l'orario ottimale.

    Args:
        data: dizionario con chiavi meta, rooms, teachers, programs,
              curricula, courses, unavailability, softPolicy.
        time_limit_s: tempo massimo in secondi per il solver.

    Returns:
        dict con 'assignments' (lista di assegnazioni), 'status', 'objective'.
    """
    if HAS_ORTOOLS:
        return _solve_cpsat(data, time_limit_s)
    else:
        return _solve_greedy(data)


# ---------------------------------------------------------------------------
# Raccolta eventi
# ---------------------------------------------------------------------------
def _collect_events(data):
    """Estrae tutti gli eventi schedulabili dai corsi."""
    events = []
    teachers_by_id = {t['id']: t for t in data.get('teachers', [])}
    for course in data.get('courses', []):
        for evt in course.get('weeklyEvents', []):
            teacher_prefs = {}
            for tid in course.get('teacherIds', []):
                t = teachers_by_id.get(tid, {})
                prefs = t.get('preferences', {})
                teacher_prefs[tid] = prefs

            events.append({
                'idx': len(events),
                'eventId': evt.get('id', f"E-{len(events)}"),
                'courseId': course['id'],
                'courseName': course.get('name', ''),
                'duration': max(1, evt.get('durationHours', 1)),
                'teacherIds': course.get('teacherIds', []),
                'curriculaIds': course.get('curriculaIds', []),
                'programId': course.get('programId', ''),
                'expectedStudents': course.get('expectedStudents', 0),
                'roomType': course.get('roomType', 'lecture'),
                'teacherPrefs': teacher_prefs,
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
                'day': DAYS[di],
                'dayIt': DAY_NAMES_IT.get(DAYS[di], DAYS[di]),
                'startHour': si,
                'endHour': si + e['duration'],
                'duration': e['duration'],
                'roomId': rooms[ri]['id'] if ri < len(rooms) else 'N/A',
                'roomName': rooms[ri].get('name', '') if ri < len(rooms) else 'N/A',
                'teacherIds': e['teacherIds'],
                'teacherNames': teacher_names,
                'color': _course_color(e['courseId']),
            })

        return {
            'assignments': assignments,
            'status': 'optimal' if status == cp_model.OPTIMAL else 'feasible',
            'objective': solver.ObjectiveValue() if obj_parts else 0,
            'wallTime': round(solver.WallTime(), 2),
            'message': ('Soluzione ottimale trovata.' if status == cp_model.OPTIMAL
                        else 'Soluzione ammissibile trovata (non garantita ottimale).'),
        }
    else:
        return {
            'assignments': [],
            'status': 'infeasible',
            'objective': None,
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

    # Indisponibilità pre-calcolate
    unav_lookup = defaultdict(set)  # tid -> set di (day_idx, hour)
    for unav in data.get('unavailability', []):
        tid = unav.get('teacherId', '')
        di = DAY_INDEX.get(unav.get('day', ''), -1)
        if di < 0:
            continue
        for h in unav.get('hours', []):
            unav_lookup[tid].add((di, h))

    def _slots(day_idx, start, duration):
        """Restituisce l'insieme di (day, hour) occupati."""
        return {(day_idx, start + h) for h in range(duration)}

    def is_valid(event, day_idx, start, room_idx):
        dur = event['duration']
        slots = _slots(day_idx, start, dur)

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
            teacher_names = [teachers_by_id.get(tid, {}).get('name', tid)
                             for tid in event['teacherIds']]
            assignments.append({
                'eventId': event['eventId'],
                'courseId': event['courseId'],
                'courseName': event['courseName'],
                'day': DAYS[d],
                'dayIt': DAY_NAMES_IT.get(DAYS[d], DAYS[d]),
                'startHour': h,
                'endHour': h + event['duration'],
                'duration': event['duration'],
                'roomId': rooms[r]['id'],
                'roomName': rooms[r].get('name', ''),
                'teacherIds': event['teacherIds'],
                'teacherNames': teacher_names,
                'color': _course_color(event['courseId']),
            })
        else:
            assignments.append({
                'eventId': event['eventId'],
                'courseId': event['courseId'],
                'courseName': event['courseName'],
                'day': 'N/A',
                'dayIt': 'N/A',
                'startHour': -1,
                'endHour': -1,
                'duration': event['duration'],
                'roomId': 'N/A',
                'roomName': 'N/A',
                'teacherIds': event['teacherIds'],
                'teacherNames': [],
                'color': '#888',
                'error': 'Impossibile piazzare questo evento con i vincoli attuali.',
            })

    unplaced = sum(1 for a in assignments if a.get('error'))
    return {
        'assignments': assignments,
        'status': 'feasible' if unplaced == 0 else 'partial',
        'objective': None,
        'unplaced': unplaced,
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
