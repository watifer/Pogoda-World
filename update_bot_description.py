import os
import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["TG_TOKEN"]
BASE = f"https://api.telegram.org/bot{TOKEN}"

DESCRIPTIONS = {
  "pl": "Cześć! Wyślę prognozę na dziś, najbliższe godziny i trend na 14 dni. Kliknij przycisk na dole, aby rozpocząć.",
  "en": "Hi! I can send today’s forecast, the next hours, and a 14‑day trend. Tap the button below to begin.",
  "de": "Hallo! Ich sende die Prognose für heute, die nächsten Stunden und den 14‑Tage‑Trend. Tippe unten auf die Schaltfläche, um zu starten.",
  "fr": "Salut ! Je peux envoyer la prévision du jour, les prochaines heures et la tendance sur 14 jours. Appuie sur le bouton ci‑dessous pour commencer.",
  "es": "¡Hola! Puedo enviar la previsión de hoy, las próximas horas y la tendencia de 14 días. Pulsa el botón de abajo para empezar.",
  "nb": "Hei! Jeg kan sende dagens prognose, de neste timene og en 14-dagers trend. Trykk på knappen nederst for å starte.",
}

SHORT = {
  "pl": "Codzienne karty pogody, prognoza godzinowa i trend 14 dni.",
  "en": "Daily weather cards, hourly forecast and 14‑day trend.",
  "de": "Tägliche Wetterkarten, Stundenprognose und 14‑Tage‑Trend.",
  "fr": "Cartes météo quotidiennes, prévisions horaires et tendance 14 jours.",
  "es": "Tarjetas diarias, pronóstico por horas y tendencia de 14 días.",
  "nb": "Daglige værkort, timevarsel og 14-dagers trend.",
}

def post(method: str, payload: dict):
    r = requests.post(f"{BASE}/{method}", json=payload, timeout=10)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"{method} failed: {j}")
    return j

# 1) Default (fallback) – ustaw EN jako globalny
post("setMyDescription", {"description": DESCRIPTIONS["en"]})
post("setMyShortDescription", {"short_description": SHORT["en"]})

# 2) Języki per language_code
for lang in ("pl","de","fr","es","nb"):
    post("setMyDescription", {"description": DESCRIPTIONS[lang], "language_code": lang})
    post("setMyShortDescription", {"short_description": SHORT[lang], "language_code": lang})

# (opcjonalnie) jeśli chcesz obsłużyć też 'no' jako alias dla norweskiego:
post("setMyDescription", {"description": DESCRIPTIONS["nb"], "language_code": "no"})
post("setMyShortDescription", {"short_description": SHORT["nb"], "language_code": "no"})

print(requests.post(f"{BASE}/getMyDescription", json={"language_code":"de"}).json())
print(requests.post(f"{BASE}/getMyShortDescription", json={"language_code":"de"}).json())

print("OK")