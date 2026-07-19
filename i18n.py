import re

STRINGS = {
  "pl": {
    "report_morning": "raport poranny",
    "report_afternoon": "raport popołudniowy",
    "report_evening": "aktualizacja wieczorna",
    "tactical_radar": "Radar taktyczny",
    "long_range": "Prognoza długoterminowa",
    "outlook_14d": "Prognoza 14-dniowa",
    "section_today": "Prognoza na dziś",
    "section_rest_day": "Reszta dnia",
    "section_next_hours": "Najbliższe godziny",
    "watch_out": "Uważaj",
    "good_to_know": "Dziś warto wiedzieć",
    "next_days": "Najbliższe dni",
    "tomorrow": "Jutro",
    "next_14d_trend": "Trend na kolejne 14 dni",
    "next_weekend": "Przyszły weekend",
    "blk_morning": "Rano",
    "blk_afternoon": "Popołudnie",
    "blk_evening": "Wieczór",
    "blk_night": "Noc",
    "blk_tomorrow_morning": "Jutro rano",
    "hero_storm_trend": "Uwaga na burze w nadchodzących dniach",
    "hero_wind_trend": "Uwaga: Niezwykle wietrzne i niebezpieczne dni",
    "hero_snow_trend": "Kierunek na chłodne, śnieżne dni",
    "hero_rain_trend": "Przewaga deszczowej, mokrej pogody",
    "hero_sun_trend": "Przewaga słonecznej, wyżowej pogody",
    "hero_showers_trend": "Zmienna pogoda z okresowymi opadami",
    "hero_partly_trend": "Większość dni pogodnych i przejściowych",
    "hero_overcast_trend": "Przewaga pochmurnej aury",
    "drizzle": "mżawka",
    "light_rain": "lekki deszcz",
    "rain": "deszcz",
    "heavy_rain": "silny deszcz",
    "downpour": "ulewa",
    "light_snow": "lekki śnieg",
    "snow": "śnieg",
    "heavy_snow": "intensywny śnieg",
    "sleet": "deszcz ze śniegiem",
    "sleet_short": "deszcz/śnieg",
    "storms": "burze",
    "fog": "mgła",
    "from": "od",
    "until": "do",
    "after": "po",
    "stronger_after": "silniej po",
    "wind_up_to": "wiatr do",
    "feels_like_prefix": "odcz. ",
    "clear": "Clear sky",
    "sunny": "Sunny",
    "partly": "Partly cloudy",
    "overcast": "Overcast",
    "section_hourly_from": "Prognoza godzinowa od {h:02d}:00",
    "data_from": "dane z",
    "source_label": "Źródło:",
    
  },
  "en": {
    "report_morning": "morning report",
    "report_afternoon": "afternoon report",
    "report_evening": "evening update",
    "tactical_radar": "Tactical radar",
    "long_range": "Long-range forecast",
    "outlook_14d": "14-day outlook",
    "section_today": "Today's forecast",
    "section_rest_day": "Rest of day",
    "section_next_hours": "Next hours",
    "watch_out": "Watch out",
    "good_to_know": "Good to know",
    "next_days": "Next days",
    "tomorrow": "Tomorrow",
    "next_14d_trend": "14-day trend",
    "next_weekend": "Next weekend",
    "blk_morning": "Morning",
    "blk_afternoon": "Afternoon",
    "blk_evening": "Evening",
    "blk_night": "Night",
    "blk_tomorrow_morning": "Tomorrow morning",
    "hero_storm_trend": "Watch out for storms in the coming days",
    "hero_wind_trend": "Warning: Extremely windy and dangerous days",
    "hero_snow_trend": "Heading towards cold, snowy days",
    "hero_rain_trend": "Predominantly rainy, wet weather",
    "hero_sun_trend": "Predominantly sunny, clear weather",
    "hero_showers_trend": "Unstable weather with occasional rain",
    "hero_partly_trend": "Mostly clear and transition days",
    "hero_overcast_trend": "Predominantly cloudy skies",
    "drizzle": "drizzle",
    "light_rain": "light rain",
    "rain": "rain",
    "heavy_rain": "heavy rain",
    "downpour": "downpour",
    "light_snow": "light snow",
    "snow": "snow",
    "heavy_snow": "heavy snow",
    "sleet": "sleet",
    "sleet_short": "sleet",
    "storms": "storms",
    "fog": "fog",
    "from": "from",
    "until": "until",
    "after": "after",
    "stronger_after": "stronger after",
    "wind_up_to": "wind up to",
    "feels_like_prefix": "feels ",
    "clear": "Clear sky",
    "sunny": "Sunny",
    "partly": "Partly cloudy",
    "overcast": "Overcast",
    "section_hourly_from": "Hourly forecast from {h:02d}:00",
    "data_from": "data from",
    "source_label": "Source:",
    "search_loc": "🔍 Searching map...",
    "search_fail": "❌ Could not find this place. Try adding the country, e.g., `/city name, Poland`.",
    "search_success": "✅ *Location updated!*\n\n📍 Recognized: {city}\n🌍 Full map address: {address}\n\n💡 *Wrong place?* Be more specific, click, paste it and correct e.g.:\n👉 `/city {query}, <postal code>, ,country>`.",
    "search_err": "⚠️ Google Server error. Try again later."
  }
}

DAYS_FULL = {
  "pl": ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"],
  "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}

DAYS_SHORT = {
  "pl": ["Pn", "Wt", "Śr", "Czw", "Pt", "Sob", "Ndz"],
  "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}

def t(lang: str, key: str, **kw) -> str:
    """Pobiera przetłumaczony tekst ze STRINGS. Fallback do PL, a potem do samego klucza."""
    d = STRINGS.get(lang) or STRINGS["pl"]
    s = d.get(key) or STRINGS["pl"].get(key) or key
    return s.format(**kw) if kw else s


# =====================================================================
# SŁOWNIKI DYNAMICZNEGO TŁUMACZA (EXACT MAPS)
# =====================================================================
EXACT_MAPS = {
    "en": {
        "bezchmurnie, pogoda jak kryształ": "Clear sky, crystal perfect",
        "bezchmurnie": "Clear sky",
        "słonecznie": "Sunny",
        "pogodnie": "Sunny",
        "przejaśnienia": "Partly cloudy",
        "dużo chmur": "Mostly cloudy",
        "pochmurno": "Overcast",
        "śnieg, potem deszcz": "Snow, then rain",
        "deszcz ze śniegiem": "Sleet",
        "rano mgły, po poł. burze": "Morning fog, afternoon storms",
        "słonecznie, po poł. burze": "Sunny, afternoon storms",
        "przelotne burze": "Passing storms",
        "rano mgły, w dzień przelotny śnieg": "Morning fog, passing snow during the day",
        "rano mgły, po poł. śnieg": "Morning fog, afternoon snow",
        "mglisto i śnieżnie": "Foggy and snowy",
        "słońce i przelotny śnieg": "Sun and passing snow",
        "rano śnieg": "Snow in the morning",
        "po południu śnieg": "Snow in the afternoon",
        "rano mgły, potem przel. mżawka": "Morning fog, then passing drizzle",
        "rano mgły, potem przel. deszcz": "Morning fog, then passing rain",
        "rano mgły, po poł. mżawka": "Morning fog, afternoon drizzle",
        "rano mgły, po poł. deszcz": "Morning fog, afternoon rain",
        "mgły i mżawka": "Fog and drizzle",
        "mgły i deszcz": "Fog and rain",
        "słońce i przelotna mżawka": "Sun and passing drizzle",
        "słońce i przelotny deszcz": "Sun and passing rain",
        "rano mżawka": "Drizzle in the morning",
        "po poł. mżawka": "Drizzle in the afternoon",
        "rano deszcz": "Rain in the morning",
        "po południu deszcz": "Rain in the afternoon",
        "rano mgły, w dzień słońce": "Morning fog, sunny during the day",
        "rano mgły, przejaśnienia": "Morning fog, partly cloudy",
        "rano mgły, dużo chmur": "Morning fog, mostly cloudy",
        "śnieg z deszczem": "Sleet",
        "śnieżnie": "Snowy",
        "przelotny śnieg": "Passing snow",
        "deszczowo": "Rainy",
        "ciągła mżawka": "Continuous drizzle",
        "przelotna mżawka": "Passing drizzle",
        "przelotny deszcz": "Passing rain",
        "nocna mżawka": "Night drizzle",
        "nocny deszcz ze śniegiem": "Night sleet",
        "nocne burze": "Night storms",
        "nocny śnieg": "Night snow",
        "nocny deszcz": "Night rain",
        "zła jakość powietrza — normy zanieczyszczeń są przekroczone": "Poor air quality — pollution standards exceeded",
        "awaria źródeł — brak weryfikacji prognozy z drugiego modelu. możliwe błędy w dzisiejszej prognozie. wywołaj raport za chwilę ( z menu - opcja /day ).": "Source failure — No verification from the second model. Possible errors. Try again in a moment using the /day option.",
        "nocne dane — możliwa korekta prognozy rano.": "Night data — possible forecast adjustment in the morning.",
        "nocne dane — odśwież prognozę z menu później": "Night data — refresh the forecast from the menu later",
        "tropikalna duchota: bardzo wysoka wilgotność sprawi, że powietrze będzie wyjątkowo ciężkie.": "Tropical mugginess: High humidity will make the air feel exceptionally heavy.",
        "trudny biomet: przez wysoką wilgotność odczujemy zaduch, a powietrze stanie się ciężkie i lepkie.": "Tropical mugginess: High humidity will make the air feel exceptionally heavy.",
        "niestabilna aura, możliwe opady": "Unstable weather, possible rain",
        "niepewna prognoza zachmurzenia": "Uncertain cloud cover forecast",
        "niepewna prognoza": "Uncertain forecast",
        "gwałtowny spadek ciśnienia": "Rapid pressure drop",
        "spadek ciśnienia": "Pressure drop",
        "gwałtowny wzrost ciśnienia": "Rapid pressure rise",
        "wzrost ciśnienia": "Pressure rise",
        "przed zapowiadanym deszczem może siąpić.": "Drizzle possible before the expected rain.",
        "możliwe lekkie siąpienie.": "Light drizzle possible.",
        "brak danych": "No data",
    }
    # Tutaj w przyszłości dodasz np.:
    # "de": { "bezchmurnie": "Wolkenlos", ... }
}

# =====================================================================
# SŁOWNIKI CZĄSTKOWE (REPLACEMENTS)
# =====================================================================
REPLACEMENTS = {
    "en": [
        ("spodziewany jest wzrost temperatury do", "expected temperature rise up to"),
        ("w dzień tylko", "only during the day"),
        ("silniej po", "stronger after"),
        ("silny wiatr:", "Strong wind alert:"),
        ("silny wiatr", "strong wind"),
        ("potężna wichura:", "Severe gale alert:"),
        ("potężna wichura", "severe gale"),
        ("wichura do", "gale up to"),
        ("wichura", "gale"),
        ("wiatr do", "wind up to"),
        ("jutro ok.", "tomorrow approx."),
        ("lekki deszcz", "light rain"),
        ("silny deszcz", "heavy rain"),
        ("lekki śnieg", "light snow"),
        ("intensywny śnieg", "heavy snow"),
        ("ciągła mżawka", "continuous drizzle"),
        ("przel. burze", "passing storms"),
        ("przel. deszcz", "passing rain"),
        ("przel. mżawka", "passing drizzle"),
        ("przel. śnieg", "passing snow"),
        ("przelotny deszcz", "passing rain"),
        ("przelotna mżawka", "passing drizzle"),
        ("przelotny śnieg", "passing snow"),
        ("przelotne burze", "passing storms"),
        ("przelotne opady", "passing precipitation"),
        ("opady", "precipitation"),
        ("opad", "precipitation"),
        ("deszcz ze śniegiem", "sleet"),
        ("deszczowo", "rainy"),
        ("śnieżnie", "snowy"),
        ("mglisto", "foggy"),
        ("wietrznie", "windy"),
        ("deszcz", "rain"),
        ("mżawka", "drizzle"),
        ("śnieg", "snow"),
        ("burze:", "Storms alert:"),
        ("burze", "storms"),
        ("burza:", "Storm alert:"),
        ("burza", "storm"),
        ("mgła rano", "morning fog"),
        ("mgła:", "Fog alert:"),
        ("mgła", "fog"),
        ("upał:", "Heat alert:"),
        ("upał", "heat"),
        ("mróz:", "Frost alert:"),
        ("mróz", "frost"),
        ("! dziś maks. temp.", "! Today max temp."),
        ("w nocy.", "at night."),
        ("w dzień najwyżej", "daytime up to"),
        ("jutro", "tomorrow"),
        ("rano", "morning"),
        ("po poł.", "afternoon"),
        ("po południu", "afternoon"),
        ("noc", "night"),
        ("odcz.", "feels"),
        ("od", "from"),
        ("do", "to"),
        ("po", "after"),
        ("ok.", "approx."),
        ("hpa", "hPa"),
        # === WORTH KNOWING (Dziś warto wiedzieć) - Kawałki dynamicznych zdań ===
        ("ryzyko burzy: modele wskazują dziś na", "storm risk: models indicate a"),
        ("szans na wyładowania.", "chance of lightning today."),
        
        ("ekstremalne promieniowanie", "extreme radiation"),
        ("unikaj słońca w południe, nałóż mocny filtr!", "avoid midday sun, apply strong sunscreen!"),
        ("wysokie promieniowanie słoneczne", "high solar radiation"),
        ("pamiętaj o kremie z filtrem i ochronie głowy.", "remember sunscreen and head protection."),
        ("słońce dziś mocno operuje", "the sun is very strong today"),
        ("przy dłuższym pobycie na zewnątrz użyj kremu.", "use sunscreen if staying outside."),
        ("zdradliwe słońce — przy przejaśnieniach ok.", "deceptive sun — during clearings around"),
        ("promieniowanie uv sięgnie aż", "UV radiation will reach up to"),
        ("jeśli ok.", "if around"),
        ("wyjdzie słońce, promieniowanie uv będzie podwyższone", "the sun comes out, UV radiation will be elevated"),

        ("najmocniejsze porywy wiatru uderzą około", "the strongest wind gusts will hit around"),
        ("największe natężenie śniegu zapowiada się około", "the highest intensity of snow is expected around"),
        ("największe natężenie opadów zapowiada się około", "the highest intensity of precipitation is expected around"),

        ("nagłe ochłodzenie! po", "sudden cooling! after"),
        ("odczuwalnie spadnie temperatura w ciągu", "the temperature will drop noticeably within"),
        ("nagłe ocieplenie! po", "sudden warming! after"),
        ("temperatura odczuwalnie wzrośnie w ciągu", "the temperature will rise noticeably within"),
        ("1 godziny", "1 hour"),
        ("2 godzin", "2 hours"),
        ("3 godzin", "3 hours"),

        ("najlepsze, komfortowe warunki na wyjście będą po", "the best, most comfortable conditions to go out will be after"),
        ("wietrznie i mokro. parasol się nie sprawdzi, załóż dobrą kurtkę!", "windy and wet. an umbrella won't help, wear a good jacket!"),
        ("wiatr mocno potęguje chłód. koniecznie ubierz się na cebulkę!", "the wind strongly increases the cold. dress in layers!"),
        
        ("spadek ciśnienia o ok.", "pressure drop of approx."),
        ("hpa — meteopaci mogą czuć się gorzej.", "hPa — meteoropaths may feel worse."),
        
        ("bez śniegu, ale nadal zimno", "without snow, but still cold"),
        ("bez deszczu, ale nadal zimno", "without rain, but still cold"),
        ("bez śniegu, ale nadal mróz", "without snow, but still freezing"),
        ("bez deszczu, ale nadal mróz", "without rain, but still freezing"),
        
        ("lepiej pozostać w domu", "it's better to stay home"),
        ("najlepiej wrócić przed", "it's best to return before"),
        ("warunki na zewnątrz będą stabilniejsze", "outdoor conditions will be more stable"),
        
        ("utrzyma się okno bez śniegu", "a snow-free window will persist"),
        ("utrzyma się okno bez deszczu", "a rain-free window will persist"),
        ("główne okno bez śniegu między", "main snow-free window between"),
        ("główne okno bez deszczu między", "main rain-free window between"),
        (" a ", " and "),

        ("rano nawet o", "in the morning even"),
        ("chłodniej niż po południu", "colder than in the afternoon"),
        ("najzimniejszy moment to okolice", "the coldest moment is around"),
        ("pogoda szybko się pogorszy", "the weather will deteriorate quickly"),
        ("wieczór spokojniejszy niż reszta dnia", "evening calmer than the rest of the day"),
        ("jutro rano możliwe opady śniegu", "snow possible tomorrow morning"),
        ("najwięcej słońca będzie w najbliższych godzinach", "most sun will be in the coming hours"),
        ("najwięcej słońca zapowiada się w okolicach", "most sun is expected around"),
        ("najmniej chmur około", "fewest clouds around"),
        ("najcieplejszy moment dnia to okolice", "the warmest moment of the day is around"),

        ("możliwy przymrozek — uważaj na śliskie nawierzchnie", "frost possible — watch out for slippery surfaces"),
        ("rano gorsza widoczność przez mgłę", "morning visibility reduced due to fog"),
        ("możliwe silniejsze porywy wiatru do", "possible stronger wind gusts up to"),
        ("dziś może spaść łącznie ok.", "today a total of approx."),
        ("mm deszczu", "mm of rain may fall"),
        ("mm śniegu", "mm of snow may fall"),

        ("wstępne prognozy wskazują silny wiatr. największe porywy (ok.", "preliminary forecasts indicate strong wind. largest gusts (approx."),
        ("sygnał nadejścia niżu.", "signal of an approaching low-pressure system."),
        ("ciśnienie może spaść do ok.", "pressure may drop to approx."),
        ("szykuje się spore ocieplenie. termometry mogą pokazać nawet", "significant warming ahead. thermometers may show up to"),
        ("przed nami ochłodzenie.", "cooling ahead."),
        ("temperatura w dzień może spaść do", "daytime temperature may drop to"),

        ("w poniedziałek", "on Monday"),
        ("we wtorek", "on Tuesday"),
        ("w środę", "on Wednesday"),
        ("w czwartek", "on Thursday"),
        ("w piątek", "on Friday"),
        ("w sobotę", "on Saturday"),
        ("w niedzielę", "on Sunday"),
        ("dzisiaj", "today"),  
        ("dziś", "today"),     
    ]
    # "de": [ ("deszcz", "Regen"), ... ]
}

# =====================================================================
# GŁÓWNA FUNKCJA TŁUMACZĄCA
# =====================================================================
def translate_weather_text(text: str, lang: str = "pl") -> str:
    """
    Surgically translates dynamically generated Polish weather phrases, 
    alert notes, and time-block descriptions into the target language.
    """
    # Jeśli język to polski albo nie ma tekstu, po prostu go zwracamy
    if not text or lang == "pl":
        return text

    # Pobieramy słowniki dla zadanego języka. Fallback do pustych, by nic nie zepsuć.
    exact_map = EXACT_MAPS.get(lang, {})
    replacements = REPLACEMENTS.get(lang, [])

    # Jeśli dany język nie ma jeszcze słowników, zwracamy oryginał PL
    if not exact_map and not replacements:
        return text

    def translate_snippet(snippet: str) -> str:
        # PANCERNA TARCZA NA WEJŚCIU
        if not isinstance(snippet, str) or not snippet.strip():
            return snippet or ""
            
        # Zapamiętujemy oryginalną wielkość pierwszej litery
        is_upper = snippet.strip()[0].isupper()

        low = snippet.strip().lower()
        if low in exact_map:
            res = exact_map[low]
        else:
            res = snippet
            for pl_word, target_word in replacements:
                if not pl_word: continue
                # Ochrona granic słów
                left_bound = r'\b' if pl_word[0].isalpha() else r''
                right_bound = r'\b' if pl_word[-1].isalpha() else r''
                
                pattern = re.compile(left_bound + re.escape(pl_word) + right_bound, re.IGNORECASE)
                res = pattern.sub(target_word, res)
                
        res = res.strip()
        if not res: 
            return snippet

        # --- REGUŁA DWUKROPKA I MYŚLNIKA (Alerty i WK) ---
        separator = None
        if " — " in res:
            separator = " — "
        elif ": " in res:
            separator = ": "

        if separator:
            parts = res.split(separator, 1)
            title = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ""
            
            title = title[0].upper() + title[1:] if title else ""
            desc = desc[0].upper() + desc[1:] if desc else ""
            
            if desc:
                return f"{title}{separator}{desc}"
            else:
                return f"{title}{separator.rstrip()}"
            
        # --- ZASADA GŁÓWNA ---
        if is_upper:
            return res[0].upper() + res[1:]
        else:
            return res[0].lower() + res[1:]

    # ==============================================================
    # GŁÓWNY ROZDZIELACZ: Najpierw \n (Hero), potem kropki (·)
    # ==============================================================
    if "\n" in text:
        return "\n".join(translate_weather_text(line, lang) for line in text.split("\n"))

    if " · " in text:
        return " · ".join(translate_snippet(p) for p in text.split(" · "))
        
    return translate_snippet(text)
    
    
# ==============================================================
# SŁOWNIK INTERFEJSU TELEGRAMA (UI)
# ==============================================================

# ==============================================================
# SŁOWNIK INTERFEJSU TELEGRAMA (UI)
# ==============================================================

UI_TEXTS = {
    "pl": {
        "menu_header": "⚙️ *PANEL STEROWANIA* | {name}\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n🌍 *Obecna lokalizacja:*\n└ {city}\n\n📅 *Twój harmonogram:*\n├ Rano: {disp_rano}\n└ Popołudnie: {disp_wieczor}\n\n⏰ *Jak zmienić godziny?*\nKliknij przycisk poniżej.\n\n📍 *Jak zmienić miejscowość?*\nWyślij nową 📎 Pinezkę z mapy.",
        "btn_change_hours": "⏰ Zmień godziny raportów",
        "disp_off": "Wyłączony ❌",
        "loc_updated": "✅ *Lokalizacja zaktualizowana!*\n\n📍 Rozpoznano: {city}\n🌤️ Od następnego raportu pogoda będzie liczona dla tego miejsca.",
        "missing_loc": "⚠️ Brakuje współrzędnych! Wyślij najpierw pinezkę z mapy.",
        "scanning": "📡 Skanuję radary... Generuję prognozę godzinową.",
        "prep_main": "☀️ Przygotowuję główną kartę pogodową na dzisiaj...",
        "prep_future": "🔮 Generuję prognozę hybrydową na najbliższe 14 dni... Daj mi sekundę.",
        "time_limit": "ℹ️ Główny raport dzienny jest dostępny tylko od 05:00 do 15:59.\nWybierz /now, aby sprawdzić radar taktyczny na wieczór i noc!",
        "err_gen": "⚠️ Wystąpił błąd podczas generowania karty.",
        
        # --- NOWE KLUCZE (OPISY I KOMENDY) ---
        "no_access": "⛔ *Brak dostępu*\nPrzepraszamy, ten bot jest prywatny i działa wyłącznie na zaproszenia od obecnych użytkowników.",
        "limit_reached": "⛔ Niestety, globalny limit miejsc w aplikacji (50) został wyczerpany.",
        "invalid_link": "⛔ Ten link zaproszeniowy jest nieprawidłowy lub pochodzi od niezarejestrowanej osoby.",
        "welcome_new": "✅ *Rejestracja pomyślna!*\nWitamy w doborowym gronie Pogoda dla Ciebie. Dostałeś się tu z polecenia!\n\n⚠️ *Ostatni krok, ale konieczny:*\nAby raporty mogły działać i przychodzić do Ciebie o godz. 8:00 i 14:00, musisz ustawić swoją lokalizację:\n1️⃣ Naciśnij tę wiadomość i wybierz *Odpowiedz* (Reply).\n2️⃣ Wybierz 📎 (spinacz), a następnie 📍 *Lokalizacja*.\n3️⃣ Wyślij 📎 *Pinezkę z mapy*.",
        "invite_intro": "💌 *Twoje zaproszenie jest gotowe!*\n\nSkopiuj poniższą wiadomość i wyślij ją znajomemu (np. przez SMS lub WhatsApp), albo bezpośrednio przez Telegram.",
        "invite_sms": "Cześć! 🌤 Używam świetnego, prywatnego bota pogodowego na Telegramie.\n\nZostawiam Ci moje zaproszenie. Kliknij w link poniżej, aby z niego skorzystać:\n{link}",
        "invite_group_btn": "➕ Dodaj bota do Twojej grupy",
        "invite_group_desc": "💡 *A może chcesz dodać bota do swojej grupy?*\nUżyj przycisku poniżej. Telegram automatycznie otworzy listę Twoich grup.",
        "info_msg": "ℹ️ *JAK DZIAŁA POGODA DLA CIEBIE?*\nJestem Twoim asystentem pogodowym. Oto krótka ściąga:\n\n📍 *1. Dokładna Lokalizacja*\nAby zmienić miejsce, kliknij na dowolną z moich wiadomości, wybierz *Odpowiedz*, potem 📎, *lokalizację* 📍 i wyślij *Pinezkę z lokalizacją*.\n\n⏰ *2. Codzienne Raporty (/menu)*\nUstawisz własne godziny, o których mam wysyłać Ci poranne podsumowanie dnia i popołudniową prognozę.\n\n⏰ *3. Dzienny raport na żądanie (/day)*\nOdświeżysz dzienny raport wzbogacony o najnowsze dane pogodowe.\n\n📡 *4. Radar Taktyczny (/now)*\nWygeneruję natychmiast szczegółową, godzinową kartę na najbliższe 12 godzin.\n\n🔮 *5. Trend długoterminowy (/trend)*\nWygeneruję wykres prognozy na 14 dni.\n\n💌 *6. Zaproszenie (/zapros)*\nJeśli ci się podoba, przekaż link do aplikacji twoim znajomym.",
        "welcome_back": "👋 Witaj ponownie w Pogoda dla Ciebie! Twój profil jest już autoryzowany.\n\nZawsze możesz wpisać *`/menu`*, aby sprawdzić ustawienia, lub użyć komendy *`/zapros`*, by udostępnić aplikację znajomemu!",
        "porady_msg": "💡 *PORADY I TRIKI – POGODA WORLD*\nWykorzystaj pełen potencjał swojego asystenta:\n\n🔕 *1. Ciche poranki*\nGłośne powiadomienia są automatycznie wyłączone wcześnie rano (przed 7:00), aby Cię nie budzić. Możesz też ręcznie wyciszyć czat w opcjach Telegrama.\n\n⏰ *2. Dodatkowy raport*\nDostałeś już rano raport, ale pogoda jest dynamiczna i chcesz otrzymać ponownie raport z najnowższymi danymi. Po prostu z menu wybierz */day* i od razu dostaniesz zaktualizowaną, pełną prognozę! Z kolei do szybkiego podglądu bez zmieniania ustawień użyj komendy */now*.\n\n🌍 *3. Pogoda na drugim końcu świata*\nChcesz śledzić pogodę w Amazonii? Utwórz pustą grupę w Telegramie, dodaj tam bota przez */zapros* i wyślij tam pinezkę z mapy świata! *Uwaga:* Raporty przyjdą według czasu lokalnego dla tamtego miejsca.\n\n🛑 *4. Urlop od powiadomień*\nNie chcesz raportów z automatu? W */menu* wybierz opcję *\"Nie chcę\"*. Nadal będziesz mógł ręcznie sprawdzać pogodę komendami.\n\n🎛 *5. Szybkie sterowanie*\nNa prywatnym czacie używaj przycisku *Menu* po lewej stronie. W grupie wystarczy nacisnąć znak ukośnika * / *, by rozwinąć listę komend.\n\n📍 *6. Prywatność i bezpieczeństwo*\nNie musisz podawać adresu co do metra – rozbieżność 3 km nie zmienia prognozy. Ponadto, wszystkie linki z bota są w pełni bezpieczne.\n\n⚙️ *7. Domyślne ustawienia*\nJeśli nic nie zrobisz i nie ustawisz swoich godzin w menu, nic nie stracisz! Twoje raporty pogodowe będą domyślnie wysyłane o godz. *{default_rano}* i *{default_wieczor}*.\n\n🌴 *8. Pogoda na wakacjach*\nWyjeżdżasz na urlop? Po prostu wejdź w czat z botem i wyślij nową 📎 Pinezkę z miejsca, w którym jesteś. Bot natychmiast przestawi się na nową lokalizację i wyśle Ci raporty zgodnie z tamtejszą strefą czasową!",
        "search_loc": "🔍 Szukam lokalizacji na mapie...",
        "search_fail": "❌ Nie mogłem znaleźć takiego miejsca na mapie. Spróbuj dopisać kraj, np. `/miasto nazwa, Polska`.",
        "search_success": "✅ *Zaktualizowano lokalizację!*\n\n📍 Rozpoznano: {city}\n🌍 Pełny adres z mapy: {address}\n\n💡 *To złe miejsce?* Wpisz komendę dokładniej, np. `/miasto {query}, Polska`.",
        "search_err": "⚠️ Błąd zapisu na serwerze Google. Spróbuj za chwilę.",
        "city_prompt": "💡 *Podaj nazwę miejscowości*, np.:\n👉 `/miasto Kraków, Polska`"
    },
    "en": {
        "menu_header": "⚙️ *CONTROL PANEL* | {name}\n〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️\n🌍 *Current location:*\n└ {city}\n\n📅 *Your schedule:*\n├ Morning: {disp_rano}\n└ Afternoon: {disp_wieczor}\n\n⏰ *How to change hours?*\nClick the button below.\n\n📍 *How to change location?*\nSend a new 📎 Location pin.",
        "btn_change_hours": "⏰ Change report hours",
        "disp_off": "Disabled ❌",
        "loc_updated": "✅ *Location updated!*\n\n📍 Recognized: {city}\n🌤️ Weather reports will use this location from now on.",
        "missing_loc": "⚠️ Missing location! Please send a map pin first.",
        "scanning": "📡 Scanning radars... Generating hourly forecast.",
        "prep_main": "☀️ Preparing main weather card for today...",
        "prep_future": "🔮 Generating 14-day forecast... Give me a second.",
        "time_limit": "ℹ️ The main daily report is only available from 05:00 to 15:59.\nUse /now to check the tactical radar for the evening and night!",
        "err_gen": "⚠️ An error occurred while generating the card.",
        
        # --- NOWE KLUCZE (OPISY I KOMENDY) ---
        "no_access": "⛔ *Access Denied*\nSorry, this bot is private and works exclusively via invitations from current users.",
        "limit_reached": "⛔ Unfortunately, the global application limit (50 users) has been reached.",
        "invalid_link": "⛔ This invitation link is invalid or comes from an unregistered person.",
        "welcome_new": "✅ *Registration successful!*\nWelcome to Weather for You. You got here by invitation!\n\n⚠️ *One last, necessary step:*\nFor reports to work and arrive at 8:00 and 14:00, you must set your location:\n1️⃣ Tap this message and select *Reply*.\n2️⃣ Choose 📎 (paperclip), then 📍 *Location*.\n3️⃣ Send 📎 *Map pin*.",
        "invite_intro": "💌 *Your invitation is ready!*\n\nCopy the message below and send it to a friend (e.g., via SMS or WhatsApp), or directly through Telegram.",
        "invite_sms": "Hi! 🌤 I use a great, private weather bot on Telegram.\n\nHere is my invitation. Click the link below to use it:\n{link}",
        "invite_group_btn": "➕ Add bot to your group",
        "invite_group_desc": "💡 *Want to add the bot to your group?*\nUse the button below. Telegram will automatically open your group list.",
        "info_msg": "ℹ️ *HOW DOES WEATHER FOR YOU WORK?*\nI am your weather assistant. Here is a quick cheat sheet:\n\n📍 *1. Exact Location*\nTo change your location, tap any of my messages, select *Reply*, then 📎, *Location* 📍 and send a *Map pin*.\n\n⏰ *2. Daily Reports (/menu)*\nSet your own hours for the morning daily summary and the afternoon forecast.\n\n⏰ *3. Daily report on demand (/day)*\nRefresh the daily report enriched with the latest weather data.\n\n📡 *4. Tactical Radar (/now)*\nGenerate an immediate detailed, hourly card for the next 12 hours.\n\n🔮 *5. Long-term trend (/trend)*\nGenerate a 14-day forecast chart.\n\n💌 *6. Invitation (/zapros)*\nIf you like it, share the app link with your friends.",
        "welcome_back": "👋 Welcome back to Weather for You! Your profile is already authorized.\n\nYou can always type *`/menu`* to check your settings, or use the *`/zapros`* command to share the app with a friend!",
        "porady_msg": "💡 *TIPS AND TRICKS – WEATHER WORLD*\nUnlock the full potential of your assistant:\n\n🔕 *1. Quiet mornings*\nLoud notifications are automatically disabled early in the morning (before 7:00) so as not to wake you up. You can also manually mute the chat in Telegram options.\n\n⏰ *2. Extra report*\nYou already received a morning report, but the weather is dynamic and you want to get an updated forecast. Just choose */day* from the menu! For a quick glance without changing settings, use the */now* command.\n\n🌍 *3. Weather across the globe*\nWant to track the weather in the Amazon? Create an empty group in Telegram, add the bot via */zapros* and send a map pin from there! *Note:* Reports will arrive according to the local time for that location.\n\n🛑 *4. Notification vacation*\nDon't want automated reports? In */menu*, select the *\"Disabled\"* option. You can still check the weather manually using commands.\n\n🎛 *5. Quick controls*\nIn a private chat, use the *Menu* button on the left. In a group, just type a slash * / * to unfold the command list.\n\n📍 *6. Privacy and security*\nYou don't need to provide a down-to-the-meter address – a 3 km discrepancy doesn't change the forecast. Moreover, all bot links are fully secure.\n\n⚙️ *7. Default settings*\nIf you do nothing and don't set your hours in the menu, you lose nothing! Your weather reports will default to *{default_rano}* and *{default_wieczor}*.\n\n🌴 *8. Weather on vacation*\nGoing on holiday? Just enter the chat with the bot and send a new 📎 Map pin from where you are. The bot will instantly switch to the new location and send you reports according to the local time zone!",
        "search_loc": "🔍 Searching map...",
        "search_fail": "❌ Could not find this place. Try adding the country, e.g., `/city name, Poland`.",
        "search_success": "✅ *Location updated!*\n\n📍 Recognized: {city}\n🌍 Full map address: {address}\n\n💡 *Wrong place?* Be more specific, e.g., `/city {query}, Poland`.",
        "search_err": "⚠️ Google Server error. Try again later.",
        "city_prompt": "💡 *Provide a city name*, e.g.:\n👉 `/city London`"
    },
    "de": {
        "city_prompt": "💡 *Geben Sie einen Städtenamen ein*, z.B.:\n👉 `/city Berlin`"
    },
    "es": {
        "city_prompt": "💡 *Proporcione el nombre de una ciudad*, ej.:\n👉 `/city Madrid`"
    },
    "fr": {
        "city_prompt": "💡 *Indiquez le nom d'une ville*, ex.:\n👉 `/city Paris`"
    }
}

def t_ui(lang: str, key: str, **kwargs) -> str:
    """Pobiera tekst interfejsu w odpowiednim języku i podstawia zmienne."""
    lang = lang if lang in UI_TEXTS else "pl"
    text = UI_TEXTS[lang].get(key, UI_TEXTS["pl"].get(key, key))
    return text.format(**kwargs)