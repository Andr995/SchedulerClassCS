# University Timetabling System

Applicazione web (Flask) per gestire dati accademici e generare automaticamente l'orario universitario con vincoli hard/soft.

## Novita recenti

- Login admin multiutente con password hashate (`scrypt`) e gestione utenti da pannello admin.
- Import/Export bundle unico `DB + orario` (`database_con_orario.json`).
- Import di JSON esterni con normalizzazione schema e report dettagliato (duplicati/scartati).
- Import da URL lato backend (`Aggiungi da URL`), con scraping server-side e deduplica automatica.
- Fallback solver automatico: se algoritmo richiesto e `infeasible`, tenta `greedy` e puo restituire `partial`.

## Funzionalita principali

- CRUD completo su aule, docenti, CdS, curricula, insegnamenti, indisponibilita e policy.
- Generazione orario globale/semestre e rigenerazione per singolo `CdL + anno`.
- Modifica manuale orario e salvataggio persistente.
- Export PDF orario, export JSON orario, export bundle DB+orario.
- Import DB completo, bundle DB+orario, JSON esterni eterogenei, import da URL.

## Struttura progetto

```text
.
├── app.py
├── scheduler.py
├── pdf_export.py
├── latex_export.py
├── templates/
│   ├── admin.html
│   ├── admin_login.html
│   └── index.html
├── data/
│   ├── database.json
│   ├── last_schedule.json
│   └── users.json
├── script/
│   └── *.py (scraper di supporto)
├── requirements.txt
└── README.md
```

## Requisiti

- Python 3.10+ (consigliato 3.12)
- `pip`
- Connessione internet per `Importa da URL`

Dipendenze principali (`requirements.txt`):

- Flask
- ortools
- reportlab
- requests
- beautifulsoup4

## Tutorial installazione ed esecuzione

## Linux

1. Apri terminale nella cartella progetto.
2. Crea virtual environment.
3. Installa dipendenze.
4. Avvia server.

```bash
cd /percorso/del/progetto
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

## macOS

1. Apri Terminal.
2. Vai nella cartella del progetto.
3. Crea e attiva virtual environment.
4. Installa dipendenze e avvia.

```bash
cd /percorso/del/progetto
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Nota macOS: se `python3` non è disponibile, installa Python dal sito ufficiale o via Homebrew.

## Windows (PowerShell)

1. Apri PowerShell nella cartella progetto.
2. Crea virtual environment.
3. Attiva virtual environment.
4. Installa dipendenze e avvia.

```powershell
cd C:\percorso\del\progetto
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Se PowerShell blocca script locali, esegui una volta:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Avvio e accesso

- Server: `http://127.0.0.1:5000`
- Admin: `http://127.0.0.1:5000/admin`

Bootstrap iniziale (se non esiste `data/users.json`):

- Username: `admin`
- Password: `admin`

Variabili ambiente opzionali:

- `DEFAULT_ADMIN_USERNAME`
- `DEFAULT_ADMIN_PASSWORD`
- `FLASK_SECRET_KEY`

## Import da URL (backend Flask)

Il pulsante `Importa da URL` usa endpoint backend `POST /api/import/url` e non scraping browser.

Sezioni supportate:

- `Docenti`
- `CdS`
- `Curricula`
- `Insegnamenti`

Sorgenti usate per `Docenti`:

- `https://web.dmi.unict.it/docenti`
- `https://web.dmi.unict.it/it/assegnisti-di-ricerca`
- `https://web.dmi.unict.it/elenchi/contrattisti-di-ricerca`
- `https://web.dmi.unict.it/dottorandi`
- `https://web.dmi.unict.it/personale-ta`

Il merge applica deduplica e ritorna un report con `added`, `updated`, `duplicates`, `skipped`.

## Import/Export JSON

- `Esporta DB` genera bundle `database_con_orario.json` con:
  - `database`
  - `schedule`
  - metadati export
- Import dello stesso bundle: ripristina DB + ultimo orario in un solo passaggio.
- Import JSON esterni: normalizza schemi non omogenei (docenti/assegnisti/contrattisti/dottorandi/insegnamenti, ecc.).

## Algoritmi disponibili

Parametro `algorithm` in `POST /api/schedule`:

- `auto`
- `cp-sat`
- `greedy`
- `genetic`
- `tabu-search`
- `linear-programming`

Alias supportati: `cp`, `constraints`, `tabu`, `lp`, `mip`, ecc.

## Spiegazione rapida

- `auto`: usa backend migliore disponibile.
- `cp-sat`: migliore qualita media su vincoli complessi.
- `greedy`: piu rapido, utile per test e fallback.
- `genetic`: metaeuristica esplorativa.
- `tabu-search`: metaeuristica con memoria tabu.
- `linear-programming`: formulazione lineare intera (backend OR-Tools).

## Stato solver e fallback

Status tipici: `optimal`, `feasible`, `partial`, `infeasible`, `error`, `none`.

Se algoritmo richiesto produce `infeasible`, il backend attiva fallback automatico su `greedy` e puo restituire soluzione `partial`.

## API principali

## UI/Auth

- `GET /`
- `GET /admin`
- `POST /admin/login`
- `POST /admin/logout`

## Utenti admin

- `GET /api/users`
- `POST /api/users`
- `POST /api/users/<username>/password`
- `DELETE /api/users/<username>`

Regole:

- non puoi eliminare l'utente loggato
- deve esistere almeno un admin attivo

## Dati e scheduling

- `GET /api/db`
- `POST /api/db`
- `POST /api/import/url`
- `POST /api/schedule`
- `POST /api/schedule/program-year`
- `GET /api/schedule`
- `POST /api/schedule/import`
- `POST /api/schedule/manual-update`
- `GET /api/public/timetable`

## Export

- `GET /api/export/flat`
- `GET /api/export/schedule-json`
- `GET /api/export/pdf`

## Sicurezza

- Password sempre hashate (`scrypt`, Werkzeug).
- Sessione admin lato Flask.
- Policy password robusta in creazione/reset utenti.

## Note per produzione

- Usa server WSGI/ASGI (es. `gunicorn`) dietro reverse proxy.
- Imposta `FLASK_SECRET_KEY` robusta.
- Valuta DB relazionale invece di file JSON.
- Aggiungi rate limit login, audit log e backup pianificati.
