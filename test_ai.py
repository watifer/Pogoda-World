import os
from dotenv import load_dotenv

# 1. NAJPIERW wczytujemy klucze z pliku .env do pamięci!
load_dotenv()

# 2. DOPIERO TERAZ importujemy plik, który tych kluczy używa
from ai_client import _groq_chat, _parse_json_object

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

oryginal = "po 19:00 bardzo szybkie ochłodzenie (o 6°C)"
max_len = 65

system = (
    "Jesteś precyzyjnym redaktorem krótkich komunikatów pogodowych. "
    "Twoim jedynym zadaniem jest zgrabnie sparafrazować zdanie podane przez użytkownika, "
    "zachowując 100% oryginalnych faktów."
)
    
user = (
    "Sparafrazuj ten tekst, trzymając się poniższych żelaznych zasad:\n"
    f"- LIMIT: tekst musi być krótszy niż {max_len} znaków.\n"
    "- NIE dodawaj ŻADNYCH nowych informacji, zjawisk, ani porad.\n"
    "- NIE dodawaj ŻADNYCH nowych liczb ani godzin.\n"
    "- Jeśli w tekście są liczby (np. temp, km/h) lub godziny, skopiuj je dokładnie 1:1.\n"
    "- Używaj naturalnego, profesjonalnego języka.\n"
    "- Zwróć WYŁĄCZNIE czysty format JSON: {{\"text\": \"tutaj twoja parafraza\"}}\n\n"
    f"TEKST ORYGINALNY: {oryginal}"
)

messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

print("📡 Wysyłam pełny prompt pogodowy do Groq...\n")

try:
    surowa_odpowiedz = _groq_chat(GROQ_MODEL, messages)
    print("====== SUROWA ODPOWIEDŹ MODELU ======")
    print(surowa_odpowiedz)
    print("=====================================\n")
    
    # Próbujemy to teraz przeparsować naszą funkcją
    parsed = _parse_json_object(surowa_odpowiedz)
    print(f"Czy nasz skrypt umiał to przeczytać jako JSON? -> {parsed}")
    
except Exception as e:
    print(f"Wystąpił błąd: {e}")