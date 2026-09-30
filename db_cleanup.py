import gspread

import users_store


# Mapowanie opisu błędu Telegrama na powód blokady (kolumna blocked_reason w Users)
REASON_MAP = [
    ("blocked by the user", "telegram_blocked"),
    ("kicked from the group", "kicked_from_group"),
    ("user is deactivated", "user_deactivated"),
    ("chat not found", "chat_not_found"),
]


def classify_block_reason(description: str) -> str:
    """Tłumaczy opis błędu Telegrama na enum blocked_reason z planu migracji."""
    desc = (description or "").lower()
    for needle, reason in REASON_MAP:
        if needle in desc:
            return reason
    return "unknown"


def is_bot_blocked(response):
    """
    Sprawdza, czy odpowiedź z Telegrama to definitywna blokada/usunięcie,
    analizując treść błędu (description), a nie tylko status code.
    """
    if response.status_code not in (400, 403):
        return False

    try:
        data = response.json()
        if not data.get("ok"):
            desc = data.get("description", "").lower()
            trigger_words = [
                "blocked by the user",
                "kicked from the group",
                "user is deactivated",
                "chat not found"
            ]
            if any(word in desc for word in trigger_words):
                return True
    except Exception:
        pass

    return False


def mark_user_as_blocked(gc, chat_id, reason="unknown"):
    """
    Soft-Delete z podwójnym zapisem (czas migracji na zakładkę Users):

    1) USERS (nowy rejestr dostępu): access_status=blocked + blocked_at/blocked_reason
       + czyszczenie pól profilu (lokalizacji).
    2) FORMULARZ (legacy): prefix BLOCKED_ przy Chat ID — dopóki scheduler
       (main_card.py) czyta użytkowników z Formularz, to ten zapis faktycznie
       wstrzymuje wysyłkę raportów.

    Użytkownik legacy (bez wiersza w Users) jest oznaczany tylko w Formularz.
    """
    print(f"🧹 Oznaczam użytkownika {chat_id} jako ZABLOKOWANEGO (Soft Delete)...")
    reason = reason if reason in users_store.BLOCK_REASONS else "unknown"

    # --- KROK 1: Users (nowy system) ---
    try:
        users_ws = users_store.get_ws(gc)
        ok = users_store.set_blocked(users_ws, chat_id, reason, users_store.now_iso(), clear_profile=True)
        if ok:
            print(f"✅ [Users] access_status=blocked (reason: {reason}), profil wyczyszczony.")
        else:
            print(f"ℹ️ [Users] Brak wiersza {chat_id} w zakładce Users (użytkownik legacy) — pomijam.")
    except Exception as e:
        print(f"⚠️ [Users] Błąd oznaczania blocked: {e}")

    # --- KROK 2: legacy BLOCKED_ w Formularz (fallback, bez zmian) ---
    try:
        main_sheet = gc.open("Pogoda_Users").worksheet("Formularz")

        # Pobieramy całą kolumnę B (Chat ID) - to tylko 1 tanie zapytanie API
        col_values = main_sheet.col_values(2)

        cells_to_update = []
        for i, val in enumerate(col_values):
            # Dokładne porównanie (eliminuje problem z findall)
            if str(val).strip() == str(chat_id):
                # Zmieniamy np. 12345 na BLOCKED_12345
                cells_to_update.append(gspread.Cell(row=i+1, col=2, value=f"BLOCKED_{chat_id}"))

        if cells_to_update:
            main_sheet.update_cells(cells_to_update) # 1 zapytanie hurtowe!
            print(f"✅ Oznaczono {len(cells_to_update)} wierszy jako BLOCKED. Limit zwolniony.")

    except Exception as e:
        print(f"❌ Błąd podczas miękkiego usuwania: {e}")
