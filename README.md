# SchedulerClassCS

# University Timetabling System

Applicazione web per la generazione automatica dell'orario universitario con ottimizzazione vincolata.

## Panoramica

Il progetto implementa una pipeline completa:

1. gestione dati accademici (aule, docenti, corsi, curricula, vincoli),
2. generazione orario con solver CP-SAT (OR-Tools),
3. fallback euristico greedy se OR-Tools non è disponibile,
4. esportazione in JSON flat, sorgente LaTeX e PDF.

L'app è pensata per uso locale/laboratorio e persiste i dati in file JSON nella cartella `data/`.

## Struttura del progetto

```
├── app.py                 # Backend Flask + API REST + persistenza JSON
├── scheduler.py           # Motore scheduling (CP-SAT + fallback greedy)
├── latex_export.py        # Generazione .tex e compilazione PDF
├── templates/
│   └── index.html         # Frontend single-page (HTML/CSS/JS vanilla)
├── data/
│   ├── database.json      # Database applicativo
│   └── last_schedule.json # Ultimo risultato generato
├── Classes.html           # Prototipo storico UI (riferimento)
├── requirements.txt       # Dipendenze Python
└── README.md
```

## Architettura

### 1) Frontend (`templates/index.html`)
- Interfaccia web unica (SPA light) per CRUD e generazione orario.
- Invoca API REST del backend.
- Visualizza griglia settimanale, stato solver, statistiche.

### 2) Backend (`app.py`)
- Espone endpoint HTTP per dati e scheduling.
- Carica/salva JSON su disco (`database.json`, `last_schedule.json`).
- Orchestration: prepara input, invoca `scheduler.solve(...)`, arricchisce output con metadati.
- Espone export flat/LaTeX/PDF.

### 3) Solver (`scheduler.py`)
- Crea eventi da `courses[*].weeklyEvents`.
- Applica vincoli hard e minimizza penalità soft.
- Strategia primaria: OR-Tools CP-SAT.
- Strategia fallback: greedy deterministico con scoring locale.

### 4) Export (`latex_export.py`)
- Genera documento `.tex` completo con tabella orario, statistiche e legenda corsi.
- Compila PDF via `pdflatex` (2 passaggi per riferimenti pagina).
- Supporta filtri opzionali: curriculum, docente, aula.

## Requisiti

- Python 3.10+
- pip
- `pdflatex` installato nel sistema (per endpoint PDF)

Dipendenze Python principali:
- Flask
- ortools

## Installazione

```bash
cd ~/Scrivania/Tirocinio
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Avvio

```bash
source venv/bin/activate
python3 app.py
```

Server di sviluppo:
- URL locale: `http://127.0.0.1:5000`
- Bind: `0.0.0.0:5000`
- Modalità: `debug=True`

## API REST

### Health/UI
- `GET /`
	- Ritorna la pagina principale.

### Database
- `GET /api/db`
	- Ritorna il database corrente.
- `POST /api/db`
	- Salva il database inviato come JSON.
	- Errore `400` se payload non è un oggetto JSON.

### Scheduling
- `POST /api/schedule`
	- Esegue la generazione orario.
	- Body opzionale:
		- `timeLimitSeconds` (default `30`)
		- `semester` (`1`, `2` oppure `null` per tutti)
	- Ritorna risultato con:
		- `assignments`
		- `status`
		- `objective`
		- `message`
		- `solveTimeSeconds`
		- `solverBackend`
		- `timestamp`
		- `semester`

- `GET /api/schedule`
	- Ritorna l'ultimo orario generato.
	- Se assente: `status: "none"`.

### Export
- `GET /api/export/flat`
	- Esporta il DB in formato "flat" con riferimenti risolti.

- `GET /api/export/tex`
	- Ritorna sorgente LaTeX (`application/x-tex`).
	- Richiede che esista un orario generato.

- `GET /api/export/pdf`
	- Ritorna PDF compilato (`application/pdf`).
	- Richiede che esista un orario generato.
	- Query params opzionali:
		- `curriculum`
		- `teacher`
		- `room`

## Modello dati (schema logico)

Root JSON:
- `meta`
- `rooms[]`
- `teachers[]`
- `programs[]`
- `curricula[]`
- `courses[]`
- `unavailability[]`
- `softPolicy`

### `rooms[]`
- `id`, `name`, `capacity`, `type` (`lecture`/`lab`/`seminar`)

### `teachers[]`
- `id`, `name`
- `preferences` opzionali (es. `avoidEarly`, `avoidLate`)

### `courses[]`
- `id`, `name`, `programId`
- `curriculaIds[]`
- `teacherIds[]`
- `expectedStudents`
- `roomType`
- `semester`
- `patternPref` opzionale (es. `Mon-Wed`, `Mon-Wed-Fri`)
- `weeklyEvents[]` con eventi settimanali (`id`, `durationHours`)

### `unavailability[]`
- `teacherId`, `day` (`Mon..Fri`), `hours[]`

### `softPolicy`
- `weights` per penalità soft
- `preferredPatterns` per distribuzione eventi (2 o 3 eventi/settimana)

## Algoritmo di scheduling

### Strategia primaria: CP-SAT

Variabili principali per ogni evento:
- giorno (`day_v`)
- ora inizio (`start_v`)
- aula (`room_v`)
- intervallo temporale (`interval_v`)

#### Vincoli hard implementati
- No overlap docenti (`AddNoOverlap`)
- No overlap curricula/studenti (`AddNoOverlap`)
- No overlap aule (intervalli opzionali per aula)
- Eventi dello stesso corso in giorni diversi (`AddAllDifferent` sui giorni)
- Rispetto indisponibilità docenti
- Compatibilità aula per tipo/capienza
- Rispetto finestra oraria giornaliera
- Esclusione sovrapposizione con pausa pranzo (in fase di start validi)

#### Obiettivo soft (minimizzazione)
- buchi tra lezioni dello stesso curriculum/giorno,
- lezioni troppo presto,
- lezioni troppo tardi,
- eccesso ore consecutive docente,
- eccesso ore giornaliere docente,
- violazione pattern distribuzione preferita.

### Fallback greedy

Se OR-Tools non è installato/importabile:
- ordina gli eventi per "difficoltà" (docenti/curricula/durata),
- prova slot compatibili,
- sceglie il migliore con scoring locale,
- produce stato `feasible` o `partial`.

## Stati di output

Possibili `status` restituiti:
- `optimal`
- `feasible`
- `partial` (greedy con eventi non piazzati)
- `infeasible`
- `no_events`
- `no_valid_slots`
- `error`
- `none` (solo `GET /api/schedule` se non c'è cache)

## Flusso d'uso consigliato

1. Inserisci aule/docenti/corsi/curricula.
2. Definisci indisponibilità e policy soft.
3. Salva DB (`POST /api/db`).
4. Genera orario (`POST /api/schedule`).
5. Controlla risultato (`GET /api/schedule`).
6. Esporta (`/api/export/flat`, `/api/export/tex`, `/api/export/pdf`).

## Esempi API rapidi

Genera orario per semestre 1 con timeout 45s:

```bash
curl -X POST http://127.0.0.1:5000/api/schedule \
	-H "Content-Type: application/json" \
	-d '{"semester": 1, "timeLimitSeconds": 45}'
```

Esporta PDF filtrato per curriculum:

```bash
curl -L "http://127.0.0.1:5000/api/export/pdf?curriculum=CURR-ID" \
	-o orario.pdf
```

Scarica sorgente LaTeX:

```bash
curl -L http://127.0.0.1:5000/api/export/tex -o orario.tex
```

## Troubleshooting

### `status: infeasible`
Cause tipiche:
- troppe lezioni rispetto aule/slot disponibili,
- indisponibilità docenti troppo restrittive,
- pattern o durata eventi incompatibili con finestra oraria.

Suggerimenti:
- aumenta aule o capienza,
- alleggerisci indisponibilità,
- riduci durata/numero eventi,
- prova `semester` separati.

### Export PDF fallisce
- Verifica che `pdflatex` sia installato e raggiungibile nel `PATH`.
- Verifica che esista un orario già generato (`GET /api/schedule`).

### OR-Tools non disponibile
- Il sistema continua in modalità greedy.
- Installa/reinstalla dipendenze da `requirements.txt` per riattivare CP-SAT.

## Note di sviluppo

- Persistenza file-based: adatta a prototipi e uso locale.
- Per ambienti multiutente/produzione valutare:
	- DB relazionale,
	- autenticazione,
	- versioning dati,
	- gestione job asincroni.

## Tecnologie

- Backend: Python + Flask
- Solver: Google OR-Tools CP-SAT + fallback greedy
- Frontend: HTML/CSS/JS vanilla
- Export: LaTeX (`pdflatex`)
