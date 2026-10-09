"""Anti-429 polling, local offset, and Sheets cache regression tests."""

import json
from types import SimpleNamespace

import pytest

import db_cleanup
import location_bot as lb
import users_store


class FakeGoogle429(Exception):
    def __init__(self, message="APIError: [429]: Quota exceeded"):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=429)


class FakeCell:
    def __init__(self, value):
        self.value = value


class FakeBotState:
    def __init__(self, offset="50", error=None):
        self.offset = offset
        self.error = error
        self.reads = 0
        self.writes = 0

    def acell(self, _address):
        self.reads += 1
        if self.error:
            raise self.error
        return FakeCell(self.offset)

    def update_acell(self, _address, value):
        self.writes += 1
        self.offset = str(value)


class FakeForm:
    def __init__(self, headers=None, rows=None, error=None):
        self.headers = list(headers or ["Chat ID", "Imię", "Lat", "Lon", "Miasto"])
        self.rows = [list(row) for row in (rows or [])]
        self.error = error
        self.reads = 0
        self.writes = 0

    def get_all_values(self, value_render_option=None):
        self.reads += 1
        if self.error:
            raise self.error
        return [self.headers[:]] + [row[:] for row in self.rows]

    def row_values(self, row):
        return self.headers[:] if row == 1 else self.rows[row - 2][:]

    def col_values(self, column):
        return [self.headers[column - 1]] + [row[column - 1] for row in self.rows]

    def find(self, value, in_column=None):
        for index, row in enumerate(self.rows, start=2):
            if str(row[in_column - 1]).strip() == str(value):
                return SimpleNamespace(row=index)
        raise LookupError(value)

    def update_cell(self, row, column, value):
        self.writes += 1
        while len(self.rows) < row - 1:
            self.rows.append([""] * len(self.headers))
        while len(self.rows[row - 2]) < column:
            self.rows[row - 2].append("")
        self.rows[row - 2][column - 1] = value

    def update_cells(self, cells):
        for cell in cells:
            self.update_cell(cell.row, cell.col, cell.value)

    def delete_rows(self, row):
        del self.rows[row - 2]


class FakeUsers:
    def __init__(self, error=None):
        self.error = error
        self.reads = 0
        self.grid = [list(users_store.CANONICAL_HEADERS)]

    def get_all_values(self):
        self.reads += 1
        if self.error:
            raise self.error
        return [row[:] for row in self.grid]

    def row_values(self, row):
        return self.grid[row - 1][:]

    def col_values(self, column):
        return [row[column - 1] for row in self.grid]

    def update_cells(self, cells):
        for cell in cells:
            while len(self.grid) < cell.row:
                self.grid.append([""] * len(users_store.CANONICAL_HEADERS))
            self.grid[cell.row - 1][cell.col - 1] = cell.value

    def append_row(self, values, **_kwargs):
        self.grid.append(list(values))

    def delete_rows(self, row):
        del self.grid[row - 1]


class FakeSpreadsheet:
    def __init__(self, tabs):
        self.tabs = tabs
        self.worksheet_calls = []

    def worksheet(self, name):
        self.worksheet_calls.append(name)
        return self.tabs[name]


class FakeGoogleClient:
    def __init__(self, tabs):
        self.spreadsheet = FakeSpreadsheet(tabs)
        self.open_calls = []
        self.open_by_key_calls = []
        self.open_by_key_error = None

    def open(self, title):
        self.open_calls.append(title)
        return self.spreadsheet

    def open_by_key(self, key):
        self.open_by_key_calls.append(key)
        if self.open_by_key_error:
            raise self.open_by_key_error
        return self.spreadsheet


def _set_offset_file(monkeypatch, tmp_path, offset=None):
    path = tmp_path / "bot-offset.json"
    monkeypatch.setenv("BOT_OFFSET_FILE", str(path))
    if offset is not None:
        path.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    return path


def _configure_poll(monkeypatch, client, offset_path, responses, sleeps=None):
    lb._reset_polling_state(clear_client=True)
    monkeypatch.setattr(lb, "get_google_client", lambda: client)
    updates_seen = []
    response_queue = list(responses)

    def get_updates(_url, params=None, **_kwargs):
        updates_seen.append(dict(params or {}))
        if response_queue:
            return SimpleNamespace(json=lambda: response_queue.pop(0))
        return SimpleNamespace(json=lambda: {"ok": True, "result": []})

    monkeypatch.setattr(lb.requests, "get", get_updates)
    if sleeps is not None:
        monkeypatch.setattr(lb.time, "sleep", lambda seconds: sleeps.append(seconds))
    return updates_seen


def _client(form=None, users=None, state=None):
    form = form or FakeForm()
    users = users or FakeUsers()
    state = state or FakeBotState()
    client = FakeGoogleClient({"Formularz": form, "Users": users, "Bot_State": state})
    return client, form, users, state


def _empty_message_update(update_id):
    # Has a Telegram update ID but no chat; it exercises batch reads without
    # sending messages or invoking product/forecast paths.
    return {"update_id": update_id, "message": {}}


def test_idle_polls_use_local_offset_and_make_zero_sheets_reads(monkeypatch, tmp_path):
    offset_path = _set_offset_file(monkeypatch, tmp_path, offset=912)
    client, form, users, state = _client()
    polls = _configure_poll(
        monkeypatch,
        client,
        offset_path,
        [
            {"ok": True, "result": []},
            {"ok": True, "result": []},
        ],
    )

    assert lb.main_bot() is False
    assert lb.main_bot() is False

    assert [params["offset"] for params in polls] == [912, 912]
    assert client.open_calls == []
    assert form.reads == users.reads == state.reads == 0
    assert state.writes == 0


def test_missing_offset_429_backs_off_without_telegram_offset_zero(monkeypatch, tmp_path):
    offset_path = _set_offset_file(monkeypatch, tmp_path)
    state = FakeBotState(error=FakeGoogle429())
    client, form, users, _ = _client(state=state)
    sleeps = []
    polls = _configure_poll(monkeypatch, client, offset_path, [], sleeps=sleeps)

    # Repeated failures exercise the capped 30 -> 60 -> 120 -> 120 progression.
    assert [lb.main_bot() for _ in range(4)] == [True, True, True, True]

    assert sleeps == [30, 60, 120, 120]
    assert polls == []
    assert state.reads == 4
    assert form.reads == users.reads == 0
    assert not offset_path.exists()


def test_formularz_429_stops_before_users_and_does_not_advance_offset(monkeypatch, tmp_path):
    offset_path = _set_offset_file(monkeypatch, tmp_path, offset=40)
    client, form, users, _state = _client(form=FakeForm(error=FakeGoogle429()))
    sleeps = []
    polls = _configure_poll(
        monkeypatch,
        client,
        offset_path,
        [{"ok": True, "result": [_empty_message_update(41)]}],
        sleeps=sleeps,
    )

    assert lb.main_bot() is True

    assert [params["offset"] for params in polls] == [40]
    assert sleeps == [30]
    assert form.reads == 1
    assert users.reads == 0
    assert json.loads(offset_path.read_text(encoding="utf-8"))["offset"] == 40


def test_users_429_stops_batch_and_reuses_successful_form_snapshot(monkeypatch, tmp_path):
    offset_path = _set_offset_file(monkeypatch, tmp_path, offset=70)
    client, form, users, _state = _client(users=FakeUsers(error=FakeGoogle429()))
    sleeps = []
    poll_update = {"ok": True, "result": [_empty_message_update(71)]}
    polls = _configure_poll(
        monkeypatch,
        client,
        offset_path,
        [poll_update, poll_update],
        sleeps=sleeps,
    )

    assert lb.main_bot() is True
    assert lb.main_bot() is True

    assert [params["offset"] for params in polls] == [70, 70]
    assert sleeps == [30, 60]
    assert form.reads == 1  # successful tab snapshot stays warm over the retry
    assert users.reads == 2
    assert json.loads(offset_path.read_text(encoding="utf-8"))["offset"] == 70


def test_formularz_and_users_ttl_refresh_only_after_expiry(monkeypatch, tmp_path):
    offset_path = _set_offset_file(monkeypatch, tmp_path, offset=100)
    client, form, users, _state = _client()
    polls = _configure_poll(
        monkeypatch,
        client,
        offset_path,
        [
            {"ok": True, "result": [_empty_message_update(101)]},
            {"ok": True, "result": [_empty_message_update(102)]},
            {"ok": True, "result": [_empty_message_update(103)]},
        ],
    )
    clock = [100.0]
    monkeypatch.setattr(lb.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(lb, "SHEETS_CACHE_TTL_SECONDS", 30.0)

    assert lb.main_bot() is False
    clock[0] = 120.0
    assert lb.main_bot() is False
    assert form.reads == users.reads == 1

    clock[0] = 131.0
    assert lb.main_bot() is False
    assert form.reads == users.reads == 2
    assert [params["offset"] for params in polls] == [100, 102, 103]


def test_form_snapshot_is_reloaded_after_legacy_location_write(monkeypatch):
    headers = ["Sygnatura czasowa", "Chat ID", "Imię", "Miasto", "Lat", "Lon"]
    form = FakeForm(
        headers=headers,
        rows=[["2026-01-01", "44", "Ala", "Gdańsk", "54.3", "18.6"]],
    )
    client, _form, _users, _state = _client(form=form)
    lb._reset_polling_state(clear_client=True)

    first = lb._load_form_snapshot(client)
    assert first["users_records"][0]["Miasto"] == "Gdańsk"
    assert form.reads == 1

    assert lb._clear_legacy_location(form, "44") is True
    second = lb._load_form_snapshot(client)

    assert form.reads == 2
    assert second["users_records"][0]["Miasto"] == ""
    assert second["users_records"][0]["Lat"] == ""
    assert second["users_records"][0]["Lon"] == ""


def test_users_snapshot_stays_current_after_profile_write(monkeypatch):
    client, _form, users, _state = _client()
    row = [""] * len(users_store.CANONICAL_HEADERS)
    row[0] = "44"
    row[3] = users_store.ACCESS_GRANTED
    row[10] = "none"
    users.grid.append(row)
    lb._reset_polling_state(clear_client=True)
    monkeypatch.setattr(lb, "send_reply", lambda *args, **kwargs: None)

    snapshot = lb._load_users_snapshot(client)
    assert users.reads == 1

    assert lb._save_profile_from_location(
        "44", users, snapshot["users_map"], 54.352, 18.646, "Gdańsk", "pl", "city"
    ) is True

    fresh = lb._load_users_snapshot(client)
    profile = fresh["users_map"]["44"]
    assert users.reads == 2  # one initial snapshot + one writer verification read
    assert profile["lat_round"] == "54.352"
    assert profile["lon_round"] == "18.646"


def test_future_loader_propagates_open_429_without_fallback(monkeypatch):
    client, _form, _users, _state = _client()
    client.open_by_key_error = FakeGoogle429()
    monkeypatch.setattr(lb.main_card, "SHEET_ID", "spreadsheet-id")
    monkeypatch.setattr(lb.main_card, "SHEET_NAME", "Pogoda_Users")
    monkeypatch.setattr(lb.main_card, "SHEET_TAB_ENV", "")

    with pytest.raises(lb.SheetsRateLimitError):
        lb._load_future_users_from_sheet(client)

    assert client.open_by_key_calls == ["spreadsheet-id"]
    assert client.open_calls == []  # no second Sheets request after the 429


def test_blocked_user_cleanup_propagates_429_before_legacy_sheet_read():
    client, form, users, _state = _client(users=FakeUsers(error=FakeGoogle429()))

    with pytest.raises(FakeGoogle429):
        db_cleanup.mark_user_as_blocked(client, "44", reason="unknown")

    assert client.open_calls == ["Pogoda_Users"]
    assert client.spreadsheet.worksheet_calls == ["Users"]
    assert form.reads == 0  # do not continue to Formularz after Users returned 429


def test_send_reply_wraps_blocked_cleanup_429_for_poll_backoff(monkeypatch):
    client, _form, _users, _state = _client()
    response = SimpleNamespace(
        status_code=403,
        json=lambda: {"ok": False, "description": "bot was blocked by the user"},
    )
    monkeypatch.setattr(lb.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(lb, "get_google_client", lambda: client)
    monkeypatch.setattr(
        db_cleanup,
        "mark_user_as_blocked",
        lambda *args, **kwargs: (_ for _ in ()).throw(FakeGoogle429()),
    )

    with pytest.raises(lb.SheetsRateLimitError) as caught:
        lb.send_reply("44", "message")

    assert caught.value.operation == "sheets.blocked_user_cleanup"


def test_expected_long_poll_is_sampled_not_logged_as_slow(monkeypatch, capsys):
    monkeypatch.setattr(lb, "PERF_SLOW_MS", 0.0)
    monkeypatch.setattr(lb, "PERF_EVERY_N", 0)

    lb._timed_call("telegram.getUpdates", lambda: None, _perf_slow=False)

    assert capsys.readouterr().out == ""


def test_perf_logs_timing_without_nominatim_input_or_output(monkeypatch, capsys):
    monkeypatch.setattr(lb, "PERF_SLOW_MS", 0.0)
    marker = "private query 54.352,18.646"

    assert lb._timed_call("nominatim.forward", lambda value: value, marker) == marker
    output = capsys.readouterr().out

    assert "PERF stage=nominatim.forward" in output
    assert marker not in output


def test_users_store_does_not_swallow_429_as_empty_map():
    users = FakeUsers(error=FakeGoogle429())

    with pytest.raises(FakeGoogle429):
        users_store.load_users_map(users)


def test_rate_limit_detection_accepts_http_429_without_text_match():
    error = FakeGoogle429("temporary API failure")

    assert users_store._is_rate_limit_error(error) is True


def test_rate_limit_detection_accepts_403_google_rate_limit_reason():
    response = SimpleNamespace(
        status_code=403,
        json=lambda: {"error": {"errors": [{"reason": "rateLimitExceeded"}]}},
    )
    error = Exception("Google API error")
    error.response = response

    assert users_store._is_rate_limit_error(error) is True


def test_rate_limit_detection_rejects_unrelated_403_reason():
    response = SimpleNamespace(
        status_code=403,
        json=lambda: {"error": {"errors": [{"reason": "forbidden"}]}},
    )
    error = Exception("Google API error")
    error.response = response

    assert users_store._is_rate_limit_error(error) is False


def test_rate_limit_detection_uses_semantic_text_fallback():
    assert users_store._is_rate_limit_error(Exception("The quota exceeded the limit")) is True


def test_rate_limit_detection_ignores_unrelated_429_text():
    assert users_store._is_rate_limit_error(Exception("received 429 items from the endpoint")) is False


def test_rate_limit_reraise_keeps_the_original_request_frame():
    def sheets_request():
        raise FakeGoogle429("temporary API failure")

    try:
        sheets_request()
    except FakeGoogle429 as error:
        with pytest.raises(FakeGoogle429) as caught:
            users_store._reraise_rate_limit(error)
    else:
        pytest.fail("expected the request to raise")

    traceback_names = []
    traceback = caught.value.__traceback__
    while traceback is not None:
        traceback_names.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next

    assert "sheets_request" in traceback_names
