"""
Image Generator v9 — forecast_text integration, pixel measure, clean layout.
"""

from PIL import Image, ImageDraw, ImageFont
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "assets", "fonts")
ICON_DIR = os.path.join(BASE_DIR, "assets", "icons")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

WIDTH = 1080
MARGIN = 70

STYLE_COLORS = {
    "default":           (230, 235, 245),
    "meta":              (230, 235, 245),
    "feels_like_accent": (220, 180, 255),
    "alert":             (252, 129, 129),  # <--- NASZ NOWY CZERWONY
}

# Stałe tło — wariant 3 (głęboki granat)
FIXED_PALETTE = {
    "top":  (70, 86, 138),
    "bot":  (41, 57, 104),
    "card": (255, 255, 255, 31),
    
}

# Kolory tytułów sekcji
TITLE_TODAY = (254, 240, 138)    # żółty — dziś, Uważaj, Warto wiedzieć
TITLE_FUTURE = (138, 226, 160)   # zielony — prognozy na inne dni


# === CACHE I ZARZĄDZANIE PAMIĘCIĄ ===
_FONT_CACHE = {}
_ICON_CACHE = {}

def get_font(name, size):
    """Pobiera czcionkę z pamięci RAM (Cache) lub ładuje z dysku."""
    key = (name, size)
    if key not in _FONT_CACHE:
        path = os.path.join(FONT_DIR, name)
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except IOError:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]

def _get_icon_cached(path, size):
    """Pobiera przeskalowaną ikonę z RAM zamykając bezpiecznie plik (Context Manager)."""
    key = (path, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    try:
        with Image.open(path) as im:  # <--- Zamyka plik, zapobiega wyciekom RAM!
            ic = im.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
        _ICON_CACHE[key] = ic
        return ic
    except Exception:
        return None


# Uniwersalny słownik ratunkowy dla głównych ikon
WK_FALLBACK_GLOBAL = {
    "wk_clear": "sun", "wk_mostly_sunny": "sun",
    "wk_partlycloudy": "cloud", "wk_mostly_cloudy": "cloud",
    "wk_overcast": "cloud", "wk_drizzle": "rain",
    "wk_light_rain": "rain", "wk_showers": "rain",
    "wk_rain": "rain", "wk_sun_one_cloud": "sun",
    "wk_heavy_rain": "rain", "wk_light_snow": "snow",
    "wk_snow": "snow", "wk_storm": "storm",
    "wk_fog": "cloud", "wk_wind": "wind",
    "wk_sleet": "snow", "wk_sun_storm": "storm", "wk_snow_showers": "snow"
}

def paste_icon(img, x, y, icon_name, size=(60, 60)):
    # 1. Próbujemy znaleźć dokładną nazwę pliku (np. wk_rain.png)
    path = os.path.join(ICON_DIR, f"{icon_name}.png")
    
    if not os.path.exists(path):
        # 2. Szukamy w słowniku ratunkowym
        fallback_name = WK_FALLBACK_GLOBAL.get(icon_name, icon_name)
        path = os.path.join(ICON_DIR, f"{fallback_name}.png")
        
        # 3. Jeśli wciąż nie ma, warianty
        if not os.path.exists(path):
            for suffix in ["_day", "_night", ""]:
                alt = os.path.join(ICON_DIR, f"{fallback_name}{suffix}.png")
                if os.path.exists(alt):
                    path = alt
                    break

    # Rysujemy ikonę (używając RAM Cache i bezpiecznego otwierania)
    if os.path.exists(path):
        ic = _get_icon_cached(path, size)
        if ic is not None:
            img.paste(ic, (int(x), int(y)), ic)
            return

    # Fallback ostateczny (pytajnik)
    d = ImageDraw.Draw(img)
    cx, cy = int(x) + size[0]//2, int(y) + size[1]//2
    r = min(size) // 2 - 2
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(80, 95, 130))
    f = get_font("Inter-Medium.ttf", size[1]//2)
    d.text((cx-6, cy-10), "?", font=f, fill=(180, 190, 210))

def draw_rounded_rect(ov, xy, r, fill):
    d = ImageDraw.Draw(ov)
    x1, y1, x2, y2 = xy
    d.ellipse([x1, y1, x1+2*r, y1+2*r], fill=fill)
    d.ellipse([x2-2*r, y1, x2, y1+2*r], fill=fill)
    d.ellipse([x1, y2-2*r, x1+2*r, y2], fill=fill)
    d.ellipse([x2-2*r, y2-2*r, x2, y2], fill=fill)
    d.rectangle([x1+r, y1, x2-r, y2], fill=fill)
    d.rectangle([x1, y1+r, x2, y2-r], fill=fill)


def get_palette(icon):
    p = {
        "sun":   {"top": (80,175,255),  "bot": (45,120,220),  "card": (255,255,255,50)},
        "cloud": {"top": (85,130,195),  "bot": (45,75,135),   "card": (255,255,255,45)},
        "rain":  {"top": (60,85,155),   "bot": (30,48,105),   "card": (255,255,255,40)},
        "snow":  {"top": (145,170,205), "bot": (85,105,145),  "card": (255,255,255,45)},
        "wind":  {"top": (65,90,145),   "bot": (32,50,90),    "card": (255,255,255,38)},
        "storm": {"top": (55,55,105),   "bot": (22,22,58),    "card": (255,255,255,32)},
    }
    return p.get(icon, p["cloud"])


def draw_gradient(img, top, bot, h):
    d = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / max(h - 1, 1)
        d.line([(0, y), (WIDTH, y)],
               fill=tuple(int(top[i]*(1-ratio) + bot[i]*ratio) for i in range(3)))


def draw_spans(draw, x, y, spans, font, fallback_color=(230, 235, 245)):
    """Rysuje tekst span po spanie, każdy w swoim kolorze."""
    cx = x
    for span in spans:
        color = STYLE_COLORS.get(span.get("style"), fallback_color)
        # TARCZA NA NONE:
        text_val = span.get("text") or ""
        draw.text((cx, y), span["text"], font=font, fill=color)
        cx += draw.textlength(span["text"], font=font)
    return cx
    
def wrap_text(draw, text, font, max_width):
    """Dzieli tekst na linie, aby zmieścił się w zadanej szerokości."""
    words = text.split()
    lines = []
    current = ""
    for w in words:
        test = f"{current} {w}".strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = w
    if current: lines.append(current)
    return lines


def measure_spans(draw, spans, font):
    """Mierzy łączną szerokość listy spanów w pikselach."""
    # Zmieniamy z s["text"] na s.get("text") or ""
    return sum(draw.textlength(s.get("text") or "", font=font) for s in spans)

def ellipsize(draw, text, font, max_px):
    """Przycina tekst i dodaje '...', jeśli przekracza dozwoloną szerokość."""
    if draw.textlength(text, font=font) <= max_px:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        if draw.textlength(cand, font=font) <= max_px:
            lo = mid + 1
        else:
            hi = mid
    cut = max(lo - 1, 0)
    return text[:cut].rstrip() + ell
    
def fit_day_desc(draw, text, font, max_px):
    """Skraca opis semantycznie, jeśli się nie mieści. Ellipsize to ostateczność."""
    if not text:
        return text
        
    # 1. Jeśli się mieści w oryginale, zostawiamy
    if draw.textlength(text, font=font) <= max_px:
        return text
        
    t = text.lower()
    
    # 2. Krótkie, semantyczne fallbacki (bez wielokropka)
    candidates = []
    if "burz" in t:
        candidates.append("burze")
    if "wiatr" in t or "poryw" in t:
        candidates.append("wietrznie")
        
    if "grad" in t and ("śnieg" in t or "śnież" in t):
        candidates.append("śnieg i grad")
    elif "grad" in t:
        candidates.append("grad")
    elif "śnieg" in t or "śnież" in t:
        if "deszcz" in t:
            candidates.append("deszcz ze śniegiem")
        else:
            candidates.append("śnieg")
    elif "deszcz" in t:
        if "przelot" in t or "z przerw" in t:
            candidates.append("przelotny deszcz")
        else:
            candidates.append("deszcz")
    elif "mżaw" in t:
        candidates.append("mżawka")
            
    # Ostateczny, bezpieczny fallback słowny
    candidates.append("zmiennie")
    
    # Próbujemy dopasować kandydatów
    for c in candidates:
        if draw.textlength(c, font=font) <= max_px:
            return c
            
    # 3. Dopiero na samym końcu brutalna tarcza wielokropka
    return ellipsize(draw, text, font, max_px)
    


def pack_all_inline(draw, primary, primary_style, extra_lines, font, avail_px):
    """Pakuje primary + meta + feels w jedną linię, dopóki się mieszczą.
    
    Zwraca (inline_spans, remaining_extras).
    Kolejność: primary, potem meta, potem feels_like.
    Separator: ' · '
    """
    inline_spans = []
    if primary:
        inline_spans = [{"text": primary, "style": primary_style}]

    rest = list(extra_lines)
    packed_rest = []

    for el in rest:
        el_type = el.get("type", "meta")

        # Buduj spany dla tego elementu
        if "spans" in el:
            el_spans = el["spans"]
        else:
            # TARCZA NA NONE: Jeśli el.get("text") to None, operator 'or' zamieni to na ""
            text_val = el.get("text") or ""
            el_spans = [{"text": text_val, "style": "meta"}]
            #el_spans = [{"text": el.get("text", ""), "style": "meta"}]

        if not el_spans:
            packed_rest.append(el)
            continue

        # Próbuj dołączyć do inline
        if inline_spans:
            sep = {"text": " · ", "style": "default"}
            candidate = inline_spans + [sep] + el_spans
        else:
            candidate = el_spans

        if measure_spans(draw, candidate, font) <= avail_px:
            inline_spans = candidate
        else:
            packed_rest.append(el)

    return inline_spans, packed_rest


def draw_blocks_card(draw, ov, img, blocks, title, y, cx1, cx2, pal,
                     title_color=None):
    if title_color is None:
        title_color = TITLE_TODAY
    f_title = get_font("Inter-Bold.ttf", 42)
    f_label = get_font("Inter-Medium.ttf", 34)
    f_temp  = get_font("Inter-Medium.ttf", 34)
    f_line  = get_font("Inter-Regular.ttf", 30)
    CP = 20; CR = 24; ROW = 54; EXTRA_H = 32; TITLE_GAP = 62

    # Kolumny dosunięte do lewej dla ikony 36px
    #C_HOURS = cx1 + CP          # tekst skrótu dnia
    #C_ICON  = cx1 + CP + 130    # ikona — stała pozycja niezależnie od skrótu
    #C_TEMP  = cx1 + CP + 185
    #C_DESC  = cx1 + CP + 330
    # Te same kolumny co draw_blocks_card 48px
    C_HOURS = cx1 + CP
    C_ICON  = cx1 + CP + 145
    C_TEMP  = cx1 + CP + 210   # +10
    C_DESC  = cx1 + 400        # ZŁOTA KOLUMNA OPISÓW
    DESC_RIGHT = cx2 - CP
    avail_desc_px = DESC_RIGHT - C_DESC

    # --- Pack inline per block ---
    packed = []
    for b in blocks:
        primary = b.get("primary_desc", "")
        primary_style = b.get("primary_style", "default")
        extras = b.get("extra_lines", [])
        inline_spans, remaining = pack_all_inline(
            draw, primary, primary_style, extras, f_line, avail_desc_px)
        packed.append((inline_spans, remaining))

    # --- Wysokość karty ---
    card_h = CP + TITLE_GAP
    n = len(packed)
    for idx, (inline_spans, remaining) in enumerate(packed):
        card_h += ROW
        card_h += len(remaining) * EXTRA_H
        if idx < n - 1:
            card_h += 18
    card_h += CP

    draw_rounded_rect(ov, (cx1, y, cx2, y + card_h), CR, pal["card"])
    if title:
        # 1. Rysujemy główny tytuł (np. "Prognoza na dziś")
        draw.text((cx1 + CP, y + CP), title, font=f_title, fill=title_color)
        
        # --- 2. LEGENDA MIN/MAX (Jednolita, blada) ---
        f_legend = get_font("Inter-Regular.ttf", 26)
        legend_text = "Temp: min°/max°"
        legend_w = draw.textlength(legend_text, font=f_legend)
        
        start_x = cx2 - CP - legend_w
        start_y = y + CP + 14  # Y dobrane tak, by ładnie leżało w osi tytułu
        
        draw.text((start_x, start_y), legend_text, font=f_legend, fill=(170, 180, 200))
        # ---------------------------------------------

    by = y + CP + TITLE_GAP

    for i, b in enumerate(blocks):
        lbl = b.get("label", "")
        temp_str = b.get("temp_range", "")
        inline_spans, remaining = packed[i]

        # Godziny
        draw.text((C_HOURS, by), lbl, font=f_label, fill="white")

        # Ikona 36px/48x
        #paste_icon(img, C_ICON, by - 2, b.get("icon", "cloud"), size=(36, 36))
        paste_icon(img, C_ICON, by - 6, b.get("icon", "cloud"), size=(48, 48))
        # Temperatura (z obsługą alertów!)
        temp_color = STYLE_COLORS.get(b.get("temp_style", "default"), (230, 235, 245))
        if temp_str.startswith("-"):
            minus_w = draw.textlength("-", font=f_temp)
            draw.text((C_TEMP - minus_w, by), temp_str, font=f_temp, fill=temp_color)
        else:
            draw.text((C_TEMP, by), temp_str, font=f_temp, fill=temp_color)

        
        # Inline: primary + meta + feels
        if inline_spans and any(s["text"] for s in inline_spans):
            draw_spans(draw, C_DESC, by + 1, inline_spans, f_line)
        by += ROW

        # Remaining extra
        if remaining:
            by -= 16
            for el in remaining:
                spans = el.get("spans", [])
                if spans:
                    draw_spans(draw, C_DESC, by, spans, f_line)
                else:
                    text = el.get("text", "")
                    draw.text((C_DESC, by), text, font=f_line,
                              fill=(230, 235, 245))
                by += EXTRA_H

        if i < len(blocks) - 1:
            by += 18

    return y + card_h


def draw_days_card(draw, ov, img, days, title, y, cx1, cx2, pal,
                   title_color=None):
    if title_color is None:
        title_color = TITLE_FUTURE
    f_title = get_font("Inter-Bold.ttf", 42)
    f_label = get_font("Inter-Medium.ttf", 34)
    f_temp  = get_font("Inter-Medium.ttf", 34)
    f_line  = get_font("Inter-Regular.ttf", 30)
    CP = 20; CR = 24; TITLE_GAP = 62

    # Kolumny dosunięte do lewej dla ikony 48px
    #C_HOURS = cx1 + CP
    #C_ICON  = cx1 + CP + 130
    #C_TEMP  = cx1 + CP + 200   # +15 — miejsce na ikonę 48px
    #C_DESC  = cx1 + 400        # ZŁOTA KOLUMNA OPISÓW
    #DESC_RIGHT = cx2 - CP

    # === INTELIGENTNE KOLUMNY (Auto-Layout) ===
    max_label_w = max([draw.textlength(d.get("label") or d.get("name", ""), font=f_label) for d in days] + [0])
    
    # Dodajemy: szukamy najszerszego skrótu dnia (np. "Czw"), by równo ustawić margines dat
    max_day_w = max([draw.textlength((d.get("label") or d.get("name", "")).split(" ")[0], font=f_label) for d in days] + [0])
    
    col_offset = max(130, int(max_label_w + 30))

    C_HOURS = cx1 + CP
    C_DATE  = C_HOURS + int(max_day_w) + 12   # <--- NOWA, niewidzialna kolumna na samą datę
    C_ICON  = cx1 + CP + col_offset            # Ikony ani drgną!
    C_TEMP  = C_ICON + 65                      # Lekko zbliżamy temperaturę
    C_DESC  = max(cx1 + 400, C_TEMP + 150)     # Odzyskujemy aż 35 pikseli dla długich opisów!
    DESC_RIGHT = cx2 - CP

    ROW = 54
    SEP = 18  # odstęp między wierszami jak w draw_blocks_card
    n_days = len(days)
    card_h = CP + TITLE_GAP + n_days * ROW + max(0, n_days - 1) * SEP + CP
    draw_rounded_rect(ov, (cx1, y, cx2, y + card_h), CR, pal["card"])
    draw.text((cx1 + CP, y + CP), title, font=f_title, fill=title_color)

    # --- LEGENDA MIN/MAX (Jednolita, blada) ---
    f_legend = get_font("Inter-Regular.ttf", 26)
    legend_text = "Temp: min°/max°"
    legend_w = draw.textlength(legend_text, font=f_legend)
    
    start_x = cx2 - CP - legend_w
    start_y = y + CP + 14
    
    draw.text((start_x, start_y), legend_text, font=f_legend, fill=(170, 180, 200))
    # ---------------------------------------------

    dy = y + CP + TITLE_GAP
    for d in days:
        # Kolumna 1: label (np. "06–22") lub name (np. "Wt.")
        label = d.get("label") or d.get("name", "")
        
        # --- WYKRYWANIE WEEKENDU (Wersja pancerna) ---
        if any(w in label for w in ["Sob", "Nd", "Ndz", "Nie"]):
            day_color = TITLE_FUTURE  
        else:
            day_color = "white"
            
        parts = label.split(" ", 1) # Próbujemy uciąć po pierwszej spacji
        
        if len(parts) == 2:
            # Rozdzielamy "Czw 30.04" -> "Czw" i "30.04" w perfekcyjnych kolumnach
            draw.text((C_HOURS, dy), parts[0], font=f_label, fill=day_color)
            draw.text((C_DATE, dy), parts[1], font=f_label, fill=day_color)
        else:
            # Standardowe zachowanie dla raportów dziennych (np. "Wt.", "06-10")
            draw.text((C_HOURS, dy), label, font=f_label, fill=day_color)
            
        # Kolumna 2: ikona 36x/48x
        paste_icon(img, C_ICON, dy - 6, d.get("icon", "cloud"), size=(48, 48))

        # Kolumna 3: temperatura
        t_min, t_max = d.get('temp_min', '?'), d.get('temp_max', '?')
        temp_str = f"{t_min}°/{t_max}°"
        
        if temp_str.startswith("-"):
            minus_w = draw.textlength("-", font=f_temp)
            draw.text((C_TEMP - minus_w, dy), temp_str, font=f_temp,
                      fill=(230, 235, 245))
        else:
            draw.text((C_TEMP, dy), temp_str, font=f_temp,
                      fill=(230, 235, 245))

        # Kolumna 4: badge LUB descriptor
        badge = d.get("precip_badge")
        descriptor = d.get("descriptor")
        
            
        desc_avail = cx2 - CP - C_DESC

        if badge:
            badge = fit_day_desc(draw, badge, f_line, desc_avail)
            
            # --- INTELIGENTNE KOLOROWANIE ---
            b_low = badge.lower()
            # Pomarańczowy rezerwujemy TYLKO dla groźnych zjawisk
            if any(w in b_low for w in ["burz", "wiatr", "wichur", "grad"]):
                b_color = (253, 186, 116)
            else:
                b_color = (200, 210, 225) # Zwykły deszcz/śnieg dostaje jednolity, błękitno-biały kolor
                
            draw.text((C_DESC, dy + 1), badge, font=f_line, fill=b_color)
            
        elif descriptor:
            descriptor = fit_day_desc(draw, descriptor, f_line, desc_avail)
            draw.text((C_DESC, dy + 1), descriptor, font=f_line, fill=(200, 210, 225))
        
        
        dy += ROW
        if d is not days[-1]:
            dy += SEP

    return y + card_h


def generate_weather_card(data, palette_override=None):
    f_city = get_font("Inter-Bold.ttf", 78)
    f_date = get_font("Inter-Regular.ttf", 40)
    f_meta = get_font("Inter-Regular.ttf", 32)
    f_temp = get_font("Inter-Bold.ttf", 130)
    f_summ = get_font("Inter-Medium.ttf", 40)
    f_pres = get_font("Inter-Regular.ttf", 32)
    f_alrt = get_font("Inter-Medium.ttf", 36)
    f_foot = get_font("Inter-Regular.ttf", 30)
    f_atit = get_font("Inter-Bold.ttf", 42)
    f_ctx  = get_font("Inter-Regular.ttf", 32)

    blocks = data.get("today_blocks", [])
    days = data.get("next_days", [])
    wdays = data.get("weekend_detail_days", [])
    alerts = data.get("alerts", [])
    mi = data.get("main_icon", "cloud")
    if palette_override:
        pal = palette_override
    else:
        pal = FIXED_PALETTE
    tr = data.get("temp_range", "?°")

    CP = 32; CR = 24; GAP = 28

    
    # === PRECYZYJNE WYSOKOŚCI ===
    TOP_SAFE_OFFSET = 140
    # Liczymy ile linii ma nasze summary (aby karta się nie rozjechała)
    sm_raw = data.get("summary", "")
    sm_lines_count = len(sm_raw.split("\n")) if sm_raw else 1
    
    # Header: Margines + offset + city + date + icon + (liczba_linii * 46) + padding
    hh = MARGIN + TOP_SAFE_OFFSET + 97 + 56 + 140 + (sm_lines_count * 46) + 20
    # --- NOWE: Dynamiczne łamanie linii dla context_line ---
    # Inicjujemy narzędzie mierzące tekst ZAWSZE na samej górze
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    
    ctx = data.get("context_line")
    ctx_lines = []
    if ctx:
        ctx_lines = wrap_text(dummy_draw, ctx, f_ctx, WIDTH - 2 * MARGIN)
        hh += len(ctx_lines) * 38
    

    # Bloki dziś (z draw_blocks_card: CP=20, TITLE_GAP=62, ROW=54, EXTRA=32)
    ht = 0
    if blocks:  # <--- Liczymy wysokość tylko, jeśli są jakieś bloki
        ht = 20 + 62
        for i, b in enumerate(blocks):
            ht += 54 + len(b.get("extra_lines", [])) * 32
            if i < len(blocks) - 1: ht += 18
        ht += 20

    # Weekend (wdd)
    hw = 0
    for wd in wdays:
        hww = 20 + 62
        for i, b in enumerate(wd["blocks"]):
            hww += 54 + len(b.get("extra_lines", [])) * 32
            if i < len(wd["blocks"]) - 1: hww += 18
        hww += 20
        hw += hww + GAP

    # Przyszłe dni (z draw_days_card: CP=20, TITLE_GAP=62, ROW=54, SEP=18)
    hd = 0
    if days:
        nd = len(days)
        hd = 20 + 62 + nd * 54 + max(0, nd - 1) * 18 + 20

    # Alerty (Precyzyjne liczenie wysokości dla idealnej kolumny)
    ha = 0
    if alerts:
        f_alrt_name = get_font("Inter-Medium.ttf", 34)
        f_alrt_desc = get_font("Inter-Regular.ttf", 30)  
        ALERT_ROW = 42
        ALERT_CP = 28
        # ZŁOTA KOLUMNA OPISÓW
        DESC_X = MARGIN + 400 
        desc_avail = WIDTH - MARGIN - DESC_X - 10
        
        ha = ALERT_CP + 58
        for a in alerts:
            if " — " in a:
                title, desc = a.split(" — ", 1)
            elif ": " in a:
                title, desc = a.split(": ", 1)
            else:
                title, desc = a, ""
            
            title = title.strip()
            desc = desc.strip()
            if desc: title += ":"
            
            # Łamanie tytułu
            title_words = title.split()
            title_lines = []
            current_tline = ""
            for tw in title_words:
                test_t = f"{current_tline} {tw}".strip()
                if dummy_draw.textlength(test_t, font=f_alrt_name) <= (DESC_X - MARGIN - ALERT_CP - 15):
                    current_tline = test_t
                else:
                    if current_tline: title_lines.append(current_tline)
                    current_tline = tw
            if current_tline: title_lines.append(current_tline)
            if not title_lines: title_lines = [title]
            
            # Łamanie opisu
            lines_desc = 0
            if desc:
                words = desc.split()
                line = ""
                lines_desc = 1
                for w in words:
                    test = f"{line} {w}".strip()
                    if dummy_draw.textlength(test, font=f_alrt_desc) <= desc_avail:
                        line = test
                    else:
                        lines_desc += 1
                        line = w

            # Wysokość to max z liczby linii tytułu i opisu
            total_lines = max(len(title_lines), max(1, lines_desc) if desc else 0)
            ha += total_lines * ALERT_ROW

        ha += ALERT_CP

    # Warto wiedzieć
    hwk = 0
    if data.get("worth_knowing"):
        hwk = 20 + 62 + 38 + 20

    # Weekend Teaser (Wysokość dla nowej karty z 2 dniami)
    wt = data.get("weekend_teaser")
    h_wt = 0
    if wt and isinstance(wt, dict):
        h_wt = 20 + 62 + (2 * 54) + 18 + 20 # Dokładne wyliczenie: Marginesy, Tytuł, 2 Wiersze

    # Stopka - zbalansowana wysokość (kompromis między ucięciem a oddechem)
    hf = 40

    # SUMOWANIE (z uwzględnieniem odstępów GAP między kartami)
    total = hh + ht + GAP
    total += hw  # hw dodaje swoje własne GAPy w pętli
    if days: total += hd + GAP
    if alerts: total += ha + GAP
    if hwk: total += hwk + GAP
    if h_wt: total += h_wt + GAP
    total += hf

    # Dajemy 200px bezpiecznego zapasu na "płótno". Na końcu dotniemy je gilotyną.
    HEIGHT = total + 200

    img = Image.new("RGB", (WIDTH, HEIGHT), pal["top"])
    draw_gradient(img, pal["top"], pal["bot"], HEIGHT)
    draw = ImageDraw.Draw(img)
    ov = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    cx1 = MARGIN; cx2 = WIDTH - MARGIN

    # WYŁĄCZAMY sztuczne pompowanie pustych przestrzeni! Odstępy mają być zwarte.
    gap = GAP

    # ═══ HEADER ═══
    TOP_SAFE_OFFSET = 140  # <--- Przesunięcie w dół (zmień by dostosować)
    TOP_FOOTER_Y = 40      # <--- Pozycja Y górnych napisów

    # Rysowanie górnego paska (kopia stopki)
    forecast_source = data.get("forecast_source", "OpenMeteo + Yr.no")
    source_label = data.get("source_label", "Źródło:") # Pobieramy z tłumacza
    draw.text((MARGIN, TOP_FOOTER_Y), f"{source_label} {forecast_source}", font=f_foot, fill=(255, 255, 255, 130))
    app_name = "Pogoda World"
    app_bbox = draw.textbbox((0, 0), app_name, font=f_foot)
    draw.text((WIDTH - MARGIN - (app_bbox[2] - app_bbox[0]), TOP_FOOTER_Y), app_name, font=f_foot, fill=(255, 255, 255, 130))

    # Przesunięty start rysowania
    y = MARGIN + TOP_SAFE_OFFSET

    draw.text((MARGIN, y), data.get("city", ""), font=f_city, fill="white")
    y += 82 + 15

    # Data (żółta, rozmiar tytułu) + raport (mniejszy, szary)
    wd = data.get("weekday", "")
    dt = data.get("date", "")
    rt = data.get("report_type", "")
    f_date_bold = get_font("Inter-Bold.ttf", 42)
    f_report    = get_font("Inter-Regular.ttf", 32)
    # Jeśli data jest pusta (jak w /future), nie wstawiaj przecinka!
    date_str = f"{wd}, {dt}" if wd and dt else f"{wd}{dt}"
    draw.text((MARGIN, y), date_str, font=f_date_bold, fill=TITLE_TODAY)
    date_w = draw.textlength(date_str, font=f_date_bold)
    #if rt:
    #    draw.text((MARGIN + date_w + 14, y + 5), f"· {rt}", font=f_report,
    #             fill=(170, 180, 200))
    if rt:
        rt_main = None
        rt_yellow = None
        
        # Szukamy słów-kluczy dla obu języków (najpierw wersje z nawiasem)
        trigger = None
        for t in ["(dane z", "(data from", "dane z", "data from"]:
            if t in rt:
                trigger = t
                break
                
        if trigger:
            parts = rt.split(trigger)
            rt_main = parts[0]               
            rt_yellow = trigger + parts[1]  

        # --- Rysowanie ---
        if rt_yellow is not None:
            # Szara część
            main_text = f"· {rt_main}"
            draw.text((MARGIN + date_w + 14, y + 5), main_text, font=f_report, fill=(170, 180, 200))
            # Żółta część
            main_w = draw.textlength(main_text, font=f_report)
            draw.text((MARGIN + date_w + 14 + main_w, y + 5), rt_yellow, font=f_report, fill=TITLE_TODAY)
        else:
            # Zwykły raport
            draw.text((MARGIN + date_w + 14, y + 5), f"· {rt}", font=f_report, fill=(170, 180, 200))
    
    y += 48 + 8

    # Ikona + temperatura
    paste_icon(img, MARGIN, y + 8, mi, size=(120, 120))
    draw.text((MARGIN + 140, y - 10), tr, font=f_temp, fill="white")
    y += 140

    # Summary i pressure (Obsługa dwolinijkowego tekstu w Hero)
    sm = data.get("summary", "")
    pr = data.get("pressure")
    
    if pr and "hPa" not in sm:
        summary_line = f"{sm}\n{pr}" if sm else pr
    else:
        summary_line = sm

    # 1. Rysujemy Summary (Zawsze na ZŁOTO)
    for line in (summary_line or "").split("\n"):
        draw.text((MARGIN, y), line, font=f_summ, fill=(254, 240, 138)) # <- Zmieniono na żółty
        y += 46  
        
    # 2. Rysujemy Context/Alerts (Zawsze na ZŁOTO)
    if ctx_lines:
        for line in ctx_lines:
            draw.text((MARGIN, y + 4), line, font=f_ctx, fill=(254, 240, 138)) # <- Zmieniono na żółty
            y += 38

    y += 20

    # ==========================================
    # BLOKI ŻÓŁTE (TERAŹNIEJSZOŚĆ / DZISIAJ)
    # ==========================================

    # 1. DZIŚ (Rano, Popołudnie, Wieczór / Reszta dnia)
    if blocks: 
        y = draw_blocks_card(draw, ov, img, blocks,
                             data.get("section_title", "Prognoza na dziś"),
                             y, cx1, cx2, pal)
        y += gap 

    # 2. ALERTY (Uważaj) - Priorytet dla bezpieczeństwa na dziś
    if alerts:
        f_alrt_name = get_font("Inter-Medium.ttf", 34)
        f_alrt_desc = get_font("Inter-Regular.ttf", 30)  
        ALERT_ROW = 42
        ALERT_CP = 28
        DESC_X = cx1 + 400  
        desc_avail = cx2 - DESC_X - 10
        
        draw_rounded_rect(ov, (cx1, y, cx2, y + ha), CR, pal["card"])
        #draw.text((cx1 + ALERT_CP, y + ALERT_CP), data.get("alert_title", "Uważaj"), font=f_atit, fill=(254, 240, 138))
        draw.text((cx1 + ALERT_CP, y + ALERT_CP), data.get("alert_title", "Uważaj"), font=f_atit, fill=(254, 240, 138))
        ay = y + ALERT_CP + 58
        
        for a in alerts:
            if " — " in a:
                title, desc = a.split(" — ", 1)
            elif ": " in a:
                title, desc = a.split(": ", 1)
            else:
                title, desc = a, ""
            
            title = title.strip()
            desc = desc.strip()
            if desc: title += ":"
            
            title_words = title.split()
            title_lines = []
            current_tline = ""
            max_title_w = DESC_X - (cx1 + ALERT_CP) - 15
            
            for tw in title_words:
                test_t = f"{current_tline} {tw}".strip()
                if draw.textlength(test_t, font=f_alrt_name) <= max_title_w:
                    current_tline = test_t
                else:
                    if current_tline: title_lines.append(current_tline)
                    current_tline = tw
            if current_tline: title_lines.append(current_tline)
            if not title_lines: title_lines = [title]
            
            ty = ay
            for t_line in title_lines:
                draw.text((cx1 + ALERT_CP, ty), t_line, font=f_alrt_name, fill=(252, 129, 129))
                ty += ALERT_ROW

            dy_desc = ay
            if len(title_lines) > 1:
                dy_desc = ay + (len(title_lines) - 1) * ALERT_ROW
                
            if desc:
                words = desc.split()
                line = ""
                for w in words:
                    test = f"{line} {w}".strip()
                    if draw.textlength(test, font=f_alrt_desc) <= desc_avail:
                        line = test
                    else:
                        draw.text((DESC_X, dy_desc), line, font=f_alrt_desc, fill=(230, 235, 245))
                        dy_desc += ALERT_ROW
                        line = w
                if line:
                    draw.text((DESC_X, dy_desc), line, font=f_alrt_desc, fill=(230, 235, 245))
                    dy_desc += ALERT_ROW
                    
            ay = max(ty, dy_desc)

        y += ha + gap

    # 3. DZIŚ WARTO WIEDZIEĆ (Ciekawostki / Wnioski na teraz)
    wk = data.get("worth_knowing")
    if wk:
        f_wk_title = get_font("Inter-Bold.ttf", 42)
        f_wk_text = get_font("Inter-Regular.ttf", 30)
        WK_CP = 20
        wk_text = wk["text"]
        
        # Złota kolumna opisów (jak w innych blokach)
        DESC_X = cx1 + 400 
        desc_avail = cx2 - DESC_X - 10

        # Inteligentne łamanie tekstu dla białego opisu
        words = (wk_text or "").split()
        wk_lines = []
        current_line = ""
        for w in words:
            test = f"{current_line} {w}".strip()
            if dummy_draw.textlength(test, font=f_wk_text) <= desc_avail:
                current_line = test
            else:
                if current_line: wk_lines.append(current_line)
                current_line = w
        if current_line: wk_lines.append(current_line)
        if not wk_lines: wk_lines = [wk_text]

        # Dynamiczne liczenie wysokości: Margines + Tytuł (+62) + Linie opisu + Margines
        wk_h = WK_CP + 62 + max(38, len(wk_lines) * 38) + WK_CP
        wk_card_fill = (255, 255, 255, 42)
        
        # Rysowanie tła karty
        draw_rounded_rect(ov, (cx1, y, cx2, y + wk_h), CR, wk_card_fill)
        
        # Rysowanie żółtego tytułu z lewej (ma całą linię dla siebie)
        draw.text((cx1 + WK_CP, y + WK_CP), wk["title"], font=f_wk_title, fill=TITLE_TODAY)
        
        # Rysowanie białego opisu: W NOWEJ LINII (+62) i w ZŁOTEJ KOLUMNIE (DESC_X)
        wk_dy = y + WK_CP + 62 
        for w_line in wk_lines:
            draw.text((DESC_X, wk_dy), w_line or "", font=f_wk_text, fill=(230, 235, 245))
            wk_dy += 38
            
        y += wk_h + gap

    # ==========================================
    # BLOKI ZIELONE (PRZYSZŁOŚĆ)
    # ==========================================

    # 4. NAJBLIŻSZE DNI
    future_order = data.get("future_order", [])
    next_days_title = data.get("next_days_title", "Najbliższe dni")

    if future_order:
        wday_iter = iter(wdays)
        summary_placed = False
        for section_type in future_order:
            if section_type == "detail":
                wd = next(wday_iter, None)
                if wd:
                    y = draw_blocks_card(draw, ov, img, wd["blocks"], wd["name"], y, cx1, cx2, pal, title_color=TITLE_FUTURE)
                    y += gap
            elif section_type == "summary" and not summary_placed:
                if days:
                    y = draw_days_card(draw, ov, img, days, next_days_title, y, cx1, cx2, pal, title_color=TITLE_FUTURE)
                    y += gap
                summary_placed = True
    else:
        for wdd_item in wdays:
            y = draw_blocks_card(draw, ov, img, wdd_item["blocks"], wdd_item["name"], y, cx1, cx2, pal, title_color=TITLE_FUTURE)
            y += gap
        if days:
            y = draw_days_card(draw, ov, img, days, next_days_title, y, cx1, cx2, pal, title_color=TITLE_FUTURE)
            y += gap

    # 5. KARTA WEEKENDOWA
    if wt and isinstance(wt, dict):
        sat = wt.get("sat", {})
        sun = wt.get("sun", {})

        # Tytuł dla bloków zajawki jest teraz wstrzykiwany przez Kierownika Ruchu!
        weekend_title = wt.get("title", "W ten weekend")

        weekend_days = [
            {
                "label": "Sob",
                "icon": sat.get("icon", "cloud"),
                "temp_min": sat.get("temp_min", "?"),
                "temp_max": sat.get("temp_max", "?"),
                "descriptor": sat.get("desc", sat.get("text", ""))
            },
            {
                "label": "Ndz",
                "icon": sun.get("icon", "cloud"),
                "temp_min": sun.get("temp_min", "?"),
                "temp_max": sun.get("temp_max", "?"),
                "descriptor": sun.get("desc", sun.get("text", ""))
            }
        ]

        y = draw_days_card(draw, ov, img, weekend_days, weekend_title, y, cx1, cx2, pal, title_color=TITLE_FUTURE)
        y += gap
    
    # ═══ STOPKA ═══
    # 'y' przechowuje dokładny koniec ostatniego bloku (+ ładny odstęp gap). 
    # Przypinamy stopkę dokładnie w tym miejscu!
    fy = y
    
    forecast_source = data.get("forecast_source", "OpenMeteo + Yr.no")
    source_label = data.get("source_label", "Źródło:") # Pobieramy z tłumacza
    draw.text((MARGIN, fy), f"{source_label} {forecast_source}", font=f_foot, fill=(140, 150, 170))
    draw.text((WIDTH - MARGIN - 230, fy), "Pogoda World", font=f_foot, fill=(140, 150, 170))

    # ═══ ZAPIS I GILOTYNA ═══
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    
    # MAGICZNE CIĘCIE: Ucinamy obrazek do perfekcyjnego wymiaru!
    # Czcionka zajmuje ok. 30px, dodajemy 60px soczystego, idealnego marginesu dolnego.
    final_height = int(fy + 90)
    img = img.crop((0, 0, WIDTH, final_height))
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fp = os.path.join(OUTPUT_DIR, "raport_telegram.png")
    img.save(fp, optimize=True, quality=92)
    print(f"  🎨 Karta zapisana: {fp} ({WIDTH}x{final_height})")
    return fp