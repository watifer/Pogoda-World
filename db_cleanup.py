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
    """Block an existing Users record and clear its location profile."""
    print(f"🧹 Oznaczam użytkownika {chat_id} jako ZABLOKOWANEGO (Soft Delete)...")
    reason = reason if reason in users_store.BLOCK_REASONS else "unknown"

    # --- Users: jedyny rejestr użytkowników i grup ---
    try:
        users_ws = users_store.get_ws(gc)
        ok = users_store.set_blocked(users_ws, chat_id, reason, users_store.now_iso(), clear_profile=True)
        if ok:
            print(f"✅ [Users] access_status=blocked (reason: {reason}), profil wyczyszczony.")
        else:
            print(f"ℹ️ [Users] Brak wiersza {chat_id} w zakładce Users — pomijam.")
    except Exception as e:
        # Propagate quota errors so the poller can back off before any further Sheets access.
        users_store._reraise_rate_limit(e)
        print(f"⚠️ [Users] Błąd oznaczania blocked: {e}")
