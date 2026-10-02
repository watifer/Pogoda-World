"""
menu_setup.py — starszy, uproszczony skrypt resetu menu komend (default + PL).

Głównym skryptem jest `update_menu.py` (wgrywa menu dla pl/en/de/es/fr/no/nb
we wszystkich zakresach). Ten plik zostaje jako szybki twardy reset i ma
IDENTYCZNĄ listę 8 komend, żeby oba skrypty nie rozjeżdżały się w UX.

PR2 UX cleanup:
- /menu -> /report (PL: /raport) — dotyczy wyłącznie godzin raportów,
- /porady (PL) i /tips (EN) znikają z menu — porady są sekcją w /info,
- komendy prywatności (/privacy, /my_data, /forget_location, /delete_me) oraz
  techniczny /save_location nie są pozycjami menu — są w /info i w /dane,
- PL aliasy: /dzien /teraz /trend /raport /miasto /zapros /info /dane.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TG_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

commands_en = [
    {"command": "day", "description": "☀️ Daily weather card"},
    {"command": "now", "description": "📡 Tactical radar (12 hrs)"},
    {"command": "trend", "description": "🔮 14-day weather trend"},
    {"command": "report", "description": "⚙️ Report hours"},
    {"command": "city", "description": "🌍 Change your location"},
    {"command": "invite", "description": "💌 Invite or add to group"},
    {"command": "info", "description": "ℹ️ Info and tips"},
    {"command": "data", "description": "📦 Your stored data"},
]

commands_pl = [
    {"command": "dzien", "description": "☀️ Prognoza dzienna"},
    {"command": "teraz", "description": "📡 Prognoza na 12 godzin"},
    {"command": "trend", "description": "🔮 Prognoza na 14 dni"},
    {"command": "raport", "description": "⚙️ Godziny raportów"},
    {"command": "miasto", "description": "🌍 Zmień lokalizację"},
    {"command": "zapros", "description": "💌 Zaproś znajomego"},
    {"command": "info", "description": "ℹ️ Informacje i porady"},
    {"command": "dane", "description": "📦 Twoje dane"},
]

print("🧹 1. Kasowanie starych ustawień z serwerów Telegrama...")
requests.post(f"{BASE_URL}/deleteMyCommands")
requests.post(f"{BASE_URL}/deleteMyCommands", json={"language_code": "pl"})
requests.post(f"{BASE_URL}/deleteMyCommands", json={"language_code": "en"})

print("🌍 2. Wgrywanie ANGIELSKIEGO menu (jako globalnego domyślnego)...")
resp_en = requests.post(f"{BASE_URL}/setMyCommands", json={"commands": commands_en})
print(f"Status EN: {resp_en.json()}")

print("🇵🇱 3. Wgrywanie POLSKIEGO menu (tylko dla urządzeń z ustawionym j. polskim)...")
resp_pl = requests.post(f"{BASE_URL}/setMyCommands", json={"commands": commands_pl, "language_code": "pl"})
print(f"Status PL: {resp_pl.json()}")

print("✅ Twardy reset zakończony!")
