"""Regression tests for the Users-only scheduled-report reader."""

import pytest

import main_card
import users_store


USERS_HEADERS = users_store.CANONICAL_HEADERS


def users_row(chat_id="101", **overrides):
    values = {
        "chat_id": str(chat_id),
        "access_status": "granted",
        "profile_status": "active",
        "lat_round": "52.230",
        "lon_round": "21.012",
        "location_label": "Warszawa",
        "lang": "pl",
        "report_morning_time": "",
        "report_afternoon_time": "",
    }
    values.update(overrides)
    return [values.get(header, "") for header in USERS_HEADERS]


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = [list(row) for row in rows]
        self.ranges = []

    def get_values(self, range_name):
        self.ranges.append(range_name)
        return [row[:] for row in self.rows]


class FakeSpreadsheet:
    def __init__(self, tabs):
        self.tabs = tabs
        self.requested_tabs = []

    def worksheet(self, name):
        self.requested_tabs.append(name)
        if name not in self.tabs:
            raise RuntimeError(f"Worksheet not found: {name}")
        return self.tabs[name]


class FakeClient:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self.opened = []

    def open(self, title):
        self.opened.append(title)
        return self.spreadsheet


def scheduler_client(users_rows, formularz_rows=None):
    tabs = {
        "Users": FakeWorksheet([USERS_HEADERS] + list(users_rows)),
    }
    if formularz_rows is not None:
        tabs["Formularz"] = FakeWorksheet(formularz_rows)
    spreadsheet = FakeSpreadsheet(tabs)
    return FakeClient(spreadsheet), spreadsheet


def test_scheduler_loader_reads_users_only_and_ignores_sheet_tab(monkeypatch):
    client, spreadsheet = scheduler_client([users_row("101", report_morning_time="08:00")])
    monkeypatch.setattr(main_card, "SHEET_ID", "")
    monkeypatch.setattr(main_card, "SHEET_NAME", "Pogoda_Users")
    monkeypatch.setattr(main_card, "SHEET_TAB_ENV", "Formularz")

    rows = main_card._load_scheduler_users_from_sheet(client=client)

    assert len(rows) == 1
    assert rows[0]["chat_id"] == "101"
    assert spreadsheet.requested_tabs == ["Users"]
    assert client.opened == ["Pogoda_Users"]
    assert spreadsheet.tabs["Users"].ranges == ["A1:Z"]


def test_scheduler_loader_fails_if_users_tab_is_missing_without_form_fallback(monkeypatch):
    spreadsheet = FakeSpreadsheet({
        "Formularz": FakeWorksheet([["Chat ID", "Lat", "Lon"]]),
    })
    client = FakeClient(spreadsheet)
    monkeypatch.setattr(main_card, "SHEET_ID", "")
    monkeypatch.setattr(main_card, "SHEET_NAME", "Pogoda_Users")
    monkeypatch.setattr(main_card, "SHEET_TAB_ENV", "Formularz")

    with pytest.raises(RuntimeError, match="Users"):
        main_card._load_scheduler_users_from_sheet(client=client)
    assert spreadsheet.requested_tabs == ["Users"]


def test_scheduler_selects_active_morning_slot(monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    users = main_card._parse_scheduler_users([
        dict(zip(USERS_HEADERS, users_row("101", report_morning_time="07:30")))
    ])

    assert len(users) == 1
    assert users[0]["chat_id"] == "101"
    assert users[0]["godzina_rano"] == "07:30"
    assert users[0]["godzina_wieczor"] == ""
    assert users[0]["lat"] == 52.23
    assert users[0]["lon"] == 21.012
    assert users[0]["name"] == "Warszawa"
    assert users[0]["lang"] == "pl"
    assert users[0]["tz"] == "UTC"


def test_scheduler_selects_active_afternoon_slot(monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "Europe/Warsaw")
    users = main_card._parse_scheduler_users([
        dict(zip(USERS_HEADERS, users_row("102", report_afternoon_time="15:00")))
    ])

    assert len(users) == 1
    assert users[0]["godzina_rano"] == ""
    assert users[0]["godzina_wieczor"] == "15:00"
    assert users[0]["tz"] == "Europe/Warsaw"


@pytest.mark.parametrize("slot_value", ["", "brak", "BRAK", "25:00", "12:75", "8:00", "noon"])
def test_empty_disabled_and_invalid_report_slots_never_activate(slot_value, monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    raw = dict(zip(USERS_HEADERS, users_row("103", report_morning_time=slot_value)))
    assert main_card._parse_scheduler_users([raw]) == []


def test_invalid_slot_does_not_disable_the_other_valid_slot(monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    raw = dict(zip(USERS_HEADERS, users_row(
        "104", report_morning_time="not-a-time", report_afternoon_time="14:00"
    )))

    users = main_card._parse_scheduler_users([raw])

    assert len(users) == 1
    assert users[0]["godzina_rano"] == ""
    assert users[0]["godzina_wieczor"] == "14:00"


@pytest.mark.parametrize(
    "updates",
    [
        {"access_status": "revoked", "report_morning_time": "08:00"},
        {"access_status": "blocked", "report_morning_time": "08:00"},
        {"access_status": "", "report_morning_time": "08:00"},
        {"profile_status": "none", "report_morning_time": "08:00"},
        {"profile_status": "blocked", "report_morning_time": "08:00"},
    ],
)
def test_scheduler_skips_revoked_blocked_or_inactive_profiles(updates, monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    raw = dict(zip(USERS_HEADERS, users_row("105", report_morning_time="08:00")))
    raw.update(updates)
    assert main_card._parse_scheduler_users([raw]) == []


@pytest.mark.parametrize(
    "updates",
    [
        {"lat_round": "", "lon_round": "21.0"},
        {"lat_round": "52.0", "lon_round": ""},
        {"lat_round": "north", "lon_round": "21.0"},
        {"lat_round": "91", "lon_round": "21.0"},
        {"lat_round": "52", "lon_round": "181"},
        {"lat_round": "nan", "lon_round": "21.0"},
    ],
)
def test_scheduler_skips_missing_or_invalid_coordinates(updates, monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    raw = dict(zip(USERS_HEADERS, users_row("106", report_morning_time="08:00")))
    raw.update(updates)
    assert main_card._parse_scheduler_users([raw]) == []


def test_new_users_access_and_profile_do_not_activate_default_hours(monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    raw = dict(zip(USERS_HEADERS, users_row(
        "107", report_morning_time="", report_afternoon_time=""
    )))

    assert raw["access_status"] == "granted"
    assert raw["profile_status"] == "active"
    assert main_card._parse_scheduler_users([raw]) == []


def test_manually_migrated_user_with_explicit_hours_is_selected(monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    raw = dict(zip(USERS_HEADERS, users_row(
        "108", report_morning_time="08:00", report_afternoon_time="14:00"
    )))

    users = main_card._parse_scheduler_users([raw])

    assert len(users) == 1
    assert users[0]["godzina_rano"] == "08:00"
    assert users[0]["godzina_wieczor"] == "14:00"


def test_duplicate_users_chat_id_is_skipped_to_prevent_double_reports(monkeypatch):
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    first = dict(zip(USERS_HEADERS, users_row("109", report_morning_time="08:00")))
    second = dict(zip(USERS_HEADERS, users_row("109.0", report_afternoon_time="14:00")))

    assert main_card._parse_scheduler_users([first, second]) == []


def test_scheduler_uses_one_users_record_even_if_same_id_is_in_formularz(monkeypatch):
    migrated = users_row("110", report_morning_time="08:00")
    # Legacy record deliberately has the same ID and an additional report slot.
    form_row = ["2026-10-07", "110", "Legacy", "Hel", "54.6", "18.8", "14:00", "15:00", "pl"]
    client, spreadsheet = scheduler_client([migrated], [form_row])
    monkeypatch.setattr(main_card, "SHEET_ID", "")
    monkeypatch.setattr(main_card, "SHEET_NAME", "Pogoda_Users")
    monkeypatch.setattr(main_card, "SHEET_TAB_ENV", "Formularz")
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")

    selected = main_card._parse_scheduler_users(
        main_card._load_scheduler_users_from_sheet(client=client)
    )

    assert len(selected) == 1
    assert selected[0]["chat_id"] == "110"
    assert selected[0]["godzina_rano"] == "08:00"
    assert selected[0]["godzina_wieczor"] == ""
    assert spreadsheet.requested_tabs == ["Users"]


def test_run_send_cycle_reads_users_only_and_sends_once_for_parallel_form_record(monkeypatch):
    migrated = users_row("120", report_morning_time="08:00")
    form_row = ["2026-10-07", "120", "Legacy", "Hel", "54.6", "18.8", "08:00", "14:00", "pl"]
    client, spreadsheet = scheduler_client([migrated], [form_row])
    real_loader = main_card._load_scheduler_users_from_sheet
    monkeypatch.setattr(main_card, "SHEET_ID", "")
    monkeypatch.setattr(main_card, "SHEET_NAME", "Pogoda_Users")
    monkeypatch.setattr(main_card, "SHEET_TAB_ENV", "Formularz")
    monkeypatch.setattr(main_card, "_load_scheduler_users_from_sheet", lambda: real_loader(client=client))
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    monkeypatch.setattr(main_card, "_load_cache", lambda: {})
    monkeypatch.setattr(main_card, "_prune_cache", lambda cache, keep_days=2: cache)
    monkeypatch.setattr(main_card, "_save_cache", lambda cache: None)
    monkeypatch.setattr(main_card.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        main_card, "_in_send_window",
        lambda tz, morning, afternoon, chat_id: (True, "RANO", "2026-10-07", False),
    )
    sent = []
    monkeypatch.setattr(
        main_card, "_send_card_to_user",
        lambda user, is_quiet=False: (sent.append(user["chat_id"]) or True),
    )

    main_card.run_send_cycle()

    assert sent == ["120"]
    assert spreadsheet.requested_tabs == ["Users"]


def test_hard_deleted_user_does_not_reappear_from_formularz(monkeypatch):
    form_row = ["2026-10-07", "130", "Legacy", "Hel", "54.6", "18.8", "08:00", "14:00", "pl"]
    client, spreadsheet = scheduler_client([], [form_row])
    monkeypatch.setattr(main_card, "SHEET_ID", "")
    monkeypatch.setattr(main_card, "SHEET_NAME", "Pogoda_Users")
    monkeypatch.setattr(main_card, "SHEET_TAB_ENV", "Formularz")

    rows = main_card._load_scheduler_users_from_sheet(client=client)
    assert main_card._parse_scheduler_users(rows) == []
    assert spreadsheet.requested_tabs == ["Users"]


def test_existing_send_window_timing_and_weekday_independence(monkeypatch):
    from datetime import datetime as RealDateTime

    class FrozenDateTime(RealDateTime):
        minute = 1

        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 11, 8, cls.minute, tzinfo=tz)  # Sunday, UTC

    monkeypatch.setattr(main_card, "datetime", FrozenDateTime)

    assert main_card._in_send_window("UTC", "08:00", "", 10) == (
        True, "RANO", "2026-10-11", False
    )
    assert main_card._in_send_window("UTC", "", "08:00", 10) == (
        True, "POPOLUDNIE", "2026-10-11", False
    )

    # ID 10 ma offset 1 min; początek okna nie przesuwa się przed godzinę.
    FrozenDateTime.minute = 0
    assert main_card._in_send_window("UTC", "08:00", "", 10)[0] is False
    # Dotychczasowe okno kończy się 16 min po godzinie dla offsetu 1 min.
    FrozenDateTime.minute = 17
    assert main_card._in_send_window("UTC", "08:00", "", 10)[0] is False
