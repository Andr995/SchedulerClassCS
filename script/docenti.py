import requests
from bs4 import BeautifulSoup
import json
import re
""" Script per scarica la pagina ed estrarre i corsi di laurea
e salvarli in JSON """

# download html page
url ="https://web.dmi.unict.it/docenti"
html = requests.get(url).text 
# parser HTML
soup = BeautifulSoup(html, "html.parser")
docenti = []

# find elements
rows = soup.find_all("tr")
for row in rows[1:]:   # salta intestazione
    cols = row.find_all("td")

    if len(cols) >= 3:
        nome = cols[0].get_text(strip=True)
        ruolo = cols[1].get_text(strip=True)
        ssd = cols[2].get_text(strip=True)

        docenti.append({
            "nome_docente": nome,
            "ruolo": ruolo,
            "ssd": ssd
        })

print("docenti: \n", docenti)
#  JSON
dataset = {"docenti": docenti}
with open("docenti.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON!")