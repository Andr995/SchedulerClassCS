async function fetchJson(path, payload) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json"
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`);
  }
  return await response.json();
}
function textIt(obj, defaultValue = "") {
  if (Array.isArray(obj)) {
    // Prima ricerca: item con iso === "ita" e text presente
    for (const item of obj) {
      if (item && typeof item === "object" && item.iso === "ita" && item.text) {
        return String(item.text);
      }
    }
    // Seconda ricerca: qualsiasi item con text presente
    for (const item of obj) {
      if (item && typeof item === "object" && item.text) {
        return String(item.text);
      }
    }
  }
  return defaultValue;
}

function slug(s) {
  s = String(s || "");
  // Normalizza e rimuove i caratteri combinanti (accenti)
  s = s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  // Minuscole
  s = s.toLowerCase();
  // Sostituisce tutto ciò che non è a-z o 0-9 con "-"
  s = s.replace(/[^a-z0-9]+/g, "-");
  // Rimuove trattini multipli e quelli ai bordi
  s = s.replace(/-+/g, "-").replace(/^-|-$/g, "");
  // Limite a 48 caratteri
  return s.slice(0, 48);
}

function teacherId(name) {
  return (`T-${slug(name)}`).toUpperCase().slice(0, 64);
}

function curriculumId(programId, curriculumName, pathId) {
  const base =
    slug(curriculumName) ||
    slug(pathId) ||
    "curriculum";

  return (`CUR-${programId}-${base}`).toUpperCase().slice(0, 72);
}

function courseId(programId, code, name) {
  const codeText = String(code || "").trim();
  if (codeText) {
    return codeText;
  }

  const nameText = String(name || "").trim();
  return slug(nameText).toUpperCase().slice(0, 72);
}

function dedupKeepOrder(items) {
  const out = [];
  const seen = new Set();

  for (const x of items) {
    if (x && !seen.has(x)) {
      seen.add(x);
      out.push(x);
    }
  }

  return out;
}
async function main() {
  const listing = await fetch_json("/getDepartmentsAndCourses", {
    mode: "classRoomList",
    academicYear: ACADEMIC_YEAR,
    courseTypes: ["CorsoDiStudio"]
  });

  let dmi_struct = null;
  for (const struct of listing?.data?.data || []) {
    if (String(struct.code || "").trim() === STRUCTURE_CODE) {
      dmi_struct = struct;
      break;
    }
  }

  if (!dmi_struct) {
    console.error("Struttura DMI non trovata su getDepartmentsAndCourses");
    process.exit(1);
  }

  let selected_courses = [];
  for (const c of dmi_struct.courses || []) {
    const classes = (c.classes || []).map(x => String(x).trim());
    if (classes.some(x => x.startsWith("DOTT"))) continue;
    selected_courses.push(c);
  }

  const wanted_classes = new Set(["L-31", "L-35", "LM-18", "LM-40", "LM-DATA"]);
  const filtered = [];

  for (const c of selected_courses) {
    const classes = (c.classes || [])
      .map(x => String(x).toUpperCase().replace(" R", "").trim());
    if (classes.some(cls => wanted_classes.has(cls))) {
      filtered.push(c);
    }
  }

  if (filtered.length > 0) {
    selected_courses = filtered;
  }

  const db = {
    meta: {
      version: 1,
      timeModel: {
        dayStart: 8,
        dayEnd: 19,
        lunchStart: 13,
        lunchEnd: 14,
        granularityHours: 1
      },
      note:
        `Dataset auto-generato da web.dmi.unict.it / public.smartedu.unict.it ` +
        `AA ${ACADEMIC_YEAR}. Include corsi, curricula, docenti e materie comuni.`,
      source: {
        site: "https://web.dmi.unict.it/",
        api: "https://public.smartedu.unict.it/CourseAPI",
        academicYear: ACADEMIC_YEAR
      }
    },
    rooms: [],
    teachers: [],
    programs: [],
    curricula: [],
    courses: [],
    unavailability: [],
    softPolicy: {
      weights: {
        patternViolation: 1000,
        curriculumGapPerHour: 10,
        teacherConsecutiveOver3PerHour: 30,
        teacherDailyOver5PerHour: 20,
        lateStartPenalty: 3,
        earlyStartPenalty: 2,
        lunchOverlapPenalty: 200
      },
      preferredPatterns: {
        twoEvents: ["Mon-Wed", "Wed-Fri", "Tue-Thu"],
        threeEvents: ["Mon-Wed-Fri"]
      }
    }
  };

  const teachers_map = {};
  const courses_index = {};
  const common_tracker = new Map();

  for (const course_stub of selected_courses) {
    const uid = course_stub.uid;

    const details = await fetch_json("/getCourse", {
      mode: "classRoom",
      uid,
      code: "",
      academicYear: ACADEMIC_YEAR,
      curricula: null,
      years: null,
      iso: "ita",
      showCUINs: "False"
    });

    const data = details.data || {};

    const program_name = text_it(course_stub.name, text_it(data.name, "Corso"));

    let class_code = "";
    const classes = (course_stub.classes || []).map(x => String(x).trim());
    if (classes.length > 0) {
      class_code = classes[0].replace(" R", "").trim();
    }
    if (!class_code) {
      class_code =
        String(course_stub.code || "").trim() ||
        slug(program_name).toUpperCase();
    }

    const program_id = class_code.toUpperCase();

    db.programs.push({
      id: program_id,
      name: `${program_name} (${class_code})`,
      department: "DMI",
      type: class_code.startsWith("LM-")
        ? "Laurea Magistrale"
        : "Laurea Triennale",
      classCode: class_code,
      sourceUid: uid,
      academicYear: ACADEMIC_YEAR
    });

    const cur_map = {};

    for (const cur of data.curricula || []) {
      const cur_name = text_it(cur.name, "Curriculum");
      const cur_id = curriculum_id(program_id, cur_name, cur.pathId);

      if (!db.curricula.some(c => c.id === cur_id)) {
        db.curricula.push({
          id: cur_id,
          programId: program_id,
          name: cur_name,
          yearCohort: String(ACADEMIC_YEAR),
          sourceUid: cur.uid,
          pathId: cur.pathId
        });
      }

      cur_map[`${cur.uid}|${cur.pathId}`] = cur_id;

      for (const year of cur.years || []) {
        const year_num = parseInt(year.number || 1);

        for (const unit of year.units || []) {
          for (const activity of unit.activities || []) {
            const children =
              activity.type !== "activity"
                ? activity.children || []
                : [];

            const all_items = [activity, ...children];

            for (const item of all_items) {
              const code = String(item.code || "").trim();
              const name = text_it(item.name, "").trim();
              if (!name) continue;

              const cid = course_id(program_id, code, name);

              let prof_names = [];
              for (const part of item.partitions || []) {
                for (const p of part.professors || []) {
                  const tname =
                    `${String(p.lastName || "").trim()} ` +
                    `${String(p.name || "").trim()}`.trim();
                  if (tname) prof_names.push(tname);
                }
              }
              prof_names = dedup_keep_order(prof_names);

              const t_ids = [];
              for (const tname of prof_names) {
                const tid = teacher_id(tname);
                if (!teachers_map[tid]) {
                  teachers_map[tid] = {
                    id: tid,
                    name: tname,
                    email: "",
                    preferences: {
                      avoidEarly: false,
                      avoidLate: false
                    }
                  };
                }
                t_ids.push(tid);
              }

              let row = courses_index[cid];
              if (!row) {
                row = {
                  id: cid,
                  name: code ? `${code} - ${name}` : name,
                  programId: program_id,
                  roomType: "lecture",
                  semester: year_num === 1 ? 1 : 2,
                  curriculaIds: [],
                  teacherIds: [],
                  expectedStudents: class_code.startsWith("L-") ? 50 : 30,
                  patternPref: "",
                  weeklyEvents: [{ durationHours: 2 }],
                  sourceCode: code,
                  year: year_num
                };
                courses_index[cid] = row;
              }

              row.curriculaIds = dedup_keep_order([
                ...row.curriculaIds,
                cur_id
              ]);

              row.teacherIds = dedup_keep_order([
                ...row.teacherIds,
                ...t_ids
              ]);

              if (year_num < row.year) {
                row.year = year_num;
              }

              const key = `${program_id}|${(code || name).toUpperCase()}`;
              if (!common_tracker.has(key)) {
                common_tracker.set(key, new Set());
              }
              common_tracker.get(key).add(cur_id);
            }
          }
        }
      }
    }
  }

  db.teachers = Object.values(teachers_map).sort((a, b) =>
    a.name.localeCompare(b.name)
  );

  for (const course of Object.values(courses_index)) {
    const key = `${course.programId}|${(course.sourceCode || course.name).toUpperCase()}`;
    const cset = Array.from(common_tracker.get(key) || []).sort();

    if (cset.length > 1) {
      course.isCommonBetweenCurricula = true;
      course.sharedWithCurriculaIds = cset;
    }

    db.courses.push(course);
  }

  db.programs.sort((a, b) => a.id.localeCompare(b.id));
  db.curricula.sort((a, b) =>
    a.programId === b.programId
      ? a.name.localeCompare(b.name)
      : a.programId.localeCompare(b.programId)
  );
  db.courses.sort((a, b) =>
    a.programId === b.programId
      ? a.name.localeCompare(b.name)
      : a.programId.localeCompare(b.programId)
  );

  const out_path = "data/dmi_unict_catalog_2025_db.json";
  fs.writeFileSync(out_path, JSON.stringify(db, null, 2), "utf8");

  console.log("written", out_path);
  console.log("programs", db.programs.length);
  console.log("curricula", db.curricula.length);
  console.log("teachers", db.teachers.length);
  console.log("courses", db.courses.length);

  const shared = db.courses.filter(c => c.isCommonBetweenCurricula).length;
  console.log("common-courses", shared);
}

main();