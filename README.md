# University Timetabling System

Applicazione web per la gestione dati accademici e la generazione automatica dell'orario universitario con ottimizzazione vincolata.

## Panoramica

Il progetto include:

1. Gestione dataset: aule, docenti, corsi, curricula, indisponibilita, policy soft.
2. Generazione orario con algoritmi selezionabili: `auto`, `cp-sat`, `greedy`, `genetic`, `tabu-search`, `linear-programming`.
3. Validazione vincoli hard e report nel risultato.
4. Modifica manuale dell'orario con salvataggio persistente.
5. Import/Export JSON (DB, orario, bundle DB+orario) e export PDF.
6. Autenticazione admin multiutente con password hashate (scrypt), gestione utenti da UI admin.

I dati sono persistiti in file JSON nella cartella `data/`.

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
├── requirements.txt
└── README.md
```

## Architettura

### Frontend

- `templates/admin.html`: UI amministrativa (CRUD, scheduling, import/export, gestione utenti).
- `templates/admin_login.html`: login admin con `username + password`.
- `templates/index.html`: vista pubblica read-only dell'orario.

### Backend (`app.py`)

- API REST per DB, scheduling, import/export.
- Persistenza file-based (`database.json`, `last_schedule.json`, `users.json`).
- Autenticazione admin con sessione Flask.
- Password protette con hash `scrypt` (`werkzeug.security`).

### Solver (`scheduler.py`)

- Generazione eventi dai `weeklyEvents` dei corsi.
- Vincoli hard + obiettivo soft.
- Supporto multi algoritmo.
- Fallback backend: se algoritmo richiesto restituisce `infeasible`, il backend tenta automaticamente `greedy` e ritorna soluzione `partial` se disponibile.

## Sicurezza e autenticazione

### Modello utenti

- Gli utenti admin sono salvati in `data/users.json`.
- Le password non sono mai salvate in chiaro.
- Hash password: `scrypt` (metodo moderno, memory-hard).

### Bootstrap default

Alla prima esecuzione, se non esiste alcun utente, viene creato:

- Username: `admin`
- Password: `admin`

Cambiare subito la password dopo il primo accesso.

Variabili ambiente supportate per bootstrap:

- `DEFAULT_ADMIN_USERNAME` (default: `admin`)
- `DEFAULT_ADMIN_PASSWORD` (default: `admin`)

### Policy password (creazione/reset da admin)

Password obbligatoriamente robuste:

- minimo 12 caratteri
- almeno una minuscola
- almeno una maiuscola
- almeno un numero
- almeno un simbolo

## Funzionalita principali

- CRUD completo su risorse e vincoli.
- Generazione orario per semestre o globale.
- Generazione per singolo CdL/anno (`program-year`).
- Report vincoli hard.
- Edit manuale orario.
- Export PDF senza dipendenza LaTeX.
- Export JSON orario.
- Export bundle `DB+orario` (`database_con_orario.json`).
- Import DB classico, import JSON esterni (aule/corsi/docenti), import bundle `DB+orario`.
- Gestione utenti admin (crea, reset password, elimina).

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

- URL: `http://127.0.0.1:5000`
- Host: `0.0.0.0`
- Porta: `5000`

## Algoritmi disponibili

Parametro `algorithm` in `POST /api/schedule`:

- `auto`
- `cp-sat`
- `greedy`
- `genetic`
- `tabu-search`
- `linear-programming`

Alias principali supportati: `cp`, `constraints`, `tabu`, `lp`, `mip`, ecc.

### Spiegazione rapida

- `auto`: prova CP-SAT (se disponibile), altrimenti usa greedy.
- `cp-sat`: solver a vincoli (OR-Tools), migliore qualita media su vincoli complessi.
- `greedy`: euristica veloce; robusta, ma puo lasciare piu eventi non piazzati.
- `genetic`: metaeuristica multi-start/evolutiva; utile per esplorare alternative.
- `tabu-search`: metaeuristica con memoria tabu; migliora candidati euristici evitando cicli.
- `linear-programming`: formulazione lineare intera via backend OR-Tools CP-SAT.

### Tabella comparativa

| Algoritmo | Punti di forza | Limiti | Quando usarlo |
|---|---|---|---|
| `auto` | Semplice e bilanciato, sceglie il backend migliore disponibile | Dipende dall'ambiente (OR-Tools installato o no) | Scelta predefinita consigliata |
| `cp-sat` | Soluzioni migliori su vincoli hard complessi, buona qualita globale | Piu pesante su istanze molto grandi | Pianificazione principale |
| `greedy` | Molto veloce e prevedibile | Qualita inferiore rispetto a CP-SAT | Test rapidi, fallback, debug dati |
| `genetic` | Esplora piu configurazioni, puo trovare alternative utili | Variabilita nei risultati, tempi maggiori del greedy | Confronto euristico quando CP-SAT fatica |
| `tabu-search` | Affina soluzioni euristiche con memoria delle mosse | Parametri sensibili (iterazioni/vicinato) | Miglioramento euristico iterativo |
| `linear-programming` | Approccio formale MILP-like | In questa implementazione usa backend CP-SAT intero | Casi in cui vuoi impostazione lineare intera |

### Suggerimento pratico

1. Parti da `auto`.
2. Se vuoi qualita massima, prova `cp-sat`.
3. Se vuoi tempi brevi, usa `greedy`.
4. Per confronti sperimentali, prova `genetic` e `tabu-search`.
5. Se il solver richiesto va in `infeasible`, il backend puo tornare `partial` tramite fallback `greedy`.

## API principali

### UI/Auth

- `GET /` -> pagina pubblica
- `GET /admin` -> area admin (o login)
- `POST /admin/login` -> login con `username`, `password`
- `POST /admin/logout` -> logout

### Utenti admin

- `GET /api/users` -> lista utenti (senza hash)
- `POST /api/users` -> crea utente admin
- `POST /api/users/<username>/password` -> reset password
- `DELETE /api/users/<username>` -> elimina utente

Note di protezione:

- non puoi eliminare l'utente loggato
- deve rimanere almeno un admin attivo

### Database

- `GET /api/db`
- `POST /api/db`

### Scheduling

- `POST /api/schedule`
- `POST /api/schedule/program-year`
- `GET /api/schedule`
- `POST /api/schedule/import`
- `POST /api/schedule/manual-update`
- `GET /api/public/timetable`

### Export

- `GET /api/export/flat`
- `GET /api/export/schedule-json`
- `GET /api/export/pdf`

## Import/Export JSON

### Export DB (UI admin)

Il pulsante `Esporta DB` genera un bundle:

- file: `database_con_orario.json`
- campi principali: `database`, `schedule`, metadati export

### Reimport bundle

Lo stesso file puo essere importato per ripristinare:

1. Database
2. Ultimo orario

in un unico passaggio.

## Stato solver e fallback

Possibili `status`:

- `optimal`, `feasible`, `partial`, `infeasible`, `error`, `none`, ecc.

Se l'algoritmo richiesto produce `infeasible`, il backend puo restituire una soluzione `partial` tramite fallback automatico a `greedy`.

## Note sviluppo

- Persistenza file-based: ottima per prototipo/uso locale.
- Per produzione consigliato:
  - server WSGI/ASGI (es. gunicorn)
  - gestione segreti robusta
  - database relazionale
  - rate limit login e audit log
