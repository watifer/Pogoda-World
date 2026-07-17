import os
import json
import time
import requests
from typing import Optional

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_MODEL_FALLBACK = os.environ.get("GROQ_MODEL_FALLBACK", "llama-3.1-8b-instant").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def _groq_chat(model: str, messages: list, timeout: int = 8) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("Brak klucza GROQ_API_KEY w pliku .env")
        
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4, # Lekka swoboda twórcza
        "max_tokens": 120,
    }
    
    for attempt in range(2):
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=timeout)
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(0.5)
            continue
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()
        
    return ""

def _parse_json_object(s: str) -> Optional[dict]:
    s = s.strip()
    s = s.replace("{{", "{").replace("}}", "}")
    
    if "```" in s:
        parts = s.split("```")
        if len(parts) >= 3:
            s = parts[1].strip()
            if s.startswith("json"):
                s = s[4:].strip()
            
    try:
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            clean_json = s[start:end + 1]
            obj = json.loads(clean_json)
            return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    return None

def paraphrase_short_pl(text: str, max_len: int) -> Optional[str]:
    return None


# DODANY PARAMETR 'lang' (Domyślnie "pl")
def wk_candidate_from_facts(facts: dict, max_len: int, mode: str = "comfort", lang: str = "pl") -> Optional[dict]:
    if not facts or not isinstance(facts, dict):
        return None

    comfort_h = facts.get("comfort_hour")
    sun_h = facts.get("sunniest_hour")
    ws = facts.get("window_start")
    we = facts.get("window_end")

    # Dynamiczne dopasowanie języka instrukcji (Możesz tu łatwo dodać "de", "es" itd.)
    if lang.lower() == "en":
        lang_instruction = (
            "- Write the response in ENGLISH.\n"
            "- Use natural English phrasing and a 24-hour time system (e.g., 14:00, 20:00).\n"
            "- Avoid phrase 'tomorrow'."
        )
        lang_name = "angielsku"
    else:
        # Fallback na polski
        lang_instruction = (
            "- Napisz jedno krótkie, naturalne zdanie po polsku.\n"
            "- Używaj naturalnego polskiego nazewnictwa i systemu 24-godzinnego.\n"
            "- BEZWZGLĘDNY ZAKAZ używania słowa 'jutro' lub 'jutra'!"
        )
        lang_name = "polsku"

    # WYMUSZANIE KOTWICY W PROMPCIE
    if mode == "comfort" and isinstance(comfort_h, int):
        constraint = f"- MUSISZ użyć w tekście dokładnie godziny {comfort_h:02d}:00.\n"
    elif mode == "sun" and isinstance(sun_h, int):
        constraint = f"- MUSISZ użyć w tekście dokładnie godziny {sun_h:02d}:00.\n"
    elif mode == "window" and isinstance(ws, int) and isinstance(we, int):
        constraint = f"- MUSISZ użyć w tekście zakresu od {ws:02d}:00 do {we:02d}:00 (lub między {ws:02d}:00 a {we:02d}:00).\n"
    elif mode == "window_start_only" and isinstance(ws, int):
        constraint = (
            f"- MUSISZ użyć w tekście dokładnie godziny {ws:02d}:00.\n"
            "- NIE podawaj godziny końca.\n"
            f"- Sens ma być taki: po tej godzinie wieczorem/w nocy warunki pogodowe będą stabilniejsze.\n"
        )
    else:
        return None  # Brak kotwicy -> awaryjne przerwanie
        
    system = (
        "Jesteś błyskotliwym, lifestylowym redaktorem pogodowym. "
        "Twoje teksty są naturalne, eleganckie i brzmią jak pisane przez człowieka. "
        "Zwracasz wyłącznie obiekt JSON."
    )

    user = (
        f"Na podstawie FAKTÓW napisz jedno krótkie, naturalne zdanie po {lang_name} (max 65 znaków). "
        "Jesteśmy w trakcie spokojnego, stabilnego dnia.\n\n"
        "Reguły:\n"
        "- Dopasuj ton do 'current_hour' (jeśli jest po 18:00, pisz o spokojnym wieczorze; rano o dobrym dniu).\n"
        f"{lang_instruction}\n"
        "- Tekst MUSI być akcjonowalny (wskazywać konkretną godzinę lub zakres).\n"
        f"{constraint}"
        "- Unikaj w kółko słowa 'spacer' / 'walk'. Używaj synonimów: relaks, wietrzenie, rower, czas na zewnątrz.\n"
        "- NIE zmyślaj opadów, wiatru, liczb ani godzin, których nie ma w faktach!\n"
        "- WAŻNE: Poniższe zdania to TYLKO inspiracja stylu. NIE KOPIUJ ich słowo w słowo. Wymyśl zawsze swoje własne, unikalne zdanie!\n\n"
        "PRZYKŁADY STYLU (Nie kopiuj ich):\n"
        "1. \"Przyjemny i stabilny wieczór. Najlepsze warunki będą po 20:00.\"\n"
        "2. \"Świetne warunki na zewnątrz. Najprzyjemniej będzie około 16:00.\"\n"
        "3. \"Około 14:00 zachmurzenie będzie najmniejsze, warto łapać słońce.\"\n"
        "4. \"Między 19:00 a 22:00 warunki na zewnątrz będą najbardziej stabilne.\"\n\n"
        "- Zwróć WYŁĄCZNIE JSON w formacie:\n"
        "{\"text\":\"...\",\"wx\":[\"window\"],\"kind\":\"opportunity\"}\n\n"
        f"FAKTY_JSON: {json.dumps(facts, ensure_ascii=False)}"
    )

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    for model in (GROQ_MODEL, GROQ_MODEL_FALLBACK):
        try:
            content = _groq_chat(model, messages, timeout=8)
            if os.environ.get("AI_DEBUG") == "1":
                print(f"[AI DEBUG] WK facts raw {model}: {content}")

            obj = _parse_json_object(content)
            if not obj:
                continue

            text = (obj.get("text") or "").strip()
            wx = obj.get("wx") or []
            kind = (obj.get("kind") or "generic").strip()

            if not text:
                continue
            if not isinstance(wx, list):
                wx = [str(wx)]
            wx = [str(x).strip() for x in wx if str(x).strip()]

            if len(text) > max_len:
                text = text[:max_len].rsplit(" ", 1)[0].rstrip() + "..."

            return {"text": text, "wx": wx, "kind": kind}
        except Exception as e:
            if os.environ.get("AI_DEBUG") == "1":
                print(f"[AI DEBUG] WK facts error {model}: {e}")
            time.sleep(0.2)

    return None