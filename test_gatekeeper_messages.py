"""test_gatekeeper_messages.py — komunikaty Gatekeepera P7 + przycisk copy_text.

Obejmuje 18 wymagań z zadania Etapu 2 dla zatwierdzonych tekstów:

  1. access_granted == welcome_access we wszystkich językach
  2. wszystkie 10 kluczy obecne we wszystkich 6 językach
  3. no_access zawiera aktualny dynamiczny public_code
  4. no_access bez backticków
  5. registration_closed bez kodu / {url} / {max_users}
  6. limit_reached bez {url} / {max_users}
  7. invalid_link bez {url}
  8. komendy zwykłym tekstem
  9. brak pustych linii pomiędzy komendami
 10. dokładnie jeden \n\n między sekcjami 🔔 i 🔒
 11. copy_text.text == "/start " + aktualny public_code
 12. rotacja PUBLIC_BETA_CODES aktualizuje tekst i przycisk
 13. reply_markup tylko dla no_access
 14. copy_text nie generuje callback_query i nie zmienia stanu
 15. pokrycie wszystkich 6 języków
 16. testy Gatekeepera P7 (test_access_gate.py)
 17. testy onboardingu (test_welcome_privacy.py)
 18. brak błędów str.format()

Uruchomienie: pytest test_gatekeeper_messages.py -v
"""

import re

import pytest

import i18n
import location_bot as lb
from test_access_gate import BotHarness, LEGACY_ROW, BLOCKED_LEGACY_ROW

LANGS = ("pl", "en", "de", "es", "fr", "no")
KEYS = (
    "no_access", "no_access_no_code", "registration_closed", "limit_reached",
    "invalid_link", "access_granted", "welcome_access", "welcome_back",
    "reg_err", "copy_access_command",
)


@pytest.fixture
def bot(monkeypatch):
    return BotHarness(monkeypatch, formularz_rows=[LEGACY_ROW, BLOCKED_LEGACY_ROW])


def _button(markup):
    """Wyciąga przycisk z reply_markup albo zwraca None."""
    if not markup:
        return None
    rows = markup.get("inline_keyboard") or []
    for row in rows:
        for btn in row:
            if "copy_text" in btn:
                return btn
    return None


# ============================================================================
# 1. access_granted == welcome_access
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_access_granted_equals_welcome_access(lang):
    assert i18n.UI_TEXTS[lang]["access_granted"] == i18n.UI_TEXTS[lang]["welcome_access"]


# ============================================================================
# 2. Kompletność kluczy
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", KEYS)
def test_all_keys_exist_in_every_language(lang, key):
    assert key in i18n.UI_TEXTS[lang], f"{lang}: brak klucza {key}"
    assert i18n.UI_TEXTS[lang][key].strip(), f"{lang}: klucz {key} jest pusty"


# ============================================================================
# 3 + 4. no_access: dynamiczny kod i brak backticków
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_no_access_uses_dynamic_public_code(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODZDYNAMICZNY")
    monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
    text = lb._invite_message(lang, "no_access")
    assert lb._primary_public_code() == "KODZDYNAMICZNY"
    assert "/start KODZDYNAMICZNY" in text, (lang, text)
    assert "{public_code}" not in text, (lang, text)
    assert "`" not in text, (lang, text)
    assert "`/start" not in text, (lang, text)


@pytest.mark.parametrize("lang", LANGS)
def test_no_access_markup_copies_same_code_as_placeholder(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODZDYNAMICZNY")
    monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
    text, markup = lb._no_access_parts(lang)
    assert f"/start {lb._primary_public_code()}" in text
    btn = _button(markup)
    assert btn is not None, (lang, markup)
    assert btn["copy_text"]["text"] == f"/start {lb._primary_public_code()}"


# ============================================================================
# 5 + 6 + 7. D1/D2: brak {url}, {max_users}, kodu i liczby miejsc
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ("registration_closed", "limit_reached"))
def test_block_messages_have_no_link_no_count(monkeypatch, lang, key):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "SEKRETNITYKOD")
    monkeypatch.setenv("MAX_USERS", "7")
    text = lb._invite_message(lang, key)
    assert "{url}" not in text, (lang, key, text)
    assert "{max_users}" not in text, (lang, key, text)
    assert "https://" not in text, (lang, key, text)
    assert "SEKRETNITYKOD" not in text, (lang, key, text)
    assert not re.search(r"\d+\s*/\s*\d+", text), (lang, key, text)
    assert "{" not in text and "}" not in text, (lang, key, text)


@pytest.mark.parametrize("lang", LANGS)
def test_invalid_link_has_no_url(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_INVITE_URL", "https://example.invalid/secret/")
    text = lb._invite_message(lang, "invalid_link")
    assert "{url}" not in text, (lang, text)
    assert "https://" not in text, (lang, text)
    assert "{" not in text and "}" not in text, (lang, text)


@pytest.mark.parametrize("lang", LANGS)
def test_invalid_link_stays_distinct_from_registration_closed(monkeypatch, lang):
    invalid = lb._invite_message(lang, "invalid_link")
    closed = lb._invite_message(lang, "registration_closed")
    assert invalid != closed, lang
    assert "Nieprawidłowy" not in closed, lang


@pytest.mark.parametrize("lang", LANGS)
def test_no_access_no_code_has_no_placeholder(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "")
    monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
    text, markup = lb._no_access_parts(lang)
    assert "{public_code}" not in text, (lang, text)
    assert "{" not in text and "}" not in text, (lang, text)
    assert markup is None, "brak kodu => brak przycisku kopiującego"


# ============================================================================
# 8 + 9 + 10. Formatowanie i klikalność
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", KEYS)
def test_commands_are_plain_clickable_text(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert "`" not in text, f"{lang}/{key}: backtick"
    assert not re.search(r"`/", text), f"{lang}/{key}: komenda w backtickach"
    assert "\\/" not in text, f"{lang}/{key}: escapowany slash"
    assert not re.search(r"</?[a-zA-Z][^>]*>", text), f"{lang}/{key}: HTML"
    for m in re.finditer(r"(?<!\S)/[a-zA-Z]+", text):
        end = m.end()
        nxt = text[end] if end < len(text) else "\n"
        assert nxt.isspace() or not nxt.isalpha(), \
            f"{lang}/{key}: /{m.group(0)[1:]} niewykonalne"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ("access_granted", "welcome_access", "welcome_back"))
def test_no_blank_lines_between_commands(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert not re.search(r"/[a-z]+[^\n]*\n\n/[a-z]+", text), \
        f"{lang}/{key}: pusta linia pomiędzy komendami"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ("access_granted", "welcome_access"))
def test_bell_and_lock_sections_separated_by_one_blank_line(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert "\n\n🔔" in text, f"{lang}/{key}: brak \\n\\n przed 🔔"
    assert "\n\n🔒" in text, f"{lang}/{key}: brak \\n\\n przed 🔒"
    assert "dni🔔" not in text and "🔔🔒" not in text, f"{lang}/{key}: sklejone sekcje"
    assert "\n\n\n" not in text, f"{lang}/{key}: więcej niż jedna pusta linia"


@pytest.mark.parametrize("lang", LANGS)
def test_welcome_back_sections_not_glued(lang):
    text = i18n.UI_TEXTS[lang]["welcome_back"]
    assert "\n\n⚙️" in text, f"{lang}: brak \\n\\n przed ⚙️"
    assert "\n\n\n" not in text, f"{lang}: więcej niż jedna pusta linia"


# ============================================================================
# 11 + 12. copy_text: treść i rotacja kodów
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_copy_text_equals_start_plus_public_code(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "ALFA2026")
    monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
    _text, markup = lb._no_access_parts(lang)
    btn = _button(markup)
    assert btn is not None, (lang, markup)
    assert btn["copy_text"]["text"] == "/start ALFA2026"
    assert btn["text"] == i18n.UI_TEXTS[lang]["copy_access_command"]


@pytest.mark.parametrize("lang", LANGS)
def test_rotation_updates_both_message_and_button(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "STARYKOD")
    monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
    text1, markup1 = lb._no_access_parts(lang)
    assert "/start STARYKOD" in text1
    assert _button(markup1)["copy_text"]["text"] == "/start STARYKOD"

    monkeypatch.setenv("PUBLIC_BETA_CODES", "NOWYKOD")
    text2, markup2 = lb._no_access_parts(lang)
    assert "/start NOWYKOD" in text2
    assert _button(markup2)["copy_text"]["text"] == "/start NOWYKOD"
    assert "STARYKOD" not in text2
    assert "STARYKOD" not in _button(markup2)["copy_text"]["text"]


def test_copy_text_length_within_bot_api_limit(monkeypatch):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "K" * 200)
    _text, markup = lb._no_access_parts("pl")
    copied = _button(markup)["copy_text"]["text"]
    assert 1 <= len(copied) <= 256, len(copied)


@pytest.mark.parametrize("lang", LANGS)
def test_button_never_contains_master_token_or_private_codes(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "JEDYNYKODPUBLICZNY")
    monkeypatch.setenv("MASTER_TOKEN", "SUPERSEKRETMASTERTOKEN")
    _text, markup = lb._no_access_parts(lang)
    btn = _button(markup)
    copied = btn["copy_text"]["text"]
    assert copied == "/start JEDYNYKODPUBLICZNY"
    assert "SUPERSEKRETMASTERTOKEN" not in copied
    assert "SUPERSEKRET" not in str(markup)


# ============================================================================
# 13. reply_markup tylko dla no_access
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_markup_only_for_no_access(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "JAKISKOD")
    monkeypatch.setenv("MAX_USERS", "200")
    _text, markup = lb._no_access_parts(lang)
    assert _button(markup) is not None

    # pozostałe klucze gatekeepera: czysty tekst, bez reply_markup
    for key in ("access_granted", "welcome_access", "welcome_back",
                "no_access_no_code", "registration_closed",
                "limit_reached", "invalid_link", "reg_err"):
        assert lb._invite_message(lang, key) == lb.t_ui(lang, key), (lang, key)


@pytest.mark.parametrize("lang", LANGS)
def test_bare_start_sends_markup_only_for_no_access(bot, monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODPRZEZBOTEM")
    monkeypatch.setenv("REGISTRATION_ENABLED", "1")
    cid = 3100 + LANGS.index(lang)
    bot.run(bot.msg(cid, "/start", lang=lang))
    texts = bot.replies(cid)
    markups = bot.markups_for(cid)
    assert len(texts) == 1
    assert "/start KODPRZEZBOTEM" in texts[0]
    assert _button(markups[0]) is not None
    assert _button(markups[0])["copy_text"]["text"] == "/start KODPRZEZBOTEM"


def test_closed_registration_sends_no_markup(empty_bot_holder, monkeypatch):
    """registration_closed / limit_reached: zero reply_markup."""
    local = empty_bot_holder
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODPRZEZBOTEM")
    monkeypatch.setenv("REGISTRATION_ENABLED", "0")
    cid = 3200
    local.run(local.msg(cid, "/start"))
    markups = local.markups_for(cid)
    assert all(m is None for m in markups), markups


@pytest.fixture
def empty_bot_holder(monkeypatch):
    return BotHarness(monkeypatch, formularz_rows=[])


@pytest.fixture
def fake_sleep(monkeypatch):
    """Przechwytuje pauzę między komunikatami po hard delete (bez realnego czekania)."""
    calls = []
    monkeypatch.setattr(lb.time, "sleep", lambda s: calls.append(s))
    return calls


def test_hard_delete_path_also_sends_copy_button(monkeypatch, fake_sleep):
    """Ścieżka po hard delete wysyła ten sam no_access z przyciskiem."""
    local = BotHarness(monkeypatch, formularz_rows=[LEGACY_ROW, BLOCKED_LEGACY_ROW])
    cid = 3300
    local.run(local.msg(cid, "/start BETAX1"))
    assert local.users.record(cid) is not None, "rejestracja musi się udać"
    # rotacja kodu dopiero PO aktywacji — no_access po skasowaniu ma nowy kod
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODPOPOZAKASACJA")
    local.run(local.msg(cid, "/usunDane"))
    before = len(local.sent)
    local.run(local.msg(cid, lb.t_ui("pl", "delete_confirm_yes")))

    texts = [t for c, t in local.sent[before:] if c == cid]
    markups = [m for (c, _t), m in zip(local.sent, local.markups) if c == cid]
    assert len(texts) == 2, texts
    assert "/start KODPOPOZAKASACJA" in texts[1], texts[1]
    btn = _button(markups[-1])
    assert btn is not None, markups[-1]
    assert btn["copy_text"]["text"] == "/start KODPOPOZAKASACJA"


# ============================================================================
# 14. copy_text nie generuje callback_query i nie zmienia stanu
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_copy_button_is_not_a_callback_button(monkeypatch, lang):
    """copy_text nie jest callback_data — przycisk nie generuje callback_query."""
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODBEZSTANU")
    _text, markup = lb._no_access_parts(lang)
    btn = _button(markup)
    assert "callback_data" not in btn, "callback_data uruchomiłoby handler"
    assert "url" not in btn, "przycisk nie może być linkiem"
    assert "web_app" not in btn
    assert "login_url" not in btn
    assert set(btn) == {"text", "copy_text"}, btn


def test_sending_no_access_does_not_change_user_state(bot, monkeypatch):
    """Wysłanie no_access nie tworzy rekordu i nie aktywuje dostępu."""
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODSTAN")
    cid = 3400
    before = bot.users.record(cid)
    assert before is None
    bot.run(bot.msg(cid, "/start"))
    assert bot.users.record(cid) is None, "no_access nie może tworzyć rekordu"
    assert _button(bot.markups_for(cid)[0]) is not None


def test_button_does_not_bypass_gatekeeper(bot, monkeypatch):
    """Samo wysłanie no_access nie rejestruje — rejestracja wymaga /start z kodem."""
    monkeypatch.setenv("PUBLIC_BETA_CODES", "KODGATE")
    cid = 3500
    bot.run(bot.msg(cid, "/start"))
    assert bot.users.record(cid) is None
    assert bot.has_reply(cid, "bot prywatny")
    # dopiero prawidłowe /start z kodem aktywuje dostęp
    bot.run(bot.msg(cid, "/start KODGATE"))
    assert bot.users.record(cid) is not None
    assert bot.users.record(cid)["access_status"] == "granted"


# ============================================================================
# 18. Brak błędów str.format()
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", KEYS)
def test_every_message_renders_without_format_error(lang, key):
    """t_ui() używa str.format() — żaden komunikat nie może rzucić wyjątku."""
    kwargs = {"public_code": "KOD", "url": "https://example.invalid/", "max_users": 200}
    rendered = i18n.t_ui(lang, key, **kwargs)
    assert rendered == i18n.UI_TEXTS[lang][key].replace("{public_code}", "KOD") \
        if "{public_code}" in i18n.UI_TEXTS[lang][key] else rendered == i18n.UI_TEXTS[lang][key]
    assert "{" not in rendered and "}" not in rendered


@pytest.mark.parametrize("lang", LANGS)
def test_no_access_no_code_renders_without_code(monkeypatch, lang):
    monkeypatch.setenv("PUBLIC_BETA_CODES", "")
    monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
    text, markup = lb._no_access_parts(lang)
    assert "{" not in text and "}" not in text
    assert markup is None