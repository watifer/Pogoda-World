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
# BAZA KOMEND DLA RÓŻNYCH JĘZYKÓW (Z NOWĄ KOLEJNOŚCIĄ)
# ==============================================================
COMMANDS_BY_LANG = {
    # 1. DOMYŚLNE MENU GLOBALNE (EN - dla obcokrajowców bez własnego tłumaczenia)
    "default": [
        {"command": "day", "description": "☀️ Daily weather card"},
        {"command": "now", "description": "📡 Tactical radar (current 12 hours)"},
        {"command": "trend", "description": "🔮 14-day weather trend"},
        {"command": "menu", "description": "⚙️ Geo & hours settings panel"},
        {"command": "city", "description": "🌍 Change location by text"},
        {"command": "invite", "description": "💌 Invite or add to group"},
        {"command": "info", "description": "ℹ️ Brief bot manual"},
        {"command": "tips", "description": "💡 Useful tricks and features"}
    ],
    
    # 2. POLSKI (pl)
    "pl": [
        {"command": "day", "description": "☀️ Dzienna karta pogodowa"},
        {"command": "now", "description": "📡 Radar taktyczny (bieżące 12 godziny)"},
        {"command": "trend", "description": "🔮 Trend pogody (14 dni)"},
        {"command": "menu", "description": "⚙️ Panel ustawień Geo i godzin"},
        {"command": "miasto", "description": "🌍 Zmień miasto z klawiatury"},
        {"command": "zapros", "description": "💌 Zaproś lub dodaj do grupy"},
        {"command": "info", "description": "ℹ️ Krótka instrukcja obsługi"},
        {"command": "porady", "description": "💡 Przydatne triki i funkcje"}
    ],

    # 3. NIEMIECKI (de)
    "de": [
        {"command": "day", "description": "☀️ Tägliche Wetterkarte"},
        {"command": "now", "description": "📡 Taktisches Radar (aktuell)"},
        {"command": "trend", "description": "🔮 14-Tage-Wettertrend"},
        {"command": "menu", "description": "⚙️ Einstellungen für Geo & Zeit"},
        {"command": "city", "description": "🌍 Standort per Text ändern"},
        {"command": "invite", "description": "💌 Einladen oder zur Gruppe hinzufügen"},
        {"command": "info", "description": "ℹ️ Kurzes Bot-Handbuch"},
        {"command": "tips", "description": "💡 Nützliche Tricks und Funktionen"}
    ],

    # 4. HISZPAŃSKI (es)
    "es": [
        {"command": "day", "description": "☀️ Tarjeta meteorológica diaria"},
        {"command": "now", "description": "📡 Radar táctico (actual)"},
        {"command": "trend", "description": "🔮 Tendencia del tiempo (14 días)"},
        {"command": "menu", "description": "⚙️ Panel de ajustes de Geo y hora"},
        {"command": "city", "description": "🌍 Cambiar ubicación por texto"},
        {"command": "invite", "description": "💌 Invitar o añadir al grupo"},
        {"command": "info", "description": "ℹ️ Breve manual del bot"},
        {"command": "tips", "description": "💡 Trucos y funciones útiles"}
    ],

    # 5. FRANCUSKI (fr)
    "fr": [
        {"command": "day", "description": "☀️ Carte météo du jour"},
        {"command": "now", "description": "📡 Radar tactique (actuel)"},
        {"command": "trend", "description": "🔮 Tendance météo (14 jours)"},
        {"command": "menu", "description": "⚙️ Paramètres géo et horaires"},
        {"command": "city", "description": "🌍 Changer de lieu par texte"},
        {"command": "invite", "description": "💌 Inviter ou ajouter au groupe"},
        {"command": "info", "description": "ℹ️ Bref manuel du bot"},
        {"command": "tips", "description": "💡 Astuces et fonctions utiles"}
    ],

    # 6. NORWESKI (no) - Miejsce na przyszłość (odkomentuj, gdy będziesz gotowy)
    "no": [
        {"command": "day", "description": "☀️ Daglig værkort"},
        {"command": "now", "description": "📡 Taktisk radar (nå)"},
        {"command": "trend", "description": "🔮 14-dagers værvarsel"},
        {"command": "menu", "description": "⚙️ Geo- og tidsinnstillinger"},
        {"command": "city", "description": "🌍 Endre posisjon med tekst"},
        {"command": "invite", "description": "💌 Inviter eller legg til i gruppe"},
        {"command": "info", "description": "ℹ️ Kort bot-manual"},
        {"command": "tips", "description": "💡 Nyttige triks og funksjoner"}
    ]
}
# ==============================================================

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
        
        wyswietlany_jezyk = "GLOBALNE (EN)" if lang_key == "default" else lang_key.upper()
        print(f"{wyswietlany_jezyk} -> {scope['type']}: {status}")

print("\n🎉 ZAKOŃCZONE! Menu jest zaktualizowane.")