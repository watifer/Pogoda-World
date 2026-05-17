# 🌍 Pogoda World 🌤️

**Twój osobisty, inteligentny asystent pogodowy na Telegramie.**

Pogoda World to zaawansowany bot pogodowy, który codziennie dostarcza piękne, generowane graficznie karty z precyzyjną prognozą dopasowaną do Twojej lokalizacji. Koniec z nudnymi tabelkami i dezinformacją – nasz system analizuje dane z najlepszych modeli (m.in. norweskiego Yr.no) i podaje je w przejrzystej, "gazetowej" formie.
Używa też sztucznej inteligencji (LLM / Groq API) do analizy i tworzenia naturalnych podsumowań dla użytkownika, ale tylko w momentach, gdy to podsumowanie przebije się przez drabinę weryfikacji zgodności z surowymi danymi.
W praktyce oznacza to, że w prognozach, w których nic się specjalnie nie dzieje ( stabilnych) AI ma szansę pokonać weryfikator i wkroczyć do akcji ze swoją opinią.
Projekt Pogoda World dostępny jest na razie tylko w języku polskim.

Pogoda World is an advanced weather bot that daily delivers beautiful, custom-generated visual cards with precise forecasts tailored to your location. Say goodbye to boring tables and misinformation—our system analyzes data from the best meteorological models (including the Norwegian Yr.no) and presents it in a clear, newspaper-style layout.
It also utilizes Artificial Intelligence (LLM / Groq API) to analyze data and generate natural-sounding summaries for the user. However, the AI is only allowed to speak if its output successfully passes a strict verification ladder ensuring complete compliance with the raw meteorological data.
In practice, this means that during stable, uneventful weather conditions, the AI gets the green light to clear the strict verification filters and step in with its own contextual insights.
Currently, the Pogoda World project is available only in Polish.
---

## 🚀 Jak zacząć korzystać z bota?
Z aplikacji może skorzystać każdy, kto posiada komunikator Telegram.

1. Kliknij w poniższy link zaproszeniowy, aby uruchomić bota:
   👉 **[DOŁĄCZ DO POGODA WORLD](https://t.me/Twoja_pogoda_bot?start=ODM5NjgzMjgyMA)**
2. Po wejściu wyślij botowi 📍 **Pinezkę z lokalizacją** ze swojego telefonu.
3. Gotowe! Bot automatycznie dopasuje strefę czasową i wyśle Ci pierwszą prognozę.

### 🌟 Główne funkcje
* **Gazeta Codzienna (Raporty)** – Ustaw własne godziny (np. 07:00 i 15:00) w wygodnym panelu (`/menu`). Bot obudzi Cię piękną kartą podsumowującą to, co najważniejsze.
* **Radar Taktyczny (`/now`)** – Wychodzisz z domu i nie wiesz, czy brać parasol? Radar taktyczny natychmiast wygeneruje kartę z chirurgiczną prognozą na 12 najbliższych godzin (odróżniając mżawkę od ulewy i ciągły śnieg od przelotnego!).
* **Trend 14-dniowy (`/future`)** – Hybrydowa, długoterminowa prognoza analizująca zjawiska w szerszym kontekście.
* **Smart Alerts** – Wewnętrzny silnik, który ostrzeże Cię przed załamaniami ciśnienia, niszczącymi porywami wiatru czy złą jakością powietrza (integracja z czujnikami Airly).
* **Tryb Wakacyjny (Auto-Timezone)** – Wyjeżdżasz? Wyślij pinezkę z drugiego końca świata. Bot automatycznie wykryje nową strefę czasową i wyśle Ci raporty w czasie lokalnym dla Twojego miejsca pobytu.

---

## 💻 Pod maską (Dla Developerów)

Kod udostępniony w tym repozytorium stanowi **portfolio technologiczne**. Aplikacja działa produkcyjnie, 24/7 na prywatnym serwerze (Oracle Cloud), obsługując zapytania użytkowników w modelu bezstanowym (*stateless*). 

Klucze API (Google, Telegram, Airly, Open-Meteo) oraz dane użytkowników są rygorystycznie chronione i nie znajdują się w repozytorium.

### 🏗️ Architektura i Główne Technologie
* **Język:** Python 3.10+
* **Renderowanie Kart (UI):** Biblioteka `Pillow` (PIL) - własny, pikselowo perfekcyjny silnik rysujący zaokrąglone kafelki, inteligentnie łamiący tekst i pozycjonujący ikony pogodowe.
* **Agregator Danych:** Równoległe odpytywanie API **Yr.no** (Norweski Instytut Met.) oraz **Open-Meteo**. Silnik wybiera optymalne dane lub scala je hybrydowo w zależności od komendy.
* **Baza Danych (Zasada DRY):** Google Sheets API (`gspread`). Arkusz Google pełni rolę wygodnego systemu CRM, a bot inteligentnie zarządza limitami zapytań i blokadami (Smart Auto-Cleanup).
* **Deterministyczny Silnik Tekstowy:** Zamiast polegać na surowych kodach WMO, aplikacja posiada rygorystyczny "Drabinkowy Klasyfikator" (`forecast_text.py`), który przelicza na żywo milimetry opadów i warstwy chmur, pilnując, by ikony zawsze były w 100% zgodne z opisem tekstowym.

---
*Tworzone z pasją do meteorologii, UX i czystego kodu. ☁️☀️*