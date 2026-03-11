import requests
from bs4 import BeautifulSoup
import json
import re
""" Script per scarica la pagina ed estrarre il personale amministrazivo
e salvarli in JSON """

# download html page
url ="https://web.dmi.unict.it/personale-ta"
html = requests.get(url).text 
# parser HTML
soup = BeautifulSoup(html, "html.parser")
ta = []
#

# find elements
rows = soup.find_all("tr")
for row in rows[1:]:   # salta intestazione
    cols = row.find_all("td")

    if len(cols) >= 3:
        nome = cols[0].get_text(strip=True)
        ruolo = cols[1].get_text(strip=True)
        ssd = cols[2].get_text(strip=True)

        ta.append({
            "nome_ta": nome,
            "email": ruolo,
            "telefono": ssd
        })

print("Personale amministrativo: \n", ta)
#  JSON
dataset = {"Personale amministrativo": ta}
with open("ta.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON!")