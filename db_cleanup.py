import gspread

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

def mark_user_as_blocked(gc, chat_id):
    """
    Soft-Delete. Zamiast kasować wiersze, dodaje prefix BLOCKED_ do Chat ID.
    Wykonywane 1 hurtowym zapytaniem do API Google.
    """
    print(f"🧹 Oznaczam użytkownika {chat_id} jako ZABLOKOWANEGO (Soft Delete)...")
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