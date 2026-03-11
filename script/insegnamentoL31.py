import requests
from bs4 import BeautifulSoup
import json
import re

url = "https://web.dmi.unict.it/corsi/l-31/programmi"
#scrip per creare file json per gli insegnamenti del corso di laurea triennale in informatica
response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

insegnamenti_set = set()  # per rimuovere duplicati

# trova tutte le righe della tabella
rows = soup.find_all("tr")

for row in rows[1:]:  # salta intestazione
    cols = row.find_all("td")
    if cols:
        testo = cols[0].get_text(strip=True)

        # estrai codice e nome insegnamento
        match = re.match(r"(\d+)\s*-\s*(.*?)\s+[A-Z]\s*-\s*[A-Z]", testo)
        if match:
            codice = match.group(1)
            nome = match.group(2).strip()
            insegnamenti_set.add((codice, nome))  # aggiunge come tupla unica

# converti in lista di dizionari
insegnamenti = [{"codice": c, "nome_insegnamento": n} for c, n in sorted(insegnamenti_set)]

# salva JSON
dataset = {"insegnamenti": insegnamenti}

with open("insegnamenti_l31.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON creato!")