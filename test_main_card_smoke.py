"""Offline tests for the ``main_card.py --smoke`` Users-store lookup.

Smoke must resolve users exactly like the production bot: spreadsheet
``Pogoda_Users``, worksheet ``Users``, via ``users_store`` (normalized
chat_id, effective access, active saved profile) — without requiring
scheduled report times. No Google/Telegram traffic: the Sheets client,
timezone resolver and card sender are fakes/monkeypatched.
"""

import pytest

import main_card
import users_store


HEADERS = users_store.CANONICAL_HEADERS

WIAZOWNA_ID = "8396832820"
Lagos_GROUP_ID = "-1004378536170"
Algier_GROUP_ID = "-1004411426759"
EXTRA_GROUP_ID = "-1004408925383"


def users_row(chat_id, **overrides):
    values = {
        "chat_id": str(chat_id),
        "access_status": "granted",
        "profile_status": "active",
        "lat_round": "52.160",
        "lon_round": "21.300",
        "location_label": "Wiązowna",
        "lang": "pl",
        # Deliberately empty: smoke must not require scheduled report times.
        "report_morning_time": "",
        "report_afternoon_time": "",
    }
    values.update(overrides)
    return [values.get(header, "") for header in HEADERS]


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = [list(row) for row in rows]
        self.reads = 0

    def get_all_values(self):
        self.reads += 1
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
    """Records openings; ``open_by_key`` must never be used by smoke."""

    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self.opened = []

    def open(self, title):
        self.opened.append(title)
        return self.spreadsheet

    def open_by_key(self, key):  # pragma: no cover - guard
        raise AssertionError("smoke must open Pogoda_Users by title via users_store")


def smoke_client(rows):
    spreadsheet = FakeSpreadsheet({
        "Users": FakeWorksheet([HEADERS] + list(rows)),
        # Legacy sheet exists in the workbook, but must never be requested.
        "Formularz": FakeWorksheet([["Chat ID", "Lat", "Lon"]]),
    })
    return FakeClient(spreadsheet), spreadsheet


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")
    monkeypatch.setattr(
        main_card, "_send_card_to_user",
        lambda user, is_now=False, is_future=False: calls.append(
            {"user": user, "is_now": is_now, "is_future": is_future}) or True,
    )
    return calls


def run_smoke(monkeypatch, rows, target):
    client, spreadsheet = smoke_client(rows)
    monkeypatch.setattr(main_card, "SHEET_ID", "must-not-be-used-by-smoke")
    result = main_card.run_smoke_test(target, client=client)
    return result, client, spreadsheet


def base_rows():
    return [
        users_row(WIAZOWNA_ID),
        users_row(Lagos_GROUP_ID, lat_round="6.524", lon_round="3.379",
                  location_label="Lagos"),
        users_row(Algier_GROUP_ID, lat_round="36.737", lon_round="3.086",
                  location_label="Algier"),
        users_row(EXTRA_GROUP_ID, lat_round="52.230", lon_round="21.012",
                  location_label="Warsaw Group"),
    ]


@pytest.mark.parametrize("target,label", [
    (WIAZOWNA_ID, "Wiązowna"),
    (Lagos_GROUP_ID, "Lagos"),
    (Algier_GROUP_ID, "Algier"),
    (EXTRA_GROUP_ID, "Warsaw Group"),
])
def test_smoke_finds_private_and_group_users_without_report_times(
        monkeypatch, sent, target, label):
    result, client, spreadsheet = run_smoke(monkeypatch, base_rows(), target)

    assert result is True
    assert client.opened == ["Pogoda_Users"]
    assert spreadsheet.requested_tabs == ["Users"]
    assert len(sent) == 1
    user = sent[0]["user"]
    assert user["chat_id"] == target
    assert user["name"] == label
    assert user["godzina_rano"] == "" and user["godzina_wieczor"] == ""


def test_smoke_accepts_chat_id_with_sheets_float_suffix(monkeypatch, sent):
    rows = [users_row("8396832820.0")]
    result, _, _ = run_smoke(monkeypatch, rows, WIAZOWNA_ID)

    assert result is True
    assert sent[0]["user"]["chat_id"] == WIAZOWNA_ID


def test_smoke_rejects_inactive_user(monkeypatch, sent, capsys):
    rows = [
        users_row("111", access_status="revoked"),
        users_row("222", profile_status="inactive"),
        users_row("333", access_status="blocked", blocked_at="2026-11-01 00:00:00"),
    ]
    for target in ("111", "222", "333"):
        result, _, _ = run_smoke(monkeypatch, rows, target)
        assert result is False
    assert sent == []
    assert "nieaktywny" in capsys.readouterr().out


def test_smoke_rejects_user_without_location(monkeypatch, sent, capsys):
    rows = [
        users_row("111", lat_round="", lon_round=""),
        users_row("222", lat_round="abc", lon_round="21.0"),
    ]
    for target in ("111", "222"):
        result, _, _ = run_smoke(monkeypatch, rows, target)
        assert result is False
    assert sent == []
    assert "nie ma zapisanej lokalizacji" in capsys.readouterr().out


def test_smoke_missing_user_is_clear_and_sends_nothing(monkeypatch, sent, capsys):
    result, client, spreadsheet = run_smoke(monkeypatch, base_rows(), "424242")

    assert result is False
    assert sent == []
    out = capsys.readouterr().out
    assert "Nie znaleziono użytkownika 424242" in out
    assert "Pogoda_Users (zakładka Users)" in out
    assert client.opened == ["Pogoda_Users"]
    assert spreadsheet.requested_tabs == ["Users"]


def test_smoke_keeps_card_type_selection(monkeypatch, sent):
    client, _ = smoke_client(base_rows())
    monkeypatch.setattr(main_card, "_resolve_tz", lambda lat, lon, raw_tz="": "UTC")

    assert main_card.run_smoke_test(WIAZOWNA_ID, is_now=True, client=client) is True
    assert sent[-1]["is_now"] is True and sent[-1]["is_future"] is False
    assert main_card.run_smoke_test(WIAZOWNA_ID, is_future=True, client=client) is True
    assert sent[-1]["is_future"] is True and sent[-1]["is_now"] is False
