import os
import requests
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TG_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

commands_en = [
    {"command": "menu", "description": "⚙️ Geo & hours settings panel"},
    {"command": "now", "description": "📡 Tactical radar (current)"},
    {"command": "day", "description": "☀️ Daily weather card"},
    {"command": "trend", "description": "🔮 14-day weather trend"},
    {"command": "invite", "description": "💌 Invite or add to group"},
    {"command": "info", "description": "ℹ️ Brief bot manual"},
    {"command": "tips", "description": "💡 Useful tricks and features"},
    {"command": "city", "description": "🌍 Change location by text"}
]

commands_pl = [
    {"command": "menu", "description": "⚙️ Panel ustawień Geo i godzin"},
    {"command": "now", "description": "📡 Radar taktyczny (na teraz)"},
    {"command": "day", "description": "☀️ Dzienna karta pogodowa"},
    {"command": "trend", "description": "🔮 Trend pogody (14 dni)"},
    {"command": "zapros", "description": "💌 Zaproś lub dodaj do grupy"},
    {"command": "info", "description": "ℹ️ Krótka instrukcja obsługi"},
    {"command": "porady", "description": "💡 Przydatne triki i funkcje"},
    {"command": "miasto", "description": "🌍 Zmień miasto z klawiatury"}
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