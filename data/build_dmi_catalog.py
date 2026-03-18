import json
import re
import unicodedata
from collections import defaultdict
from urllib import request

API_BASE = 'https://public.smartedu.unict.it/CourseAPI'
ACADEMIC_YEAR = 2025
STRUCTURE_CODE = '190141'  # DMI


def fetch_json(path, payload):
    body = json.dumps(payload).encode('utf-8')
    req = request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
    )
    with request.urlopen(req, timeout=90) as r:
        return json.load(r)


def text_it(obj, default=''):
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item.get('iso') == 'ita' and item.get('text'):
                return str(item.get('text'))
        for item in obj:
            if isinstance(item, dict) and item.get('text'):
                return str(item.get('text'))
    return default


def slug(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:48]


def teacher_id(name):
    return f"T-{slug(name)}".upper()[:64]


def curriculum_id(program_id, curriculum_name, path_id):
    base = slug(curriculum_name) or slug(path_id) or 'curriculum'
    return f"CUR-{program_id}-{base}".upper()[:72]


def course_id(program_id, code, name):
    code_text = str(code or '').strip()
    if code_text:
        return code_text
    name_text = str(name or '').strip()
    return slug(name_text).upper()[:72]


def dedup_keep_order(items):
    out = []
    seen = set()
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def main():
    listing = fetch_json('/getDepartmentsAndCourses', {
        'mode': 'classRoomList',
        'academicYear': ACADEMIC_YEAR,
        'courseTypes': ['CorsoDiStudio'],
    })

    dmi_struct = None
    for struct in listing.get('data', {}).get('data', []):
        if str(struct.get('code', '')).strip() == STRUCTURE_CODE:
            dmi_struct = struct
            break

    if not dmi_struct:
        raise SystemExit('Struttura DMI non trovata su getDepartmentsAndCourses')

    selected_courses = []
    for c in dmi_struct.get('courses', []):
        classes = [str(x).strip() for x in (c.get('classes') or [])]
        if any(x.startswith('DOTT') for x in classes):
            continue
        selected_courses.append(c)

    # Keep the requested set explicitly if present.
    wanted_classes = {'L-31', 'L-35', 'LM-18', 'LM-40', 'LM-DATA'}
    filtered = []
    for c in selected_courses:
        classes = [str(x).upper().replace(' R', '').strip() for x in (c.get('classes') or [])]
        if any(cls in wanted_classes for cls in classes):
            filtered.append(c)
    if filtered:
        selected_courses = filtered

    db = {
        'meta': {
            'version': 1,
            'timeModel': {
                'dayStart': 8,
                'dayEnd': 19,
                'lunchStart': 13,
                'lunchEnd': 14,
                'granularityHours': 1,
            },
            'note': (
                'Dataset auto-generato da web.dmi.unict.it / public.smartedu.unict.it '
                f'AA {ACADEMIC_YEAR}. Include corsi, curricula, docenti e materie comuni.'
            ),
            'source': {
                'site': 'https://web.dmi.unict.it/',
                'api': 'https://public.smartedu.unict.it/CourseAPI',
                'academicYear': ACADEMIC_YEAR,
            },
        },
        'rooms': [],
        'teachers': [],
        'programs': [],
        'curricula': [],
        'courses': [],
        'unavailability': [],
        'softPolicy': {
            'weights': {
                'patternViolation': 1000,
                'curriculumGapPerHour': 10,
                'teacherConsecutiveOver3PerHour': 30,
                'teacherDailyOver5PerHour': 20,
                'lateStartPenalty': 3,
                'earlyStartPenalty': 2,
                'lunchOverlapPenalty': 200,
            },
            'preferredPatterns': {
                'twoEvents': ['Mon-Wed', 'Wed-Fri', 'Tue-Thu'],
                'threeEvents': ['Mon-Wed-Fri'],
            },
        },
    }

    teachers_map = {}
    courses_index = {}
    common_tracker = defaultdict(set)  # (programId, normalized code+name) -> curricula ids

    for course_stub in selected_courses:
        uid = course_stub.get('uid')
        details = fetch_json('/getCourse', {
            'mode': 'classRoom',
            'uid': uid,
            'code': '',
            'academicYear': ACADEMIC_YEAR,
            'curricula': None,
            'years': None,
            'iso': 'ita',
            'showCUINs': 'False',
        })
        data = details.get('data', {})

        program_name = text_it(course_stub.get('name'), text_it(data.get('name'), 'Corso'))
        class_code = ''
        classes = [str(x).strip() for x in (course_stub.get('classes') or [])]
        if classes:
            class_code = classes[0].replace(' R', '').strip()
        if not class_code:
            class_code = str(course_stub.get('code') or '').strip() or slug(program_name).upper()

        program_id = class_code.upper()
        db['programs'].append({
            'id': program_id,
            'name': f"{program_name} ({class_code})",
            'department': 'DMI',
            'type': 'Laurea Magistrale' if class_code.startswith('LM-') else 'Laurea Triennale',
            'classCode': class_code,
            'sourceUid': uid,
            'academicYear': ACADEMIC_YEAR,
        })

        cur_map = {}  # (uid,pathId) -> curriculum id
        for cur in data.get('curricula', []):
            cur_name = text_it(cur.get('name'), 'Curriculum')
            cur_id = curriculum_id(program_id, cur_name, cur.get('pathId'))
            if cur_id not in {c['id'] for c in db['curricula']}:
                db['curricula'].append({
                    'id': cur_id,
                    'programId': program_id,
                    'name': cur_name,
                    'yearCohort': str(ACADEMIC_YEAR),
                    'sourceUid': cur.get('uid'),
                    'pathId': cur.get('pathId'),
                })
            cur_map[(cur.get('uid'), cur.get('pathId'))] = cur_id

            for year in cur.get('years', []):
                year_num = int(year.get('number') or 1)
                for unit in year.get('units', []):
                    for activity in unit.get('activities', []):
                        if activity.get('type') != 'activity':
                            children = activity.get('children') or []
                        else:
                            children = []

                        all_items = [activity] + children
                        for item in all_items:
                            code = str(item.get('code') or '').strip()
                            name = text_it(item.get('name'), '').strip()
                            if not name:
                                continue
                            cid = course_id(program_id, code, name)

                            prof_names = []
                            for part in (item.get('partitions') or []):
                                for p in (part.get('professors') or []):
                                    tname = f"{str(p.get('lastName') or '').strip()} {str(p.get('name') or '').strip()}".strip()
                                    if tname:
                                        prof_names.append(tname)
                            prof_names = dedup_keep_order(prof_names)

                            t_ids = []
                            for tname in prof_names:
                                tid = teacher_id(tname)
                                if tid not in teachers_map:
                                    teachers_map[tid] = {
                                        'id': tid,
                                        'name': tname,
                                        'email': '',
                                        'preferences': {'avoidEarly': False, 'avoidLate': False},
                                    }
                                t_ids.append(tid)

                            row = courses_index.get(cid)
                            if row is None:
                                row = {
                                    'id': cid,
                                    'name': (f"{code} - {name}" if code else name),
                                    'programId': program_id,
                                    'roomType': 'lecture',
                                    'semester': 1 if year_num == 1 else 2,
                                    'curriculaIds': [],
                                    'teacherIds': [],
                                    'expectedStudents': 50 if class_code.startswith('L-') else 30,
                                    'patternPref': '',
                                    # Default event model: 1 event x 2h/week.
                                    'weeklyEvents': [{'durationHours': 2}],
                                    'sourceCode': code,
                                    'year': year_num,
                                }
                                courses_index[cid] = row

                            row['curriculaIds'] = dedup_keep_order(row['curriculaIds'] + [cur_id])
                            row['teacherIds'] = dedup_keep_order(row['teacherIds'] + t_ids)
                            if year_num < row.get('year', year_num):
                                row['year'] = year_num

                            key = (program_id, (code or name).upper())
                            common_tracker[key].add(cur_id)

    db['teachers'] = sorted(teachers_map.values(), key=lambda x: x['name'])

    for course in courses_index.values():
        key = (course['programId'], (course.get('sourceCode') or course['name']).upper())
        cset = sorted(common_tracker.get(key, set()))
        if len(cset) > 1:
            course['isCommonBetweenCurricula'] = True
            course['sharedWithCurriculaIds'] = cset
        db['courses'].append(course)

    db['programs'] = sorted(db['programs'], key=lambda x: x['id'])
    db['curricula'] = sorted(db['curricula'], key=lambda x: (x['programId'], x['name']))
    db['courses'] = sorted(db['courses'], key=lambda x: (x['programId'], x['name']))

    out_path = 'data/dmi_unict_catalog_2025_db.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print('written', out_path)
    print('programs', len(db['programs']))
    print('curricula', len(db['curricula']))
    print('teachers', len(db['teachers']))
    print('courses', len(db['courses']))
    shared = sum(1 for c in db['courses'] if c.get('isCommonBetweenCurricula'))
    print('common-courses', shared)


if __name__ == '__main__':
    main()
