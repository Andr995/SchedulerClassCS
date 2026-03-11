import pdfplumber
import json
""" Script per scarica la pagina ed estrarre le aule e dimensioni
e salvarli in JSON """

# download 
url = "piano triennale Dipartimentale.pdf"
# parser HTML
aule = []

with pdfplumber.open(url) as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            for row in table:
                if not row:
                    continue

                # controlla che la riga contenga un'aula
                if row[0] and "Aula" in row[0]:
                    nome = row[0].strip()
                    postazioni = None
                    if len(row) > 2 and row[2]:
                        postazioni = row[2]

                    aule.append({
                        "nome_aula": nome,
                        "postazioni": postazioni
                    })


print("aule: \n", aule)
#  JSON
dataset = {"aule": aule}
with open("aule_dmi.json", "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=4, ensure_ascii=False)

print("File JSON!")