<<<<<<< HEAD
# SchedulerClassCS
=======
# University Timetabling System

Applicazione web per la generazione automatica dell'orario delle lezioni universitarie con ottimizzazione vincolata.

## Struttura del progetto

```
├── app.py                 # Backend Flask (API REST)
├── scheduler.py           # Motore di scheduling (OR-Tools CP-SAT + fallback greedy)
├── templates/
│   └── index.html         # Interfaccia web (frontend)
├── data/                  # Database JSON (creato automaticamente)
├── Classes.html           # Prototipo HTML originale (riferimento)
├── requirements.txt       # Dipendenze Python
└── README.md
```

## Requisiti

- Python 3.10+
- pip

## Installazione

```bash
# Clona il repository e entra nella cartella
cd ~/Scrivania/Tirocinio

# Crea un virtual environment
python3 -m venv venv

# Attiva il virtual environment
source venv/bin/activate

# Installa le dipendenze
pip install -r requirements.txt
```

## Avvio

```bash
source venv/bin/activate
python3 app.py
```

Il server si avvia su **http://127.0.0.1:5000**.

## Utilizzo

1. **Aule** — Aggiungi le aule con capienza e tipologia (lecture/lab/seminar)
2. **Docenti** — Inserisci i docenti e le loro preferenze orarie
3. **CdS** — Crea i Corsi di Studio
4. **Curricula** — Definisci i gruppi di studenti (no sovrapposizioni tra corsi dello stesso curriculum)
5. **Insegnamenti** — Crea i corsi con eventi settimanali (es. 2×2h), associa docenti e curricula
6. **Indisponibilità** — Imposta i vincoli di indisponibilità per docente/giorno/ore
7. **Policy** — Configura i pesi dei vincoli soft
8. **📅 Orario** — Clicca "⚡ Genera Orario" per calcolare lo scheduling ottimale

## Vincoli

### Hard (obbligatori)
- Nessuna sovrapposizione docente, aula o gruppo studenti
- Capienza aula ≥ studenti attesi
- Tipo aula corrispondente al tipo richiesto
- Pausa pranzo 13:00–14:00 (nessuna lezione)
- Eventi dello stesso corso su giorni diversi
- Rispetto indisponibilità docenti

### Soft (ottimizzati)
- Minimizzazione buchi tra lezioni dello stesso curriculum
- Penalità lezioni alle 8:00 o dopo le 17:00
- Penalità ore consecutive eccessive per docente
- Rispetto preferenze orarie docenti

## Tecnologie

- **Backend**: Python, Flask
- **Solver**: Google OR-Tools (CP-SAT) con fallback greedy
- **Frontend**: HTML/CSS/JS vanilla (single-page, API-driven)
>>>>>>> 68aabe3 (first commit)
