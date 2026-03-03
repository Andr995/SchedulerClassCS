"""
LaTeX Timetable Exporter
=========================
Genera un documento LaTeX con l'orario settimanale e lo compila in PDF.
"""

import os
import subprocess
import tempfile
from pathlib import Path

DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
DAY_LABELS = {
    'Mon': 'Lunedì', 'Tue': 'Martedì', 'Wed': 'Mercoledì',
    'Thu': 'Giovedì', 'Fri': 'Venerdì',
}

# ──────────────────────────────────────────────────────────────
# Colori HSL → RGB approssimato per xcolor
# ──────────────────────────────────────────────────────────────
def _hsl_to_rgb(h, s, l):
    """Converte HSL (0-360, 0-1, 0-1) in RGB (0-1, 0-1, 0-1)."""
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (r + m, g + m, b + m)


def _course_color_rgb(course_id):
    """Genera un colore RGB deterministico (stesso algoritmo del frontend)."""
    h = 0
    for ch in course_id:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hue = h % 360
    return _hsl_to_rgb(hue, 0.65, 0.55)


def _tex_escape(text):
    """Escapa caratteri speciali LaTeX."""
    if not text:
        return ''
    conv = {
        '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
        '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
        '^': r'\^{}', '\\': r'\textbackslash{}',
    }
    out = []
    for ch in str(text):
        out.append(conv.get(ch, ch))
    return ''.join(out)


# ──────────────────────────────────────────────────────────────
# Generazione sorgente LaTeX
# ──────────────────────────────────────────────────────────────
def generate_latex(schedule, db, filters=None):
    """Genera il sorgente .tex completo.

    Args:
        schedule: dict con 'assignments', 'semester', 'timestamp', ecc.
        db: database completo.
        filters: dict opzionale con chiavi 'curriculum', 'teacher', 'room'.

    Returns:
        Stringa con il contenuto del file .tex.
    """
    filters = filters or {}
    assignments = [a for a in (schedule.get('assignments') or []) if a.get('day') != 'N/A']

    # Applica filtri
    f_curr = filters.get('curriculum', '')
    f_teach = filters.get('teacher', '')
    f_room = filters.get('room', '')

    if f_curr:
        curr_courses = {c['id'] for c in db.get('courses', [])
                        if f_curr in c.get('curriculaIds', [])}
        assignments = [a for a in assignments if a['courseId'] in curr_courses]
    if f_teach:
        assignments = [a for a in assignments if f_teach in (a.get('teacherIds') or [])]
    if f_room:
        assignments = [a for a in assignments if a.get('roomId') == f_room]

    tm = db.get('meta', {}).get('timeModel', {})
    ds = tm.get('dayStart', 8)
    de = tm.get('dayEnd', 19)
    ls = tm.get('lunchStart', 13)
    le = tm.get('lunchEnd', 14)

    # Raccogli colori unici per i corsi
    course_ids = sorted({a['courseId'] for a in assignments})
    colors = {}
    for cid in course_ids:
        r, g, b = _course_color_rgb(cid)
        colors[cid] = f"course{cid.replace('-', '').replace('_', '')}"

    # Sottotitolo dai filtri
    subtitle_parts = []
    if f_curr:
        name = next((c.get('name', f_curr) for c in db.get('curricula', []) if c['id'] == f_curr), f_curr)
        subtitle_parts.append(f"Curriculum: {_tex_escape(name)}")
    if f_teach:
        name = next((t.get('name', f_teach) for t in db.get('teachers', []) if t['id'] == f_teach), f_teach)
        subtitle_parts.append(f"Docente: {_tex_escape(name)}")
    if f_room:
        name = next((r.get('name', f_room) for r in db.get('rooms', []) if r['id'] == f_room), f_room)
        subtitle_parts.append(f"Aula: {_tex_escape(name)}")
    subtitle = ' --- '.join(subtitle_parts) if subtitle_parts else 'Orario completo'

    sem = schedule.get('semester')
    sem_str = f"{sem}\\textdegree{{}} Semestre" if sem else "Tutti i semestri"
    timestamp = schedule.get('timestamp', '')

    # Statistiche
    placed = len(assignments)
    total_hours = sum(a.get('duration', 1) for a in assignments)
    solver = schedule.get('solverBackend', 'N/A')
    solve_time = schedule.get('solveTimeSeconds', 0)

    # ── Costruisci il documento ──
    lines = []
    lines.append(r"""\documentclass[a4paper,landscape,8pt]{extarticle}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[italian]{babel}
\usepackage{geometry}
\geometry{left=10mm,right=10mm,top=15mm,bottom=12mm}
\usepackage{tgpagella}           % Palatino-like (classico e leggibile)
\usepackage{booktabs}
\usepackage{colortbl}
\usepackage{xcolor}
\usepackage{array}
\usepackage{graphicx}
\usepackage{fancyhdr}
\usepackage{lastpage}
\usepackage{tabularx}
\usepackage{calc}
\usepackage{microtype}            % Micro-tipografia per testo più pulito

\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.3pt}
\renewcommand{\footrulewidth}{0.15pt}
\fancyfoot[C]{\small Pagina \thepage\ di \pageref{LastPage}}
\fancyfoot[R]{\small Generato il: """ + _tex_escape(timestamp) + r"""}
\fancyhead[L]{\small\textsc{Orario delle Lezioni}}
\fancyhead[R]{\small """ + sem_str + r"""}

\setlength{\parindent}{0pt}
\setlength{\tabcolsep}{3pt}
\renewcommand{\arraystretch}{1.15}

% Definizione colori corsi
""")

    for cid in course_ids:
        r, g, b = _course_color_rgb(cid)
        cname = colors[cid]
        lines.append(f"\\definecolor{{{cname}}}{{rgb}}{{{r:.3f},{g:.3f},{b:.3f}}}")
        lines.append(f"\\definecolor{{{cname}bg}}{{rgb}}{{{min(1, r*0.2+0.85):.3f},{min(1, g*0.2+0.85):.3f},{min(1, b*0.2+0.85):.3f}}}")

    lines.append(r"""
\begin{document}

% ── Titolo ──
\begin{center}
  {\Large\textsc{Orario delle Lezioni}}\\[6pt]
  {\large LM-18 Informatica}\\[4pt]
  {\small """ + subtitle + r""" \,---\, """ + sem_str + r"""}
\end{center}
\vspace{8pt}
""")

    # ── Tabella orario ──
    # Approccio senza \multirow: per eventi multi-ora la prima riga ha il
    # contenuto, le righe successive solo lo sfondo colorato.
    # Usiamo \cline selettivi per non tagliare a metà gli eventi.

    day_col_w = r'\dimexpr(\textwidth - 2.1cm)/5 - 2\tabcolsep\relax'
    col_spec = (
        r'>{\centering\arraybackslash}m{1.8cm}|'
        + '|'.join([r'>{\centering\arraybackslash}m{' + day_col_w + '}'] * 5)
    )
    lines.append(r'\begin{center}')
    lines.append(r'\begin{tabular}{|' + col_spec + r'|}')
    lines.append(r'\hline')

    # Header giorni
    header = r'\textsc{\small Ora}'
    for d in DAYS:
        header += f" & \\textsc{{\\small {DAY_LABELS[d]}}}"
    header += r' \\ \hline\hline'
    lines.append(header)

    # Pre-calcola: per ogni (day, hour) cosa mostrare
    # cell_info[(day, h)] = {'type': 'start'|'cont'|'lunch'|'empty', 'assignment': ...}
    cell_info = {}
    for a in assignments:
        day = a['day']
        sh = a['startHour']
        dur = a.get('duration', 1)
        cell_info[(day, sh)] = {'type': 'start', 'assignment': a}
        for dh in range(1, dur):
            cell_info[(day, sh + dh)] = {'type': 'cont', 'assignment': a}

    # Righe orarie
    for h in range(ds, de):
        is_lunch = ls <= h < le
        row_parts = [f"{{\\small {h}:00--{h+1}:00}}"]

        for d in DAYS:
            info = cell_info.get((d, h))
            if info and info['type'] == 'start':
                a = info['assignment']
                cid = a['courseId']
                cname = colors.get(cid, 'white')
                course_name = _tex_escape(a.get('courseName', ''))
                room_name = _tex_escape(a.get('roomName', ''))
                teacher_names = a.get('teacherNames') or []
                teachers = ', '.join(_tex_escape(t.split()[-1]) if t.strip() else '' for t in teacher_names)
                time_str = f"{a['startHour']}:00-{a['endHour']}:00"

                # Contenuto dentro \parbox per isolare \\ dalla tabella
                inner_lines = [
                    f"\\textcolor{{{cname}}}{{\\bfseries\\scriptsize {course_name}}}",
                    f"\\scriptsize {room_name}",
                ]
                if teachers:
                    inner_lines.append(f"\\scriptsize {teachers}")
                inner_lines.append(f"\\scriptsize {time_str}")
                inner = ' \\\\ '.join(inner_lines)
                parbox = f"\\parbox[c]{{\\dimexpr(\\textwidth-2.1cm)/5 - 6\\tabcolsep\\relax}}{{\\centering {inner}}}"

                row_parts.append(f"\\cellcolor{{{cname}bg}}{parbox}")

            elif info and info['type'] == 'cont':
                a = info['assignment']
                cid = a['courseId']
                cname = colors.get(cid, 'white')
                row_parts.append(f"\\cellcolor{{{cname}bg}}")

            elif is_lunch:
                row_parts.append(r'\cellcolor{gray!10}{\tiny\itshape Pausa pranzo}')
            else:
                row_parts.append('')

        # Riga con hline pieno (il colore di sfondo rende chiara la continuità)
        lines.append(' & '.join(row_parts) + r' \\ \hline')

    lines.append(r'\end{tabular}')
    lines.append(r'\end{center}')

    # ── Statistiche ──
    lines.append(r"""
\vspace{10pt}
\begin{center}
\begin{tabular}{llllll}
\toprule
\textbf{Lezioni} & \textbf{Ore totali} & \textbf{Solver} & \textbf{Tempo} & \textbf{Status} & \textbf{Penalità} \\
\midrule
""")
    status = schedule.get('status', 'N/A')
    objective = schedule.get('objective', 'N/A')
    lines.append(
        f"{placed} & {total_hours}h & {_tex_escape(solver)} & {solve_time}s "
        f"& {_tex_escape(status)} & {objective} \\\\"
    )
    lines.append(r"""
\bottomrule
\end{tabular}
\end{center}
""")

    # ── Legenda colori ──
    if len(course_ids) > 0:
        lines.append(r"\vspace{8pt}")
        lines.append(r"\begin{center}")
        lines.append(r"\textsc{Legenda corsi}\\[4pt]")
        lines.append(r"\begin{tabular}{ll@{\qquad}ll@{\qquad}ll}")
        legend_items = []
        for cid in course_ids:
            cname = colors[cid]
            course_obj = next((c for c in db.get('courses', []) if c['id'] == cid), {})
            label = _tex_escape(course_obj.get('name', cid))
            legend_items.append(
                f"\\colorbox{{{cname}bg}}{{\\textcolor{{{cname}}}{{\\rule{{8pt}}{{8pt}}}}}} & "
                f"{{\\small {label}}}"
            )
        # Disponi in 3 colonne
        while len(legend_items) % 3 != 0:
            legend_items.append("&")
        for i in range(0, len(legend_items), 3):
            lines.append(' & '.join(legend_items[i:i+3]) + r' \\')
        lines.append(r"\end{tabular}")
        lines.append(r"\end{center}")

    lines.append(r"""
\end{document}
""")

    return '\n'.join(lines)


# ──────────────────────────────────────────────────────────────
# Compilazione PDF
# ──────────────────────────────────────────────────────────────
def compile_pdf(tex_source):
    """Compila il sorgente LaTeX e restituisce il contenuto del PDF.

    Args:
        tex_source: stringa con il contenuto del file .tex.

    Returns:
        bytes del file PDF compilato.

    Raises:
        RuntimeError: se la compilazione fallisce.
    """
    with tempfile.TemporaryDirectory(prefix='timetable_') as tmpdir:
        tex_path = os.path.join(tmpdir, 'timetable.tex')
        pdf_path = os.path.join(tmpdir, 'timetable.pdf')
        log_path = os.path.join(tmpdir, 'timetable.log')

        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(tex_source)

        # Compila 2 volte per riferimenti incrociati (LastPage)
        for run in range(2):
            result = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', '-halt-on-error',
                 '-output-directory', tmpdir, tex_path],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 and run == 1:
                log_content = ''
                if os.path.exists(log_path):
                    with open(log_path, 'r', encoding='utf-8', errors='replace') as lf:
                        log_content = lf.read()[-2000:]
                raise RuntimeError(
                    f"Compilazione LaTeX fallita (exit {result.returncode}).\n"
                    f"--- stdout ---\n{result.stdout[-1000:]}\n"
                    f"--- log (ultimi 2000 char) ---\n{log_content}"
                )

        if not os.path.exists(pdf_path):
            raise RuntimeError("Il file PDF non è stato generato.")

        with open(pdf_path, 'rb') as f:
            return f.read()


def export_pdf(schedule, db, filters=None):
    """Pipeline completa: genera LaTeX → compila → restituisce PDF bytes."""
    tex = generate_latex(schedule, db, filters)
    return compile_pdf(tex)
