import re
import sys
import io
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from collections import Counter
import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app

BASE = 'https://web.dmi.unict.it'
PROGRAM_PAGES = [
    'https://web.dmi.unict.it/corsi/l-31/programmi',
    'https://web.dmi.unict.it/corsi/lm-18/programmi',
    'https://web.dmi.unict.it/corsi/l-35/programmi',
    'https://web.dmi.unict.it/corsi/lm-40/programmi',
]

L31_PID = '66526837'
L35_PID = '68829610'
LM18_PID = '31569366'
LM40_PID = '39325618'
LMDATA_PID = '88927583'

LOCAL_PDF_SOURCES = {
    L31_PID: '/home/andrea/Scaricati/L 31 R_Informatica_finale.pdf',
    L35_PID: '/home/andrea/Scaricati/format_nuovo_2025_2026_approvato_corretto.pdf',
    LM40_PID: '/home/andrea/Scaricati/LM 40 R_Matematica.pdf',
    LMDATA_PID: '/home/andrea/Scaricati/LM Data Data science.pdf',
}

REMOTE_PDF_FALLBACKS = {
    L31_PID: 'https://web.dmi.unict.it/sites/default/files/L%2031%20R_Informatica_finale.pdf',
    L35_PID: 'https://web.dmi.unict.it/sites/default/files/format_nuovo_2025_2026_approvato_corretto.pdf',
    LM40_PID: 'https://web.dmi.unict.it/sites/default/files/documenti_sito/LM%2040%20R_Matematica.pdf',
}

session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0'})


def nrm(s):
    t = app._normalize_course_name_for_match(s)
    t = re.sub(r'^\d{4,}\s*-\s*', '', t).strip()
    t = t.replace(' e laboratorio', ' laboratorio')
    t = t.replace(' and laboratory', ' laboratorio')
    t = t.replace(' e lab ', ' laboratorio ')
    t = t.replace(' lab ', ' laboratorio ')
    t = re.sub(r'\b[a-z]\s+[a-z]\b$', '', t).strip()
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def parse_cfu(html):
    pats = [
        r'\b(\d{1,2})\s*cfu\b',
        r'crediti\s*formativi\s*universitari\s*[:\-]?\s*(\d{1,2})',
        r'numero\s+crediti\s*[:\-]?\s*(\d{1,2})',
        r'crediti\s*[:\-]?\s*(\d{1,2})',
    ]
    for p in pats:
        m = re.search(p, html, flags=re.IGNORECASE)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 24:
                return v
    return 0


def _add_catalog_entry(catalog, name, cfu):
    if not name:
        return
    try:
        val = float(cfu)
    except Exception:
        return
    if val <= 0 or val > 30:
        return
    key = nrm(name)
    if not key:
        return
    old = catalog.get(key, 0)
    if val > old:
        catalog[key] = val


def _extract_pdf_lines(local_path, fallback_url):
    lines = []
    try:
        if local_path and Path(local_path).exists():
            with pdfplumber.open(local_path) as pdf:
                for pg in pdf.pages:
                    txt = pg.extract_text() or ''
                    lines.extend(' '.join(x.split()) for x in txt.splitlines() if x.strip())
            return lines
    except Exception:
        pass

    if fallback_url:
        try:
            raw = session.get(fallback_url, timeout=30).content
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for pg in pdf.pages:
                    txt = pg.extract_text() or ''
                    lines.extend(' '.join(x.split()) for x in txt.splitlines() if x.strip())
        except Exception:
            return []
    return lines


def _catalog_from_l31_page():
    out = {}
    try:
        psoup = BeautifulSoup(
            session.get('https://web.dmi.unict.it/it/corsi/l-31/piani-di-studio', timeout=20).text,
            'html.parser',
        )
        for tr in psoup.find_all('tr'):
            cols = tr.find_all('td')
            if len(cols) < 4:
                continue
            raw_name = cols[2].get_text(' ', strip=True)
            raw_cfu = cols[3].get_text(' ', strip=True)
            if not raw_name or not raw_cfu:
                continue
            if re.search(r'anno|periodo|curriculum|denominazione', raw_name, re.IGNORECASE):
                continue
            nums = [int(x) for x in re.findall(r'\d{1,2}', raw_cfu)]
            cfu = sum(nums) if nums else 0
            _add_catalog_entry(out, raw_name, cfu)
    except Exception:
        return {}
    return out


def _catalog_from_lm18_coorte_pages():
    out = {}
    urls = [
        'https://web.dmi.unict.it/it/corsi/lm-18/piani-di-studio-coorte-202425',
        'https://web.dmi.unict.it/it/corsi/lm-18/piani-di-studio-coorte-202324',
    ]
    for url in urls:
        try:
            soup = BeautifulSoup(session.get(url, timeout=20).text, 'html.parser')
        except Exception:
            continue

        for tr in soup.find_all('tr'):
            cols = [td.get_text(' ', strip=True) for td in tr.find_all('td')]
            if len(cols) < 2:
                continue

            # LM-18 coorte pages are typically structured as [name, cfu, name, cfu].
            for i in (0, 2):
                if i + 1 >= len(cols):
                    continue
                name = cols[i]
                cfu_txt = cols[i + 1]
                nums = [int(x) for x in re.findall(r'\d{1,2}', cfu_txt)]
                cfu = sum(nums) if nums else 0
                if cfu <= 0:
                    continue
                if re.search(
                    r'curriculum|primo\s+anno|secondo\s+anno|primo\s+semestre|secondo\s+semestre|opzionale|insegnamento\s+a\s+scelta|tesi|cfu\s+liberi',
                    name,
                    re.IGNORECASE,
                ):
                    continue
                _add_catalog_entry(out, name, cfu)
    return out


def _catalog_from_l31_pdf(lines):
    out = {}
    for ln in lines:
        m = re.match(
            r'^\d+\s+(?:[A-Z0-9\-/]+|-)\s+\u2022?\s*(.+?)\s+(\d{1,2}(?:\.\d+)?)\s+(?:\d+|-)\s+(?:\d+|-)\s+[A-Z\-]$',
            ln,
            flags=re.IGNORECASE,
        )
        if not m:
            continue
        name = m.group(1).strip(' :;-')
        cfu = m.group(2)
        if not name or len(name) < 4:
            continue
        _add_catalog_entry(out, name, cfu)
    return out


def _catalog_from_l35_pdf(lines):
    out = {}
    seen = {}
    for ln in lines:
        m = re.match(
            r'^\d+\s+(?:[A-Z]{2,}\s*\d{2}|---)\s+(.+?)\s+(\d{1,2}(?:\.\d+)?)\s+\(F\)',
            ln,
            flags=re.IGNORECASE,
        )
        if not m:
            continue
        name = m.group(1).strip(' :;-')
        key = nrm(name)
        if not key:
            continue
        cfu = float(m.group(2))
        seen.setdefault(key, []).append(cfu)

    for key, vals in seen.items():
        best = max(vals)
        # Many L-35 rows split annual courses in two 7.5-CFU halves.
        if best < 1 or best > 30:
            continue
        if abs(best - round(best)) > 1e-9 and len(vals) >= 2:
            merged = round(sum(vals), 1)
            if 1 <= merged <= 30 and abs(merged - round(merged)) < 1e-9:
                out[key] = max(out.get(key, 0), merged)
                continue
        out[key] = max(out.get(key, 0), best)
    return out


def _catalog_from_lm40_pdf(lines):
    out = {}
    for ln in lines:
        m = re.match(
            r'^(?:\d+\s+)?[A-Z]{2,}(?:[\-/][A-Z0-9]+)+\s+(.+?)\s+(\d{1,2}(?:\.\d+)?)\s+L\+E\b',
            ln,
            flags=re.IGNORECASE,
        )
        if not m:
            continue
        name = m.group(1).strip(' :;-')
        cfu = m.group(2)
        _add_catalog_entry(out, name, cfu)
    return out


def _catalog_from_lmdata_pdf(lines):
    out = {}
    for ln in lines:
        m = re.match(
            r'^\d+\s+(?:[A-Z0-9]+[\-/][A-Z0-9]+|[A-Z0-9\-/]+)?\s*\u2022?\s*(.+?)\s+(\d{1,2}(?:\.\d+)?)\s+\([fl]\)',
            ln,
            flags=re.IGNORECASE,
        )
        if not m:
            continue
        name = m.group(1).strip(' :;-')
        cfu = m.group(2)
        if re.search(r'curriculum|periodo|gruppo|denominazione|frequenza|didattica', name, re.IGNORECASE):
            continue
        _add_catalog_entry(out, name, cfu)
    return out


def _lookup_by_similarity(key, catalog):
    if not key:
        return 0
    if key in catalog:
        return catalog[key]

    key_tokens = set(key.split())
    best_v = 0
    best_s = 0.0
    best_count = 0

    for k, v in catalog.items():
        if key in k or k in key:
            score = min(len(key), len(k)) / max(len(key), len(k))
        else:
            ks = set(k.split())
            if not ks or not key_tokens:
                continue
            score = len(ks & key_tokens) / len(ks | key_tokens)
        if score > best_s:
            best_s = score
            best_v = v
            best_count = 1
        elif abs(score - best_s) < 1e-9:
            best_count += 1

    # conservative: only use unambiguous and strong similarities
    if best_s >= 0.80 and best_count == 1:
        return best_v
    return 0


def run():
    code_index = {}
    for page in PROGRAM_PAGES:
        try:
            soup = BeautifulSoup(session.get(page, timeout=15).text, 'html.parser')
        except Exception:
            continue

        for row in soup.find_all('tr')[1:]:
            cols = row.find_all('td')
            if not cols:
                continue
            text = cols[0].get_text(' ', strip=True)
            m = re.match(r'\s*(\d{4,})\s*-\s*(.+)', text)
            if not m:
                continue
            code = m.group(1).strip()
            name = m.group(2).strip()
            a = cols[0].find('a', href=True)
            if not a:
                continue
            href = a['href']
            full = href if href.startswith('http') else BASE + href
            code_index.setdefault(code, (name, full))

    db = app.load_db()
    zeros = [c for c in db.get('courses', []) if int(c.get('cfu', 0) or 0) <= 0]
    needed_codes = set()
    for c in zeros:
        code = str(c.get('sourceCode', '') or '').strip()
        if not code:
            m = re.match(r'^\s*(\d{4,})\s*-\s*', str(c.get('name', '')))
            if m:
                code = m.group(1)
        if code:
            needed_codes.add(code)

    catalog_by_code = {}
    catalog_by_name = {}
    errors = 0

    for code in sorted(needed_codes):
        item = code_index.get(code)
        if not item:
            continue
        name, link = item
        try:
            html = session.get(link, timeout=10).text
        except Exception:
            errors += 1
            continue
        cfu = parse_cfu(html)
        if cfu > 0:
            catalog_by_code[code] = max(catalog_by_code.get(code, 0), cfu)
            key = nrm(name)
            if key:
                catalog_by_name[key] = max(catalog_by_name.get(key, 0), cfu)

    # Program-specific catalogs from official L-31 page and attached PDF files.
    by_program_name = {
        L31_PID: _catalog_from_l31_page(),
        LM18_PID: _catalog_from_lm18_coorte_pages(),
        L35_PID: {},
        LM40_PID: {},
        LMDATA_PID: {},
    }

    for pid in [L31_PID, L35_PID, LM40_PID, LMDATA_PID]:
        lines = _extract_pdf_lines(LOCAL_PDF_SOURCES.get(pid), REMOTE_PDF_FALLBACKS.get(pid))
        if not lines:
            continue
        if pid == L31_PID:
            parsed = _catalog_from_l31_pdf(lines)
        elif pid == L35_PID:
            parsed = _catalog_from_l35_pdf(lines)
        elif pid == LM40_PID:
            parsed = _catalog_from_lm40_pdf(lines)
        else:
            parsed = _catalog_from_lmdata_pdf(lines)

        prog_cat = by_program_name.setdefault(pid, {})
        for k, v in parsed.items():
            if v > prog_cat.get(k, 0):
                prog_cat[k] = v

    for pid, cmap in by_program_name.items():
        for k, v in cmap.items():
            if v > catalog_by_name.get(k, 0):
                catalog_by_name[k] = v

    updated = 0
    by_code = 0
    by_name = 0
    by_program_catalog = 0
    by_similarity = 0
    for c in db.get('courses', []):
        if int(c.get('cfu', 0) or 0) > 0:
            continue
        code = str(c.get('sourceCode', '') or '').strip()
        if not code:
            m = re.match(r'^\s*(\d{4,})\s*-\s*', str(c.get('name', '')))
            if m:
                code = m.group(1)
        new = 0
        if code and code in catalog_by_code:
            new = catalog_by_code[code]
            by_code += 1
        else:
            key = nrm(c.get('name', ''))
            pid = str(c.get('programId', '') or '').strip()
            prog_cat = by_program_name.get(pid, {})
            if key in prog_cat:
                new = prog_cat[key]
                by_program_catalog += 1
            elif key in catalog_by_name:
                new = catalog_by_name[key]
                by_name += 1
            else:
                s = _lookup_by_similarity(key, prog_cat) or _lookup_by_similarity(key, catalog_by_name)
                if s > 0:
                    new = s
                    by_similarity += 1
        if new > 0:
            c['cfu'] = int(new) if abs(new - round(new)) < 1e-9 else round(new, 1)
            updated += 1

    if updated:
        app.save_db(db)

    z = [c for c in db.get('courses', []) if int(c.get('cfu', 0) or 0) <= 0]
    programs = {str(p.get('id', '')).strip(): str(p.get('name', '')).strip() for p in db.get('programs', [])}
    cnt = Counter()
    for c in z:
        pid = str(c.get('programId', '')).strip()
        cnt[f"{pid} | {programs.get(pid, '?')}"] += 1

    print('zeros_before', len(zeros))
    print('needed_codes', len(needed_codes))
    print('catalog_by_code', len(catalog_by_code))
    print('catalog_by_name', len(catalog_by_name))
    print('program_catalog_sizes', {k: len(v) for k, v in by_program_name.items()})
    print('fetch_errors', errors)
    print(
        'updated',
        updated,
        'by_code',
        by_code,
        'by_name',
        by_name,
        'by_program_catalog',
        by_program_catalog,
        'by_similarity',
        by_similarity,
    )
    print('zeros_after', len(z))
    print('remaining_by_program')
    for k, v in cnt.most_common():
        print(v, '::', k)


if __name__ == '__main__':
    run()
