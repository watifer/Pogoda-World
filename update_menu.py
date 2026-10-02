"""
update_menu.py — wgrywa menu komend bota do Telegrama (setMyCommands).

PR2 UX cleanup: menu ma DOKŁADNIE 8 pozycji — tyle, ile użytkownik realnie
potrzebuje. Komendy prywatności (/priv, /bezGPS, /usunDane), porady (/porady)
oraz techniczny alias /save_location NIE są pozycjami menu; są opisane w /info
i w panelu /dane.

PL menu używa krótkich aliasów (/dzien, /teraz, /raport, /miasto, /zapros,
/dane) — parser w location_bot.COMMAND_ALIASES mapuje je na komendy kanoniczne.
Pozostałe języki dostają te same kanoniczne komendy z opisem w swoim języku.

Uwaga techniczna: Telegram przyjmuje w menu wyłącznie małe litery, cyfry i "_",
dlatego /bezGPS i /usunDane celowo nie mogą być pozycjami menu (działają jako
komendy wpisywane/case-insensitive).

Lokalne uruchomienie: python update_menu.py
"""

import os
import requests
from dotenv import load_dotenv

# Ładowanie tokena z pliku .env (działa świetnie na laptopie)
load_dotenv()
TOKEN = os.environ.get("TG_TOKEN")
BASE = f"https://api.telegram.org/bot{TOKEN}"

# Zakresy (scopes), na których operujemy
SCOPES = [
    {"type": "default"},
    {"type": "all_private_chats"},
    {"type": "all_group_chats"},
    {"type": "all_chat_administrators"},
]

# ==============================================================
# BAZA KOMEND DLA RÓŻNYCH JĘZYKÓW (8 pozycji — finalne ustalenie PR2 UX)
# ==============================================================
COMMANDS_EN = [
    {"command": "day", "description": "☀️ Daily weather card"},
    {"command": "now", "description": "📡 Tactical radar (12 hrs)"},
    {"command": "trend", "description": "🔮 14-day weather trend"},
    {"command": "report", "description": "⚙️ Report hours"},
    {"command": "city", "description": "🌍 Change your location"},
    {"command": "invite", "description": "💌 Invite or add to group"},
    {"command": "info", "description": "ℹ️ Info and tips"},
    {"command": "data", "description": "📦 Your stored data"},
]

COMMANDS_PL = [
    {"command": "dzien", "description": "☀️ Prognoza dzienna"},
    {"command": "teraz", "description": "📡 Prognoza na 12 godzin"},
    {"command": "trend", "description": "🔮 Prognoza na 14 dni"},
    {"command": "raport", "description": "⚙️ Godziny raportów"},
    {"command": "miasto", "description": "🌍 Zmień lokalizację"},
    {"command": "zapros", "description": "💌 Zaproś znajomego"},
    {"command": "info", "description": "ℹ️ Informacje i porady"},
    {"command": "dane", "description": "📦 Twoje dane"},
]

COMMANDS_BY_LANG = {
    # 1. DOMYŚLNE MENU GLOBALNE (angielski dla wszystkich nieobsługiwanych krajów)
    "default": COMMANDS_EN,

    # 2. POLSKI (pl) — telefony z językiem PL dostają krótkie aliasy
    "pl": COMMANDS_PL,

    # 3. ANGIELSKI (en)
    "en": COMMANDS_EN,

    # 4. NIEMIECKI (de)
    "de": [
        {"command": "day", "description": "☀️ Tägliche Wetterkarte"},
        {"command": "now", "description": "📡 Taktisches Radar (12 Std)"},
        {"command": "trend", "description": "🔮 14-Tage-Wettertrend"},
        {"command": "report", "description": "⚙️ Berichtszeiten"},
        {"command": "city", "description": "🌍 Standort ändern"},
        {"command": "invite", "description": "💌 In Gruppe einladen"},
        {"command": "info", "description": "ℹ️ Infos und Tipps"},
        {"command": "data", "description": "📦 Deine gespeicherten Daten"},
    ],

    # 5. HISZPAŃSKI (es)
    "es": [
        {"command": "day", "description": "☀️ Tarjeta meteorológica"},
        {"command": "now", "description": "📡 Radar táctico (12 hrs)"},
        {"command": "trend", "description": "🔮 Tendencia (14 días)"},
        {"command": "report", "description": "⚙️ Horas de informes"},
        {"command": "city", "description": "🌍 Cambiar ubicación"},
        {"command": "invite", "description": "💌 Invitar al grupo"},
        {"command": "info", "description": "ℹ️ Información y consejos"},
        {"command": "data", "description": "📦 Tus datos guardados"},
    ],

    # 6. FRANCUSKI (fr)
    "fr": [
        {"command": "day", "description": "☀️ Carte météo du jour"},
        {"command": "now", "description": "📡 Radar tactique (12 h)"},
        {"command": "trend", "description": "🔮 Tendance (14 jours)"},
        {"command": "report", "description": "⚙️ Heures des bulletins"},
        {"command": "city", "description": "🌍 Changer de position"},
        {"command": "invite", "description": "💌 Inviter au groupe"},
        {"command": "info", "description": "ℹ️ Infos et astuces"},
        {"command": "data", "description": "📦 Vos données enregistrées"},
    ],
}

# Norweski: Telegram raportuje bokmål jako "nb" albo "no" — wgrywamy oba.
COMMANDS_NO = [
    {"command": "day", "description": "☀️ Dagsvarsel"},
    {"command": "now", "description": "📡 Taktisk radar (12 timer)"},
    {"command": "trend", "description": "🔮 14-dagers trend"},
    {"command": "report", "description": "⚙️ Rapporttider"},
    {"command": "city", "description": "🌍 Endre posisjon"},
    {"command": "invite", "description": "💌 Inviter til gruppe"},
    {"command": "info", "description": "ℹ️ Info og tips"},
    {"command": "data", "description": "📦 Dine lagrede data"},
]
COMMANDS_BY_LANG["no"] = COMMANDS_NO
COMMANDS_BY_LANG["nb"] = COMMANDS_NO

# KROK 1: Resetujemy wszystko we wszystkich językach i zakresach
# (usuwa też stare pozycje: menu, tips/porady, privacy, my_data,
#  forget_location, delete_me — one zostają w bocie, ale nie w menu)
print("🧨 KROK 1: Reset nuklearny we wszystkich możliwych zakresach...")
LANGS_TO_CLEAR = [None] + [lang for lang in COMMANDS_BY_LANG.keys() if lang != "default"]

for scope in SCOPES:
    for lang in LANGS_TO_CLEAR:
        payload = {"scope": scope}
        if lang:
            payload["language_code"] = lang
        requests.post(f"{BASE}/deleteMyCommands", json=payload, timeout=10)
print("✅ Stare komendy całkowicie usunięte!\n")

# KROK 2: Wgrywamy komendy z naszego słownika
print("🌍 KROK 2: Wgrywanie nowych menu językowych (8 pozycji)...")
for lang_key, commands in COMMANDS_BY_LANG.items():
    for scope in SCOPES:
        payload = {"scope": scope, "commands": commands}
        if lang_key != "default":
            payload["language_code"] = lang_key

        r = requests.post(f"{BASE}/setMyCommands", json=payload, timeout=10)
        status = "✅ OK" if r.json().get('ok') else f"❌ BŁĄD: {r.json()}"

        wyswietlany_jezyk = "DOMYŚLNE MENU (EN)" if lang_key == "default" else lang_key.upper()
        print(f"{wyswietlany_jezyk} -> {scope['type']}: {status}")

print("\n🎉 ZAKOŃCZONE! Menu jest zaktualizowane.")
