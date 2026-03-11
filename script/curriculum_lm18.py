import requests
from bs4 import BeautifulSoup
import json

url = "https://web.dmi.unict.it/it/corsi/lm-18/piani-di-studio"

response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")

curricula = []

# trova tutti gli elementi della lista puntata
for li in soup.find_all("li"):
    text = li.get_text(strip=True)

    # filtriamo solo i curricula
    if text and len(text) > 5 and text[0].isupper():
        curricula.append(text)

dataset = {"curriculum_lm18": curricula}

with open("curriculum_lm18.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON creato!")