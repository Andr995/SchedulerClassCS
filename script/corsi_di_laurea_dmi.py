import requests
from bs4 import BeautifulSoup
import json
import re
""" Script per scarica la pagina ed estrarre i corsi di laurea
e salvarli in JSON """

# download html page
url ="https://web.dmi.unict.it/it/content/didattica"
html = requests.get(url).text 

# parser HTML
soup = BeautifulSoup(html, "html.parser")
corsi_laurea = []

# find elements
items = soup.find_all("li")
for item in items:
    text = item.get_text(strip=True)
    if "CdL" in text:
        # estrae nome corso
        nome_match = re.search(r"in (.*?) \(", text)
        # estrae classe
        classe_match = re.search(r"\((.*?)\)", text)
        if nome_match and classe_match:
            nome = nome_match.group(1)
            classe = classe_match.group(1)
            tipo = "magistrale" if "magistrale" in text else "triennale"

            corsi_laurea.append({
                "nome": nome,
                "classe": classe,
                "tipo": tipo
            })


print("corsi_laurea: \n", corsi_laurea)
#  JSON
dataset = {"corsi_laurea": corsi_laurea}
with open("corsi_dmi.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON!")