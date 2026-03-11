import requests
from bs4 import BeautifulSoup
import json
import re

url = "https://web.dmi.unict.it/corsi/lm-18/programmi"
#scrip per creare file json per gli insegnamenti del corso di laurea magistrale in informatica
response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

insegnamenti_set = set()

rows = soup.find_all("tr")

for row in rows[1:]:
    cols = row.find_all("td")

    if cols:
        testo = cols[0].get_text(strip=True)

        # separa codice e nome
        match = re.match(r"(\d+)\s*-\s*(.*)", testo)

        if match:
            codice = match.group(1)
            nome = match.group(2).strip()

            insegnamenti_set.add((codice, nome))

insegnamenti = [
    {"codice": c, "nome_insegnamento": n}
    for c, n in sorted(insegnamenti_set)
]

dataset = {"insegnamenti": insegnamenti}

with open("insegnamenti_lm18.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON creato!")