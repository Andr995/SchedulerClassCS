"""
PDF Timetable Exporter (No LaTeX)
=================================
Genera un PDF dell'orario usando ReportLab.
"""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
DAY_IT = {
    'Mon': 'Lunedi',
    'Tue': 'Martedi',
    'Wed': 'Mercoledi',
    'Thu': 'Giovedi',
    'Fri': 'Venerdi',
}


def _apply_filters(assignments, db, filters):
    f_curr = filters.get('curriculum', '') if filters else ''
    f_teach = filters.get('teacher', '') if filters else ''
    f_room = filters.get('room', '') if filters else ''

    out = [a for a in assignments if a.get('day') != 'N/A']
    if f_curr:
        curr_courses = {
            c['id'] for c in db.get('courses', []) if f_curr in c.get('curriculaIds', [])
        }
        out = [a for a in out if a.get('courseId') in curr_courses]
    if f_teach:
        out = [a for a in out if f_teach in (a.get('teacherIds') or [])]
    if f_room:
        out = [a for a in out if a.get('roomId') == f_room]

    return out


def _build_curriculum_tables(assignments, db):
    courses_by_id = {c['id']: c for c in db.get('courses', [])}
    curricula = db.get('curricula', [])

    tables = []
    for curr in curricula:
        cid = curr.get('id', '')
        rows = []
        for a in assignments:
            course = courses_by_id.get(a.get('courseId', ''), {})
            if cid in course.get('curriculaIds', []):
                rows.append(a)

        rows.sort(key=lambda x: (DAYS.index(x['day']) if x.get('day') in DAYS else 99,
                                 x.get('startHour', 0),
                                 x.get('courseName', '')))

        tables.append({
            'curriculumId': cid,
            'curriculumName': curr.get('name', cid),
            'yearCohort': curr.get('yearCohort', ''),
            'rows': rows,
        })

    return tables


def export_pdf(schedule, db, filters=None):
    """Genera PDF in memoria e restituisce bytes."""
    assignments = _apply_filters(schedule.get('assignments', []), db, filters or {})
    report = schedule.get('hardConstraintReport', {})

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title='Orario Lezioni',
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'TitleSmall',
        parent=styles['Heading1'],
        fontSize=16,
        leading=20,
        spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        'Sub',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.HexColor('#333333'),
        spaceAfter=10,
    )
    small_style = ParagraphStyle(
        'Small',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
    )

    story = []
    status = schedule.get('status', 'N/A')
    solver = schedule.get('solverBackend', schedule.get('algorithmLabel', 'N/A'))
    sem = schedule.get('semester')
    sem_txt = f"{sem} semestre" if sem else 'tutti i semestri'

    story.append(Paragraph('Orario Lezioni - LM-18', title_style))
    story.append(Paragraph(
        f"Stato: <b>{status}</b> | Solver: <b>{solver}</b> | Semestre: <b>{sem_txt}</b> | "
        f"Lezioni: <b>{len(assignments)}</b> | Tempo: <b>{schedule.get('solveTimeSeconds', 0)}s</b>",
        subtitle_style
    ))

    checks = report.get('checks', [])
    if checks:
        story.append(Paragraph('Log vincoli hard', styles['Heading3']))
        log_rows = [['Vincolo', 'Esito', 'Violazioni']]
        for c in checks:
            log_rows.append([
                c.get('label', c.get('id', '')),
                'OK' if c.get('respected') else 'KO',
                str(c.get('violations', 0)),
            ])

        log_table = Table(log_rows, colWidths=[120 * mm, 25 * mm, 30 * mm])
        log_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#efefef')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#b0b0b0')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ])
        for r in range(1, len(log_rows)):
            if log_rows[r][1] == 'OK':
                log_style.add('TEXTCOLOR', (1, r), (1, r), colors.HexColor('#0a7f3f'))
            else:
                log_style.add('TEXTCOLOR', (1, r), (1, r), colors.HexColor('#b00020'))
        log_table.setStyle(log_style)
        story.append(log_table)
        story.append(Spacer(1, 5 * mm))

    tables = _build_curriculum_tables(assignments, db)
    if not tables:
        story.append(Paragraph('Nessun curriculum disponibile.', styles['Normal']))

    for idx, table_data in enumerate(tables):
        title = f"Curriculum: {table_data['curriculumName']}"
        if table_data.get('yearCohort'):
            title += f" ({table_data['yearCohort']})"
        story.append(Paragraph(title, styles['Heading3']))

        rows = [['Giorno', 'Ora', 'Insegnamento', 'Aula', 'Docenti']]
        for a in table_data['rows']:
            teachers = ', '.join(a.get('teacherNames', []) or a.get('teacherIds', []))
            rows.append([
                DAY_IT.get(a.get('day', ''), a.get('day', '')),
                f"{a.get('startHour', '')}:00-{a.get('endHour', '')}:00",
                a.get('courseName', ''),
                a.get('roomName', ''),
                teachers,
            ])

        if len(rows) == 1:
            rows.append(['-', '-', 'Nessuna lezione per questo curriculum', '-', '-'])

        t = Table(rows, colWidths=[30 * mm, 28 * mm, 90 * mm, 42 * mm, 80 * mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ececec')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#b0b0b0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t)

        if idx < len(tables) - 1:
            story.append(Spacer(1, 6 * mm))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f"Generato il {schedule.get('timestamp', '')}",
        small_style
    ))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
