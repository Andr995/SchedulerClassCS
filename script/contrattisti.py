import requests
from bs4 import BeautifulSoup
import json

url = "https://web.dmi.unict.it/elenchi/contrattisti-di-ricerca"

response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

contrattisti = []

# trova tutte le righe della tabella
rows = soup.find_all("tr")

# leggi intestazioni
headers = [th.get_text(strip=True) for th in rows[0].find_all("th")]

for row in rows[1:]:  # salta intestazione
    cols = row.find_all("td")
    if len(cols) >= 2:  # nome e email
        nome = cols[0].get_text(strip=True)
        email = cols[1].get_text(strip=True)

        record = {
            "nome": nome,
            "email": email
        }

        # se ci sono altre colonne, le aggiungo
        if len(cols) > 2:
            for i in range(2, len(cols)):
                record[headers[i]] = cols[i].get_text(strip=True)

        contrattisti.append(record)

dataset = {"contrattisti_di_ricerca": contrattisti}

with open("contrattisti_dmi.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON creato!")