import requests
from bs4 import BeautifulSoup
import json
import re

url = "https://web.dmi.unict.it/corsi/lm-40/programmi"

response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

insegnamenti = {}

rows = soup.find_all("tr")

for row in rows[1:]:  # salta intestazione
    cols = row.find_all("td")

    if cols:
        testo = cols[0].get_text(strip=True)

        match = re.match(r"(\d+)\s*-\s*(.*)", testo)

        if match:
            codice = match.group(1)
            nome = match.group(2).strip()

            # salva solo se non esiste già
            if codice not in insegnamenti:
                insegnamenti[codice] = nome

# convertiamo in lista
output = {
    "insegnamenti": [
        {"codice": codice, "nome_insegnamento": nome}
        for codice, nome in insegnamenti.items()
    ]
}

with open("insegnamenti_lm40.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=4, ensure_ascii=False)

print("File JSON creato!")