![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-2CA5E0?logo=telegram&logoColor=white)




# 🌍 Pogoda World 🌤️

**Your personal intelligent weather assistant on Telegram.**

**Pogoda World** is an advanced weather bot that delivers beautiful, custom-generated visual cards with precise forecasts tailored to your exact location. Say goodbye to boring tables and misinformation—our system analyzes data from the best meteorological models (including the Norwegian **Yr.no**) and presents it in a clear, newspaper-style layout.

It utilizes **Artificial Intelligence (LLM / Groq API)** to generate natural-sounding summaries. However, the AI operates under strict supervision: it is only allowed to speak if its output successfully passes a rigid *"verification ladder"*, ensuring complete compliance with the raw meteorological data. The bot natively supports **6 languages** (**PL, EN, DE, FR, ES, NO**) and automatically adapts to your device's settings.

---

## 🚀 How to get started?

The application currently operates in a **Closed Beta / Invite-Only model** to ensure the highest quality, server stability, and lightning-fast graphic generation. 

To join the community, check current server availability, and get your instant access link, visit our official portal:

👉 **[Pogoda World - Official Invite Page](https://watifer.github.io/Pogoda-World/invite/)**

---
*💡 Already know an existing user? You can also ask them to generate a personal invitation link for you directly inside the bot using the `/invite` command!*
*   **Set your location (New!):** Use the built-in, modern mini-app (**Telegram WebApp**) by pressing the **📍 Update with GPS** button, or use the traditional `/city` command.
*   **All set!** The bot will automatically detect your timezone, fetch the localized city name, and immediately send you your first personalized forecast.

---

## 🌟 Main Features

*   📰 **Daily Newspaper (Reports)** – Get the weather exactly when you need it. Set your custom times (e.g., 07:00 and 15:00) in the convenient control panel (`/menu`). The bot will proactively reach out to you with a beautiful, ready-to-read card.
*   ⚡ **Tactical Radar (`/now`)** – Leaving the house and unsure if you need an umbrella? The radar generates a card with a surgical forecast for the next 12 hours (flawlessly distinguishing drizzle from a downpour, and continuous snow from flurries!).
*   🔮 **14-Day Trend (`/future`)** – A hybrid, long-term forecast analyzing phenomena in a broader, two-week context.
*   ⚠️ **Smart Alerts** – An internal engine warns you of sudden pressure drops, damaging wind gusts, or poor air quality.
*   🌍 **Multilingual (i18n)** – The bot's interface, geographical names, and weather descriptions are translated on the fly into **PL, EN, DE, FR, ES, or NO**.
*   ✈️ **Vacation Mode (Auto-Timezone)** – Traveling? Use the GPS button from the other side of the world. The bot will automatically translate the new location, adjust your timezone, and deliver reports according to your new local time.

---

## 💻 Under the Hood (For Developers)

The code shared in this repository serves as a **public technology portfolio**. The application runs in a production environment 24/7 on a private **Oracle Cloud** server, handling user requests in a stateless model.  
*(API keys such as Telegram Token, Google Credentials, Groq API, and user data are strictly protected by `.env` environment variables and are not included in the repository).*

### 🏗️ Architecture and Main Technologies

*   **Language:** Python 3.10+
*   **Card Rendering (UI):** `Pillow (PIL)` library - a custom, pixel-perfect engine that draws rounded tiles, adds shadows, intelligently wraps text, and positions dynamic weather icons.
*   **Frontend Application (WebApp):** Native integration with `Telegram WebApp` (HTML/Vanilla JS) allowing instant and secure transmission of GPS coordinates straight into the Python environment.
*   **Data Aggregator:** Parallel, asynchronous API polling from **Yr.no** (Norwegian Meteorological Institute) and **Open-Meteo**. The engine selects optimal data or merges them in a hybrid way.
*   **Database (DRY Principle):** Google Sheets API (`gspread`). A Google Sheet acts as a flexible CRM system (easy database overview). The bot features a built-in **Smart Auto-Cleanup** to automatically purge inactive accounts from the database.
*   **Deterministic Text Engine & AI:** Instead of relying on raw WMO codes, the application calculates precipitation millimeters, pressure, and cloud layers on the fly via a cross-classifier. This guarantees 100% consistency between the text description and the presented icons, with simultaneous LLM support for stable forecasts.

### 🌍 Country index (static data file — deployment requirement)

Text location search recognises an explicit country in the query (`Tokio, Japonia`,
`Tokio, Japan`, `Tokio, JP`, `Bath, UK`, `Hel,PL`). Country names come from a
**versioned, committed data file**:

```
data/countries/country_index.json     # generated offline, read at runtime
```

* The runtime (`country_index.py`) uses **only the standard library**. `Babel`
  and `pycountry` are generator-only tools (`requirements-tools.txt`) and are
  **not** installed in production.
* Source and versions are stored inside the file (`meta.source`) and documented
  in `data/countries/NOTICE.md` (CLDR/Unicode + ISO 3166-1 attribution).
* **The file must be deployed.** The loader is fail-closed on purpose: a missing
  or corrupted index disables text location search (`GEOCODE_ERROR`, the same
  `geo_conn_err` message) instead of silently searching without a country
  filter. GPS, map pins and the WebApp do not use this file and keep working.
  There is intentionally **no fallback** to a hardcoded country list.
* After deploying, check the startup log for one line:
  `[COUNTRY_INDEX] Wczytano indeks krajów: 249 krajów, …` (an error line means
  the file did not reach the server).
* Regenerating (deterministic, byte-for-byte) — only when CLDR/ISO data changes:

```bash
pip install -r requirements-tools.txt
python tools/build_country_index.py            # rewrite the JSON
python tools/build_country_index.py --check    # verify it is up to date (CI)
```

* CI workflow `.github/workflows/country_index.yml` runs `--check`, so the commit
  can never drift from the generator.
* Match rules (deliberate, tested): full-phrase match after normalisation — no
  prefixes, no fuzzy matching, no inflection, no transliteration, Latin script
  only. Ambiguous names (e.g. `Kongo`) are never resolved by picking a code, and
  names like `Georgia`/`Włochy` stay administrative context because they are also
  real admin units (`Atlanta, Georgia`, `Warszawa, Włochy`).


<p align="center">
  <img src="info/day-card.jpg" alt="Today's forecast" width="350"><br><br>
  <img src="info/now-card.jpg" alt="Hourly forecast" width="350"><br><br>
  <img src="info/trend-card.jpg" alt="14-day trend" width="350">
</p>




---

> *Built with passion for meteorology, UX, and clean code.* ☁️☀️