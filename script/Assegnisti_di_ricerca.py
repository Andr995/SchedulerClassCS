import requests
from bs4 import BeautifulSoup
import json

url = "https://web.dmi.unict.it/it/assegnisti-di-ricerca"

response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

assegnisti = []

# trova tutte le righe della tabella
rows = soup.find_all("tr")

for row in rows[1:]:  # salta l'intestazione
    cols = row.find_all("td")
    if len(cols) >= 2:
        nome = cols[0].get_text(strip=True)
        email = cols[1].get_text(strip=True)
        assegnisti.append({
            "nome": nome,
            "email": email
        })

dataset = {"assegnisti": assegnisti}

with open("assegnisti_dmi.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON creato!")