# SchedulerClassCS

# University Timetabling System

Applicazione web per la generazione automatica dell'orario universitario con ottimizzazione vincolata.

## Panoramica

Il progetto implementa una pipeline completa:

1. gestione dati accademici (aule, docenti, corsi, curricula, vincoli),
2. generazione orario con scelta algoritmo da menu (CP-SAT, greedy, genetico, tabu, lineare),
3. validazione vincoli hard e log in pagina,
4. modifica manuale orario (giorno/ora/aula) con salvataggio,
5. import/export JSON (DB e orario), esportazione PDF (senza LaTeX).

L'app è pensata per uso locale/laboratorio e persiste i dati in file JSON nella cartella `data/`.

## Struttura del progetto

```
├── app.py                 # Backend Flask + API REST + persistenza JSON
├── scheduler.py           # Motore scheduling (CP-SAT + fallback greedy)
├── pdf_export.py          # Generazione PDF senza LaTeX (ReportLab)
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

### 1) Frontend
- `templates/admin.html`: interfaccia amministrativa (CRUD + generazione orario).
- `templates/index.html`: interfaccia pubblica read-only (solo visualizzazione orario).
- `templates/admin_login.html`: login con password per accesso area admin.

### 2) Backend (`app.py`)
- Espone endpoint HTTP per dati e scheduling.
- Carica/salva JSON su disco (`database.json`, `last_schedule.json`).
- Orchestration: prepara input, invoca `scheduler.solve(...)`, arricchisce output con metadati.
- Espone export flat/PDF e endpoint pubblico read-only per consultazione orario.

### 3) Solver (`scheduler.py`)
- Crea eventi da `courses[*].weeklyEvents`.
- Applica vincoli hard e minimizza penalità soft.
- Supporta più famiglie di algoritmi selezionabili da UI/API.

## Funzionalita Principali

- Gestione completa dataset (aule, docenti, corsi, curricula, indisponibilità, policy soft).
- Import DB completo (`database.json`) o import intelligente JSON esterni (aule/corsi di laurea/docenti).
- Generazione orario per semestre (`1`, `2`) o completa.
- Selezione algoritmo da menu in area admin.
- Log vincoli hard rispettati/violati su ogni soluzione.
- Visualizzazione pubblica read-only dell'orario.
- Tabelle curriculum in formato griglia settimanale.
- Export PDF dell'orario senza dipendenza LaTeX.
- Export JSON orario generato.
- Import JSON orario nel sistema.
- Editing manuale orario (spostamento giorno/ora/aula) e salvataggio persistente.

## Algoritmi Disponibili

Il backend accetta il parametro `algorithm` in `POST /api/schedule`.

- `auto`: usa CP-SAT se disponibile, altrimenti fallback greedy.
- `cp-sat`: programmazione a vincoli con OR-Tools CP-SAT.
- `constraint-programming`: alias di `cp-sat`.
- `greedy`: euristica deterministica a costo locale.
- `genetic`: metaeuristica stile algoritmo genetico (multi-start + evoluzione seed).
- `tabu-search`: metaeuristica tabu search (vicinato + lista tabu).
- `linear-programming`: modellazione lineare intera risolta via backend OR-Tools CP-SAT; fallback greedy se OR-Tools non disponibile.

Alias supportati lato API: `cp`, `constraints`, `tabu`, `lp`, `mip`, ecc.

### Tabella Comparativa Algoritmi

| Algoritmo | Punti di forza | Limiti | Quando usarlo |
|---|---|---|---|
| `auto` | Sceglie automaticamente il miglior backend disponibile | Comportamento dipendente dall'ambiente (OR-Tools presente o meno) | Scelta predefinita consigliata |
| `cp-sat` / `constraint-programming` | Migliore qualità soluzione su vincoli complessi, supporta ottimalità/feasibility | Più pesante computazionalmente su istanze grandi | Pianificazione principale in produzione/locale |
| `greedy` | Molto veloce, robusto anche senza OR-Tools | Qualità soluzione inferiore, può lasciare eventi non piazzati | Prototipi rapidi, fallback, debug dati |
| `genetic` | Esplora più candidati, utile per evitare minimi locali semplici | Tempi maggiori del greedy, qualità variabile per seed/tempo | Ricerca euristica alternativa quando CP-SAT non è ideale |
| `tabu-search` | Migliora iterativamente soluzioni euristiche con memoria tabu | Parametri sensibili (iterazioni/vicinato), non garantisce ottimo globale | Affinamento euristico di soluzioni greedy |
| `linear-programming` | Modello MILP-like con backend OR-Tools CP-SAT, approccio formale | In questa implementazione usa backend intero CP-SAT (non LP continuo puro) | Casi in cui si preferisce formulazione lineare intera |

Suggerimento pratico:
- inizia con `auto` o `cp-sat`;
- usa `greedy` per test veloci;
- prova `genetic`/`tabu-search` quando vuoi confrontare strategie euristiche;
- usa `linear-programming` quando vuoi esplicitare un'impostazione MILP-like.

### 4) Export (`pdf_export.py`)
- Genera PDF direttamente in Python con ReportLab (nessuna dipendenza LaTeX).
- Include log vincoli hard e tabelle separate per ogni curriculum.
- Supporta filtri opzionali: curriculum, docente, aula.

## Requisiti

- Python 3.10+
- pip
- Nessuna dipendenza LaTeX richiesta per il PDF

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
	- Ritorna la pagina pubblica read-only (solo orario).
- `GET /admin`
	- Ritorna la pagina admin (se non autenticato mostra login).
- `POST /admin/login`
	- Login area admin (password via variabile `ADMIN_PASSWORD`, default `admin`).
- `POST /admin/logout`
	- Logout area admin.

### Database
- `GET /api/db`
	- Ritorna il database corrente (solo admin).
- `POST /api/db`
	- Salva il database inviato come JSON (solo admin).
	- Errore `400` se payload non è un oggetto JSON.

### Scheduling
- `POST /api/schedule`
	- Esegue la generazione orario (solo admin).
	- Body opzionale:
		- `timeLimitSeconds` (default `30`)
		- `semester` (`1`, `2` oppure `null` per tutti)
		- `algorithm` (`auto`, `cp-sat`, `greedy`, `genetic`, `tabu-search`, `linear-programming`)
	- Ritorna risultato con:
		- `assignments`
		- `status`
		- `objective`
		- `message`
		- `solveTimeSeconds`
		- `solverBackend`
		- `requestedAlgorithm`
		- `timestamp`
		- `semester`

- `GET /api/schedule`
	- Ritorna l'ultimo orario generato.
	- Se assente: `status: "none"`.

- `POST /api/schedule/import`
	- Importa un JSON orario (solo admin).
	- Normalizza il payload e salva `last_schedule.json`.

- `POST /api/schedule/manual-update`
	- Salva modifiche manuali alle assegnazioni orario (solo admin).
	- Ricalcola il report vincoli hard.

- `GET /api/public/timetable`
	- Restituisce dati read-only per vista pubblica.
	- Include anche tabelle separate per curriculum.

### Export
- `GET /api/export/flat`
	- Esporta il DB in formato "flat" con riferimenti risolti (solo admin).

- `GET /api/export/schedule-json`
	- Esporta l'ultimo orario in JSON (solo admin).

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
6. Esporta (`/api/export/flat`, `/api/export/pdf`).

## Esempi API rapidi

Genera orario per semestre 1 con timeout 45s:

```bash
curl -X POST http://127.0.0.1:5000/api/schedule \
	-H "Content-Type: application/json" \
	-d '{"semester": 1, "timeLimitSeconds": 45, "algorithm": "cp-sat"}'
```

Importa un JSON orario già pronto:

```bash
curl -X POST http://127.0.0.1:5000/api/schedule/import \
	-H "Content-Type: application/json" \
	-d @orario_generato.json
```

Esporta PDF filtrato per curriculum:

```bash
curl -L "http://127.0.0.1:5000/api/export/pdf?curriculum=CURR-ID" \
	-o orario.pdf
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
- Verifica che il pacchetto Python `reportlab` sia installato nell'ambiente attivo.
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
- Export: PDF nativo (`reportlab`)
