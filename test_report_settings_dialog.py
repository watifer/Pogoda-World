"""
test_report_settings_dialog.py — PR3 GRUPY: konwersacyjna zmiana godzin raportów.

Uruchomienie: pytest test_report_settings_dialog.py -v

Zakres (bez Telegram API i bez Google Sheets — atrapy z test_access_gate):
- A. administrator grupy: /raport pokazuje godziny, „tak” zachowuje, „nie”
     przechodzi do pytań, wpisane wartości zapisują się dopiero na końcu,
- B. zwykły członek grupy: /raport nie startuje, nic się nie zapisuje,
- C. walidacja: 05:00 i 10:00 przechodzą rano, 13:00 i 16:00 po południu,
     wartości spoza okna są odrzucane bez przejścia dalej, „8:26” -> „08:26”,
     „brak” wyłącza slot,
- D. stan dialogu: cudza odpowiedź nie przejmuje, /raport anuluj nic nie
     zapisuje, TTL kończy dialog, ponowne /raport resetuje stan,
- E. dane: zapis trafia do istniejących kolumn Users, a scheduler (main_card)
     nadal widzi zapisane godziny; brak nowych/zduplikowanych kolumn,
- F. prywatny czat: ten sam dialog, bez klawiatury WebApp.
"""

import time

import pytest

import i18n
import location_bot as lb
import main_card
import users_store
from test_access_gate import BLOCKED_LEGACY_ROW, LEGACY_ROW, BotHarness

GROUP = -100500          # ujemny chat_id = rekord grupy w Users
ADMIN = 111              # administrator grupy (creator/administrator)
MEMBER = 222             # zwykły członek grupy
PRIVATE = 100


# ============================================================================
# ATRAPY I POMOCNICE
# ============================================================================
@pytest.fixture
def bot(monkeypatch):
    harness = BotHarness(monkeypatch, formularz_rows=[LEGACY_ROW, BLOCKED_LEGACY_ROW])
    lb.PENDING_REPORT.clear()
    return harness


@pytest.fixture
def as_admin(monkeypatch):
    """Telegram potwierdza status administratora — wyłącznie dla ADMIN."""
    monkeypatch.setattr(
        lb, "_telegram_chat_member_status",
        lambda chat_id, user_id: "administrator" if user_id == ADMIN else "member",
    )


@pytest.fixture
def as_member(monkeypatch):
    """Telegram zgłasza zwykłego członka grupy (nikt nie ma uprawnień)."""
    monkeypatch.setattr(lb, "_telegram_chat_member_status",
                        lambda chat_id, user_id: "member")


@pytest.fixture
def api_error(monkeypatch):
    """Sprawdzenie uprawnień kończy się błędem (fail-closed)."""
    monkeypatch.setattr(lb, "_telegram_chat_member_status",
                        lambda chat_id, user_id: None)


def seed_chat(bot, chat_id, lang="pl", morning="08:00", afternoon="14:00",
              with_profile=True, user_id=None):
    """Rekord Users z dostępem (opcjonalnie profilem) i godzinami raportów.

    Godziny startowe wpisujemy istniejącym helperem users_store — dokładnie
    tym, którego używa dialog i ścieżka WebApp.
    """
    bot.run(bot.msg(chat_id, "/start BETAX1", lang=lang, user_id=user_id))
    if with_profile:
        bot.run(bot.msg(chat_id, "/miasto", lang=lang, user_id=user_id))
        bot.run(bot.msg(chat_id, "Warszawa", lang=lang, user_id=user_id))
    if morning or afternoon:
        assert users_store.set_report_settings(bot.gc.users, chat_id, morning, afternoon)
    return bot.gc.users.record(chat_id)


def last(bot, chat_id):
    return bot.replies(chat_id)[-1]


def pending_report(chat_id, user_id):
    """Wpis dialogu odczytany stabilnym kluczem (czat + autor)."""
    return lb.PENDING_REPORT[lb._report_state_key(chat_id, user_id)]


# ============================================================================
# A. ADMINISTRATOR GRUPY
# ============================================================================
class TestGroupAdminDialog:

    def test_report_shows_current_hours_and_asks_to_keep_them(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))

        panel = last(bot, GROUP)
        assert "GODZINY RAPORTÓW" in panel
        assert "Rano: 08:00" in panel
        assert "Popołudnie: 14:00" in panel
        assert "Czy zachować te ustawienia?" in panel
        # Stan dialogu: czat + autor + etap + czas wygaśnięcia.
        pending = lb.PENDING_REPORT[lb._report_state_key(GROUP, ADMIN)]
        assert pending["stage"] == lb.STAGE_REPORT_CONFIRM
        assert pending["user_id"] == str(ADMIN)
        assert pending["expires_at"] > time.time()

    def test_yes_keeps_hours_without_touching_users(self, bot, as_admin):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "tak", user_id=ADMIN))

        assert "Zachowano obecne godziny raportów" in last(bot, GROUP)
        assert "rano 08:00, popołudnie 14:00" in last(bot, GROUP)
        assert bot.gc.users.record(GROUP) == before
        assert lb.PENDING_REPORT == {}

    def test_no_goes_to_morning_question(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))

        prompt = last(bot, GROUP)
        assert "raportu porannego" in prompt
        assert "05:00" in prompt and "10:00" in prompt
        assert "brak" in prompt
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_MORNING

    def test_full_flow_saves_both_slots_only_at_the_end(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "08:26", user_id=ADMIN))

        # Po pierwszej odpowiedzi NIE ma częściowego zapisu.
        record = bot.gc.users.record(GROUP)
        assert record["report_morning_time"] == "08:00"
        assert record["report_afternoon_time"] == "14:00"
        assert "raportu popołudniowego" in last(bot, GROUP)

        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN))

        summary = last(bot, GROUP)
        assert "Ustawiono godziny raportów" in summary
        assert "Raport poranny: 08:26" in summary
        assert "Raport popołudniowy: wyłączony" in summary
        assert "Oczekuj raportu porannego o 08:26." in summary

        record = bot.gc.users.record(GROUP)
        assert record["report_morning_time"] == "08:26"
        assert record["report_afternoon_time"] == "brak"
        assert lb.PENDING_REPORT == {}

    def test_both_slots_off_gives_one_short_message(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN))

        assert "Wyłączono oba raporty pogodowe." in last(bot, GROUP)
        record = bot.gc.users.record(GROUP)
        assert record["report_morning_time"] == "brak"
        assert record["report_afternoon_time"] == "brak"

    def test_disabled_slot_is_shown_as_brak(self, bot, as_admin):
        seed_chat(bot, GROUP, morning="brak", afternoon="14:00", user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))

        panel = last(bot, GROUP)
        assert "Rano: brak" in panel
        assert "Popołudnie: 14:00" in panel

    def test_repeated_report_restarts_dialog_from_current_values(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "07:30", user_id=ADMIN))
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_AFTERNOON

        # Ponowne /raport: stary stan znika, zaczynamy od wartości z Users.
        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        pending = pending_report(GROUP, ADMIN)
        assert pending["stage"] == lb.STAGE_REPORT_CONFIRM
        assert pending["morning"] is None and pending["afternoon"] is None
        assert "Rano: 08:00" in last(bot, GROUP)

        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN))
        record = bot.gc.users.record(GROUP)
        assert record["report_morning_time"] == "brak"
        assert record["report_afternoon_time"] == "brak"

    def test_cancel_command_endswithout_saving(self, bot, as_admin):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "/raport anuluj", user_id=ADMIN))

        assert "Anulowano zmianę godzin raportów." in last(bot, GROUP)
        assert lb.PENDING_REPORT == {}
        assert bot.gc.users.record(GROUP) == before

    def test_cancel_word_as_plain_answer_also_cancels(self, bot, as_admin):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "anuluj", user_id=ADMIN))

        assert "Anulowano zmianę godzin raportów." in last(bot, GROUP)
        assert lb.PENDING_REPORT == {}
        assert bot.gc.users.record(GROUP) == before

    def test_unreadable_confirm_answer_repeats_the_question(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "może", user_id=ADMIN))

        assert "Nie zrozumiałem odpowiedzi" in last(bot, GROUP)
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_CONFIRM


# ============================================================================
# B. UPRAWNIENIA W GRUPIE
# ============================================================================
class TestGroupPermissions:

    def test_plain_member_does_not_start_dialog(self, bot, as_member):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=MEMBER))

        assert "Tylko administrator tej grupy" in last(bot, GROUP)
        assert lb.PENDING_REPORT == {}, "zwykły członek nie może otworzyć dialogu"
        assert bot.gc.users.record(GROUP) == before

    def test_member_answer_cannot_hijack_admin_dialog(self, bot, as_admin):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_MORNING

        # Odpowiedź zwykłego członka: zero zmian stanu i zero zapisów.
        bot.run(bot.msg(GROUP, "brak", user_id=MEMBER))
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_MORNING
        assert pending_report(GROUP, ADMIN)["user_id"] == str(ADMIN)
        assert bot.gc.users.record(GROUP) == before

        # Dopiero odpowiedź administratora przesuwa dialog dalej.
        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN, chat_type="group"))
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_AFTERNOON
        assert bot.gc.users.record(GROUP) == before, "zapis dopiero po obu slotach"

    def test_api_error_blocks_change_and_shows_safe_message(self, bot, api_error):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))

        assert "Nie udało się sprawdzić Twoich uprawnień" in last(bot, GROUP)
        assert lb.PENDING_REPORT == {}
        assert bot.gc.users.record(GROUP) == before

    def test_group_language_comes_from_group_record_not_admin(self, bot, as_admin):
        # Rekord grupy ma lang=en, a administrator pisze z telefonu PL.
        seed_chat(bot, GROUP, lang="en", user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", lang="pl", user_id=ADMIN))

        panel = last(bot, GROUP)
        assert "REPORT HOURS" in panel, "język grupy, nie język administratora"
        assert "Czy zachować" not in panel
        assert i18n.report_word("en", "yes") in panel


# ============================================================================
# C. WALIDACJA GODZIN
# ============================================================================
class TestValidation:

    @pytest.mark.parametrize("value,expected", [
        ("05:00", "05:00"),
        ("10:00", "10:00"),
        ("07:30", "07:30"),
        ("08:26", "08:26"),
        ("8:26", "08:26"),   # normalizacja do HH:MM
        ("5:00", "05:00"),
    ])
    def test_morning_window_boundaries_are_accepted(self, bot, as_admin, value, expected):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, value, user_id=ADMIN))

        assert "raportu popołudniowego" in last(bot, GROUP)
        assert pending_report(GROUP, ADMIN)["morning"] == expected

    @pytest.mark.parametrize("value", [
        "04:59", "10:01", "11:00", "12:00", "16:00", "24:00", "25:00",
        "8:5", "abc", "rano", "0", "",
    ])
    def test_morning_values_outside_window_are_rejected(self, bot, as_admin, value):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, value, user_id=ADMIN))

        # Błędna wartość: powtórzone to samo pytanie, bez przejścia dalej.
        assert "raportu porannego" in last(bot, GROUP)
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_MORNING
        assert bot.gc.users.record(GROUP) == before

    @pytest.mark.parametrize("value,expected", [
        ("13:00", "13:00"),
        ("16:00", "16:00"),
        ("14:00", "14:00"),
        ("14:05", "14:05"),
    ])
    def test_afternoon_window_boundaries_are_accepted(self, bot, as_admin, value, expected):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "08:00", user_id=ADMIN))
        bot.run(bot.msg(GROUP, value, user_id=ADMIN))

        assert "Ustawiono godziny raportów" in last(bot, GROUP)
        record = bot.gc.users.record(GROUP)
        assert record["report_afternoon_time"] == expected

    @pytest.mark.parametrize("value", [
        "12:59", "16:01", "17:00", "24:00", "25:00", "abc",
    ])
    def test_afternoon_values_outside_window_are_rejected(self, bot, as_admin, value):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "08:00", user_id=ADMIN))
        bot.run(bot.msg(GROUP, value, user_id=ADMIN))

        assert "raportu popołudniowego" in last(bot, GROUP)
        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_AFTERNOON
        assert bot.gc.users.record(GROUP) == before

    @pytest.mark.parametrize("word", ["brak", "off"])
    def test_off_word_disables_the_slot(self, bot, as_admin, word):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, word, user_id=ADMIN))
        assert pending_report(GROUP, ADMIN)["morning"] == "brak"
        bot.run(bot.msg(GROUP, "14:00", user_id=ADMIN))

        record = bot.gc.users.record(GROUP)
        assert record["report_morning_time"] == "brak"
        assert record["report_afternoon_time"] == "14:00"
        assert "Raport poranny: wyłączony" in last(bot, GROUP)

    def test_empty_answer_is_not_an_automatic_disable(self, bot, as_admin):
        """Pusta/nieczytelna odpowiedź nie wyłącza slotu — trzeba wpisać „brak”."""
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "   ", user_id=ADMIN))

        assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_MORNING
        assert bot.gc.users.record(GROUP) == before

    def test_parser_is_the_single_source_of_truth_for_both_prompts(self):
        # Komunikaty pytań i walidacja czytają te same okna.
        assert lb._parse_report_time("11:00", lb.REPORT_MORNING_WINDOW) is None
        assert lb._parse_report_time("10:00", lb.REPORT_MORNING_WINDOW) == "10:00"
        assert lb._parse_report_time("13:00", lb.REPORT_AFTERNOON_WINDOW) == "13:00"
        assert lb._parse_report_time("16:00", lb.REPORT_AFTERNOON_WINDOW) == "16:00"
        assert lb._parse_report_time("16:01", lb.REPORT_AFTERNOON_WINDOW) is None


# ============================================================================
# D. STAN DIALOGU
# ============================================================================
class TestDialogState:
    """Klucz stanu = chat_id + admin_user_id; stage i expires_at to pola wartości."""

    def test_state_key_is_chat_id_plus_admin_user_id(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))

        assert list(lb.PENDING_REPORT) == [f"{GROUP}:{ADMIN}"]
        entry = lb.PENDING_REPORT[f"{GROUP}:{ADMIN}"]
        # Klucz NIE niesie ani etapu, ani czasu wygaśnięcia — to pola wartości.
        assert entry["chat_id"] == str(GROUP)
        assert entry["user_id"] == str(ADMIN)
        assert entry["stage"] == lb.STAGE_REPORT_CONFIRM
        assert isinstance(entry["expires_at"], float)
        assert entry["morning"] is None and entry["afternoon"] is None

    def test_key_stays_the_same_across_all_stages(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        key_confirm = list(lb.PENDING_REPORT)[0]
        expires_confirm = pending_report(GROUP, ADMIN)["expires_at"]

        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        key_morning = list(lb.PENDING_REPORT)[0]
        entry = pending_report(GROUP, ADMIN)
        assert entry["stage"] == lb.STAGE_REPORT_MORNING
        assert key_morning == key_confirm, "przejście confirm -> morning nie zmienia klucza"
        assert entry["expires_at"] >= expires_confirm, "TTL liczy się od nowa"

        bot.run(bot.msg(GROUP, "08:26", user_id=ADMIN))
        key_afternoon = list(lb.PENDING_REPORT)[0]
        entry = pending_report(GROUP, ADMIN)
        assert entry["stage"] == lb.STAGE_REPORT_AFTERNOON
        assert key_afternoon == key_confirm, "przejście morning -> afternoon nie zmienia klucza"
        assert entry["morning"] == "08:26"
        assert len(lb.PENDING_REPORT) == 1, "jeden wpis na czat"

    def test_rejected_value_keeps_the_same_entry(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        key = list(lb.PENDING_REPORT)[0]

        for bad in ("04:59", "10:01", "abc"):
            bot.run(bot.msg(GROUP, bad, user_id=ADMIN))
            assert list(lb.PENDING_REPORT) == [key]
            assert pending_report(GROUP, ADMIN)["stage"] == lb.STAGE_REPORT_MORNING
            assert pending_report(GROUP, ADMIN)["morning"] is None

    def test_member_finds_no_state_under_any_key(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))

        assert lb._get_pending_report(GROUP, MEMBER) is None
        assert lb._get_pending_report(GROUP, 999) is None
        assert lb._get_pending_report(PRIVATE, ADMIN) is None
        assert lb._get_pending_report(GROUP, ADMIN) is not None

    def test_second_admin_gets_own_key_and_closes_the_previous_one(self, bot, monkeypatch):
        monkeypatch.setattr(lb, "_telegram_chat_member_status",
                            lambda chat_id, user_id: "administrator")
        other_admin = 333
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        assert list(lb.PENDING_REPORT) == [f"{GROUP}:{ADMIN}"]

        bot.run(bot.msg(GROUP, "/raport", user_id=other_admin))
        # Jeden aktywny dialog na czat — nowy właściciel, własny klucz.
        assert list(lb.PENDING_REPORT) == [f"{GROUP}:{other_admin}"]
        assert lb._get_pending_report(GROUP, ADMIN) is None

    def test_timeout_removes_the_entry_under_the_same_key(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        key = list(lb.PENDING_REPORT)[0]
        pending_report(GROUP, ADMIN)["expires_at"] = time.time() - 1

        assert lb._get_pending_report(GROUP, ADMIN) is None
        assert lb.PENDING_REPORT == {}
        assert key == f"{GROUP}:{ADMIN}"

        # Po wygaśnięciu wpis znika także przy okazji zwykłego sprzątania RAM.
        lb._set_pending_report(GROUP, ADMIN, lb.STAGE_REPORT_CONFIRM)
        pending_report(GROUP, ADMIN)["expires_at"] = time.time() - 1
        lb._prune_expired_pending()
        assert lb.PENDING_REPORT == {}

    def test_cancel_clears_exactly_the_owners_entry(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        assert len(lb.PENDING_REPORT) == 1

        bot.run(bot.msg(GROUP, "/raport anuluj", user_id=ADMIN))
        assert lb.PENDING_REPORT == {}
        assert lb._get_pending_report(GROUP, ADMIN) is None

    def test_expired_dialog_ends_without_saving(self, bot, as_admin):
        before = seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        pending_report(GROUP, ADMIN)["expires_at"] = time.time() - 1

        bot.run(bot.msg(GROUP, "tak", user_id=ADMIN))

        assert "Zachowano obecne godziny raportów" not in last(bot, GROUP)
        assert lb.PENDING_REPORT == {}
        assert bot.gc.users.record(GROUP) == before

    def test_ttl_is_between_five_and_ten_minutes(self):
        assert 300 <= lb.PENDING_REPORT_TTL_SEC <= 600

    def test_other_command_ends_the_dialog(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        assert lb.PENDING_REPORT.get(lb._report_state_key(GROUP, ADMIN))

        bot.run(bot.msg(GROUP, "/info", user_id=ADMIN))
        assert lb.PENDING_REPORT == {}

    def test_member_command_does_not_clear_admin_dialog(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "/info", user_id=MEMBER))

        assert pending_report(GROUP, ADMIN)["user_id"] == str(ADMIN)

    def test_pending_state_is_never_persisted_to_sheets(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)
        headers = list(bot.gc.users.grid[0])

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))

        assert list(bot.gc.users.grid[0]) == headers, "żadnych nowych kolumn stanu"
        values = [str(cell) for row in bot.gc.users.grid[1:] for cell in row]
        assert not any(word in value for value in values
                       for word in ("confirm", "morning", "afternoon", "expires_at"))


# ============================================================================
# E. DANE I SCHEDULER
# ============================================================================
class TestDataAndScheduler:

    def test_only_the_two_existing_report_columns_are_written(self, bot, as_admin):
        before = seed_chat(bot, GROUP, user_id=ADMIN)
        headers = list(bot.gc.users.grid[0])

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "08:26", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "14:30", user_id=ADMIN))

        after = bot.gc.users.record(GROUP)
        assert list(bot.gc.users.grid[0]) == headers
        assert set(headers) == set(users_store.CANONICAL_HEADERS)
        assert len(headers) == len(set(headers)), "bez podwójnych kolumn"
        changed = {k for k in before if before[k] != after[k]}
        assert changed == {"report_morning_time", "report_afternoon_time"}
        assert len(bot.gc.users.grid) == 2, "jeden wiersz grupy, bez duplikatów"

    def test_scheduler_still_sees_the_saved_hours(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "08:26", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "14:30", user_id=ADMIN))

        parsed = main_card._parse_scheduler_users([bot.gc.users.record(GROUP)])
        assert len(parsed) == 1
        assert parsed[0]["godzina_rano"] == "08:26"
        assert parsed[0]["godzina_wieczor"] == "14:30"
        assert parsed[0]["chat_id"] == str(GROUP)

    def test_disabled_slot_is_invisible_to_the_scheduler(self, bot, as_admin):
        seed_chat(bot, GROUP, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "brak", user_id=ADMIN))

        parsed = main_card._parse_scheduler_users([bot.gc.users.record(GROUP)])
        assert parsed == [], "oba raporty wyłączone = brak pozycji w schedulerze"

    def test_report_alone_never_activates_reports_without_profile(self, bot, as_admin):
        # Grupa ma rekord Users, ale nie ma profilu — /raport nie włącza raportów.
        seed_chat(bot, GROUP, with_profile=False, user_id=ADMIN)

        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "nie", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "08:00", user_id=ADMIN))
        bot.run(bot.msg(GROUP, "14:00", user_id=ADMIN))

        record = bot.gc.users.record(GROUP)
        assert record["report_morning_time"] == "08:00"
        assert main_card._parse_scheduler_users([record]) == [], \
            "bez profilu scheduler nadal nie wysyła raportów"

    def test_group_without_users_record_gets_a_localized_message(self, bot, as_admin):
        # Grupa ma dostęp przez legacy Formularz, ale nie ma rekordu w Users:
        # dialog nie startuje i NICZEGO nie tworzy.
        bot.gc.formularz.rows.append(
            ["2026-01-01 08:00:00", str(GROUP), "Grupa testowa", "Gdańsk",
             "54.35", "18.65", "08:00", "14:00", "pl"],
        )
        bot.run(bot.msg(GROUP, "/raport", user_id=ADMIN))

        assert "GODZINY RAPORTÓW" in last(bot, GROUP)
        assert lb.PENDING_REPORT == {}
        assert bot.gc.users.record(GROUP) is None


# ============================================================================
# F. PRYWATNY CZAT — TEN SAM DIALOG
# ============================================================================
class TestPrivateChat:

    def test_private_dialog_works_without_webapp_keyboard(self, bot):
        seed_chat(bot, PRIVATE)

        bot.run(bot.msg(PRIVATE, "/raport"))

        panel = last(bot, PRIVATE)
        assert "GODZINY RAPORTÓW" in panel
        assert "Rano: 08:00" in panel
        assert "Czy zachować te ustawienia?" in panel
        # WebApp nie jest już oferowany do zmiany godzin (także prywatnie):
        # własna wiadomość panelu nie niesie żadnej klawiatury.
        assert bot.markups_for(PRIVATE)[-1] is None
        assert "watifer.github.io" not in panel

    def test_private_flow_saves_hours(self, bot):
        seed_chat(bot, PRIVATE)

        bot.run(bot.msg(PRIVATE, "/raport"))
        bot.run(bot.msg(PRIVATE, "nie"))
        bot.run(bot.msg(PRIVATE, "9:30"))
        bot.run(bot.msg(PRIVATE, "15:00"))

        record = bot.gc.users.record(PRIVATE)
        assert record["report_morning_time"] == "09:30"
        assert record["report_afternoon_time"] == "15:00"
        assert "Oczekuj raportu popołudniowego o 15:00." in last(bot, PRIVATE)

    def test_private_dialog_uses_users_language(self, bot):
        seed_chat(bot, PRIVATE, lang="de")

        bot.run(bot.msg(PRIVATE, "/raport"))

        assert "BERICHTSZEITEN" in last(bot, PRIVATE)
        assert i18n.report_word("de", "yes") in last(bot, PRIVATE)

    def test_private_webapp_location_flow_still_works(self, bot, monkeypatch):
        """WebApp nadal działa — tyle że do lokalizacji (/miasto), nie do godzin."""
        seed_chat(bot, PRIVATE, with_profile=False)

        bot.run(bot.msg(PRIVATE, "/miasto"))
        bot.run(bot.webapp_location(PRIVATE, lat=54.5, lon=18.5))

        record = bot.gc.users.record(PRIVATE)
        assert record["lat_round"] == "54.5"
        assert record["location_source"] == "webapp"

    def test_legacy_only_chat_still_gets_migration_message(self, bot):
        # Użytkownik wyłącznie z legacy Formularz (bez rekordu Users).
        bot.run(bot.msg(700, "/raport"))

        assert "w trakcie przenoszenia" in last(bot, 700)
        assert lb.PENDING_REPORT == {}
