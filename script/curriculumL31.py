import requests
from bs4 import BeautifulSoup
import json
import re

url = "https://web.dmi.unict.it/it/corsi/l-31/piani-di-studio"

response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

curricula = []

# cerca nei titoli della pagina
for header in soup.find_all(["h1", "h2", "h3", "h4"]):

    text = header.get_text(strip=True)

    if "CURRICULUM" in text.upper():
        match = re.search(r'["“](.*?)["”]', text)

        if match:
            curricula.append(match.group(1))

# rimuove duplicati
curricula = list(set(curricula))

dataset = {"curriculum": curricula}

with open("curriculum_l31.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON creato!")