import requests
from bs4 import BeautifulSoup
import json
import re
""" Script per scarica la pagina ed estrarre i dottorandi
e salvarli in JSON """

# download html page
url ="https://web.dmi.unict.it/dottorandi"
html = requests.get(url).text 
# parser HTML
soup = BeautifulSoup(html, "html.parser")
dottorandi = []

# find elements
rows = soup.find_all("tr")
for row in rows[1:]:   # salta intestazione
    cols = row.find_all("td")

    if len(cols) >= 3:
        nome = cols[0].get_text(strip=True)
        email = cols[1].get_text(strip=True)
        Ciclo = cols[2].get_text(strip=True)

        dottorandi.append({
            "nome_dottorandi": nome,
            "email": email,
            "ciclo": Ciclo
        })

print("dottorandi: \n", dottorandi)
#  JSON
dataset = {"dottorandi": dottorandi}
with open("dottorandi.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON!")