import os
import requests
from dotenv import load_dotenv

# Lokalne uruchomienie: python update_menu.py ładuje zmiany.
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
# BAZA KOMEND DLA RÓŻNYCH JĘZYKÓW
# ==============================================================
COMMANDS_BY_LANG = {
    # 1. DOMYŚLNE MENU GLOBALNE (Angielski dla wszystkich nieobsługiwanych krajów, np. Włochy)
    "default": [
        {"command": "day", "description": "☀️ Daily weather card"},
        {"command": "now", "description": "📡 Tactical radar (12 hrs)"},
        {"command": "trend", "description": "🔮 14-day weather trend"},
        {"command": "menu", "description": "⚙️ Change report hours"},
        {"command": "city", "description": "🌍 Change your location"},
        {"command": "invite", "description": "💌 Invite or add to group"},
        {"command": "info", "description": "ℹ️ Brief bot manual"},
        {"command": "tips", "description": "💡 Useful tricks & features"}
    ],
    
    # 2. POLSKI (pl) - Wskazany wprost dla telefonów z językiem PL
    "pl": [
        {"command": "day", "description": "☀️ Dzienna karta pogodowa"},
        {"command": "now", "description": "📡 Radar taktyczny (12 godzin)"},
        {"command": "trend", "description": "🔮 Trend pogody (14 dni)"},
        {"command": "menu", "description": "⚙️ Zmień godziny raportów"},
        {"command": "miasto", "description": "🌍 Zmień swoją lokalizację"},
        {"command": "zapros", "description": "💌 Zaproś lub dodaj do grupy"},
        {"command": "info", "description": "ℹ️ Krótka instrukcja obsługi"},
        {"command": "porady", "description": "💡 Przydatne triki i funkcje"}
    ],

    # 3. ANGIELSKI (en)
    "en": [
        {"command": "day", "description": "☀️ Daily weather card"},
        {"command": "now", "description": "📡 Tactical radar (12 hrs)"},
        {"command": "trend", "description": "🔮 14-day weather trend"},
        {"command": "menu", "description": "⚙️ Change report hours"},
        {"command": "city", "description": "🌍 Change your location"},
        {"command": "invite", "description": "💌 Invite or add to group"},
        {"command": "info", "description": "ℹ️ Brief bot manual"},
        {"command": "tips", "description": "💡 Useful tricks & features"}
    ],

    # 4. NIEMIECKI (de)
    "de": [
        {"command": "day", "description": "☀️ Tägliche Wetterkarte"},
        {"command": "now", "description": "📡 Taktisches Radar (12 Std)"},
        {"command": "trend", "description": "🔮 14-Tage-Wettertrend"},
        {"command": "menu", "description": "⚙️ Berichtszeiten ändern"},
        {"command": "city", "description": "🌍 Standort ändern"},
        {"command": "invite", "description": "💌 In Gruppe einladen"},
        {"command": "info", "description": "ℹ️ Kurzes Bot-Handbuch"},
        {"command": "tips", "description": "💡 Nützliche Tipps"}
    ],

    # 5. HISZPAŃSKI (es)
    "es": [
        {"command": "day", "description": "☀️ Tarjeta meteorológica"},
        {"command": "now", "description": "📡 Radar táctico (12 hrs)"},
        {"command": "trend", "description": "🔮 Tendencia (14 días)"},
        {"command": "menu", "description": "⚙️ Cambiar horas de envío"},
        {"command": "city", "description": "🌍 Cambiar ubicación"},
        {"command": "invite", "description": "💌 Invitar al grupo"},
        {"command": "info", "description": "ℹ️ Breve manual del bot"},
        {"command": "tips", "description": "💡 Trucos y funciones"}
    ],

    # 6. FRANCUSKI (fr)
    "fr": [
        {"command": "day", "description": "☀️ Carte météo du jour"},
        {"command": "now", "description": "📡 Radar tactique (12 h)"},
        {"command": "trend", "description": "🔮 Tendance (14 jours)"},
        {"command": "menu", "description": "⚙️ Modifier les heures"},
        {"command": "city", "description": "🌍 Changer de position"},
        {"command": "invite", "description": "💌 Inviter au groupe"},
        {"command": "info", "description": "ℹ️ Bref manuel du bot"},
        {"command": "tips", "description": "💡 Astuces et fonctions"}
    ]
}

# KROK 1: Resetujemy wszystko we wszystkich językach i zakresach
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
print("🌍 KROK 2: Wgrywanie nowych menu językowych...")
for lang_key, commands in COMMANDS_BY_LANG.items():
    for scope in SCOPES:
        payload = {"scope": scope, "commands": commands}
        if lang_key != "default":
            payload["language_code"] = lang_key
            
        r = requests.post(f"{BASE}/setMyCommands", json=payload, timeout=10)
        status = "✅ OK" if r.json().get('ok') else f"❌ BŁĄD: {r.json()}"
        
        # POPRAWKA: Wyświetlanie w konsoli nie myli już, że domyślny to EN!
        wyswietlany_jezyk = "DOMYŚLNE MENU (PL)" if lang_key == "default" else lang_key.upper()
        print(f"{wyswietlany_jezyk} -> {scope['type']}: {status}")

print("\n🎉 ZAKOŃCZONE! Menu jest zaktualizowane.")