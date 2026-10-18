"""Temporary opt-in timings. No arguments, results, exceptions or URLs are logged.

PERF_DEBUG=1 is sampled on entry; disabled scopes never read the clock or UUID.
Existing application logs are deliberately untouched. This module's records are
prefixed PERF_DEBUG and contain only allowlisted labels plus random identifiers.
"""
import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

# Never derive these labels from a command, URL, exception or other input.
_OPERATIONS = {
    "poll": "poll", "smoke": "smoke", "card": "card",
    "update": "update", "dispatcher": "dispatcher",
    "snapshot_users": "snapshot_users", "snapshot_bot_state": "snapshot_bot_state",
    "waiting_message": "waiting_message", "geocode.forward": "geocode",
    "geocode.reverse": "geocode", "geocode.guest": "geocode",
    "geocode.city_cache": "geocode",
    "payload": "payload", "layout": "layout", "render": "render",
    "send_photo": "send_photo", "send_photo.http": "send_photo_http",
    "waiting_message.http": "waiting_message_http",
    "weather.openmeteo": "weather_source", "weather.yrno": "weather_source",
    "weather.airly": "weather_source", "weather.owm": "weather_source",
    "weather.openmeteo.http": "weather_http", "weather.yrno.http": "weather_http",
    "weather.airly.http": "weather_http", "weather.owm.http": "weather_http",
}
_OUTCOMES = frozenset(("started", "ok", "error", "skipped", "unavailable", "unknown"))
_CACHES = frozenset(("hit", "miss", "not_applicable", "unknown"))
_requests = ContextVar("perf_requests", default=())
_spans = ContextVar("perf_spans", default=())
_poll_id = ContextVar("perf_poll_id", default=None)


def enabled():
    return os.environ.get("PERF_DEBUG") == "1"


def _new_request(started=None):
    return (uuid.uuid4().hex, time.monotonic_ns() if started is None else started)


def _emit(requests, operation, stage, started, outcome, cache):
    # Observability must not change the result/exception of the observed call.
    try:
        if operation not in _OPERATIONS or outcome not in _OUTCOMES or cache not in _CACHES:
            return
        now = time.monotonic_ns()
        for request_id, _ in requests:
            record = dict(request_id=request_id, operation=operation, stage=stage,
                          elapsed_ms=round((now - started) / 1_000_000, 3),
                          outcome=outcome, cache=cache)
            if _poll_id.get() is not None:
                record["poll_request_id"] = _poll_id.get()
            if operation in ("snapshot_users", "snapshot_bot_state"):
                record[operation + "_cache"] = cache
                record[operation + "_outcome"] = outcome
            print("PERF_DEBUG " + json.dumps(record, separators=(",", ":")))
    except Exception:
        pass


@contextmanager
def bind(requests):
    """Share a single real snapshot read across the received update contexts."""
    if not requests:
        yield
        return
    token = _requests.set(requests)
    spans_token = _spans.set(())
    try:
        yield
    finally:
        _spans.reset(spans_token)
        _requests.reset(token)


def received(updates):
    if not enabled():
        return None
    started = time.monotonic_ns()
    contexts = [(_new_request(started),) for _ in updates]
    for context in contexts:
        _emit(context, "update", "update_received", started, "ok", "not_applicable")
    return contexts


def shared(contexts):
    return tuple(request for context in (contexts or ()) for request in context)


def selected(contexts, index):
    return contexts[index] if contexts else None


def dispatcher_started():
    for request in _requests.get():
        _emit((request,), "dispatcher", "dispatcher_started", request[1], "started", "not_applicable")


def mark(outcome=None, cache=None, operation=None):
    """Annotate the existing branch; never probe caches a second time."""
    for state in reversed(_spans.get()):
        if operation is None or state["operation"] == operation:
            if outcome in _OUTCOMES:
                state["outcome"] = outcome
            if cache in _CACHES:
                state["cache"] = cache
            return


@contextmanager
def span(operation):
    requests = _requests.get()
    if not requests or operation not in _OPERATIONS:
        yield
        return
    started = time.monotonic_ns()
    state = dict(operation=operation, outcome="ok", cache="not_applicable")
    if operation.startswith("snapshot_") or operation in ("geocode.reverse", "geocode.guest", "weather.openmeteo", "weather.owm"):
        state["cache"] = "unknown"
    token = _spans.set(_spans.get() + (state,))
    prefix = _OPERATIONS[operation]
    _emit(requests, operation, prefix + "_before", started, "started", state["cache"])
    try:
        yield
    except BaseException:
        state["outcome"] = "error"
        raise
    finally:
        _emit(requests, operation, prefix + "_after", started, state["outcome"], state["cache"])
        _spans.reset(token)


def call(operation, function, *args, **kwargs):
    if not _requests.get():
        return function(*args, **kwargs)
    with span(operation):
        result = function(*args, **kwargs)
        try:
            _observe(operation, result)
        except Exception:
            # Result inspection is telemetry only (e.g. a custom Response mock).
            pass
        return result


def traced(operation):
    """Function boundary inside an existing request (preserves function metadata)."""
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            return call(operation, function, *args, **kwargs)
        return wrapper
    return decorate


def entry(operation):
    """Create a context for poll/smoke/card, unless the caller supplied one."""
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            if _requests.get():
                return call(operation, function, *args, **kwargs)
            context = (_new_request(),)
            poll_token = _poll_id.set(context[0][0]) if operation == "poll" else None
            try:
                with bind(context):
                    return call(operation, function, *args, **kwargs)
            finally:
                if poll_token is not None:
                    _poll_id.reset(poll_token)
        return wrapper
    return decorate


def waiting_http(function, *args, **kwargs):
    # send_reply also serves unrelated messages; don't mislabel them as waiting.
    if any(state["operation"] == "waiting_message" for state in _spans.get()):
        return call("waiting_message.http", function, *args, **kwargs)
    return function(*args, **kwargs)


def skipped(operation):
    if _requests.get():
        with span(operation):
            mark(outcome="skipped", cache="not_applicable")


@contextmanager
def _dispatch(context):
    with bind(context):
        dispatcher_started()
        with span("dispatcher"):
            yield


def start_dispatch(context):
    if not context:
        return None
    manager = _dispatch(context)
    manager.__enter__()
    return manager


def start_span(operation):
    if not _requests.get():
        return None
    manager = span(operation)
    manager.__enter__()
    return manager


def finish(manager):
    if manager is not None:
        import sys
        manager.__exit__(*sys.exc_info())


def _observe(operation, result):
    if operation.startswith("weather.") and not operation.endswith(".http"):
        if (result is None or (type(result) is list and not result) or
                (operation == "weather.yrno" and type(result) is tuple and
                 result and type(result[0]) is list and not result[0])):
            # Keep an explicit skipped/error outcome set inside the source.
            state = _spans.get()[-1]
            if state["outcome"] == "ok":
                mark(outcome="unavailable")
    elif operation == "render" and not result:
        mark(outcome="error")
    elif operation in ("send_photo", "card") and result is False:
        mark(outcome="error")
    if operation == "geocode.city_cache":
        mark(cache="hit" if result else "miss")
    elif operation == "geocode.forward" and isinstance(result, tuple) and result[0] == "geocode_error":
        mark(outcome="error")
    elif operation == "geocode.reverse" and isinstance(result, tuple) and result[-1] == "error":
        mark(outcome="error")
    if operation.endswith(".http"):
        try:
            failed = result.status_code >= 400
        except Exception:
            failed = False
        if failed:
            mark(outcome="error")
            if operation == "send_photo.http":
                mark(outcome="error", operation="send_photo")
            elif operation == "waiting_message.http":
                mark(outcome="error", operation="waiting_message")


def uncached_snapshot(function, *args, **kwargs):
    if not _requests.get():
        return function(*args, **kwargs)
    with span("snapshot_users"):
        mark(cache="miss")
        return function(*args, **kwargs)
