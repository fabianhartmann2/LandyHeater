"""Bounded Phase-11 event history, protocol log and capture service.

The hub owns no hardware and never calls a controller from a record path.
Existing application event queues and the UART activity queue are drained only
from :meth:`step`, outside their time-critical operations.  Every public page
is deliberately small enough for the bounded HTTP response encoder.
"""

try:
    from ubinascii import hexlify
except ImportError:  # CPython
    from binascii import hexlify


DEFAULT_EVENT_CAPACITY = 200
MAX_EVENT_CAPACITY = 200
DEFAULT_PROTOCOL_CAPACITY = 64
MAX_PROTOCOL_CAPACITY = 128
DEFAULT_CAPTURE_CAPACITY = 128
MAX_CAPTURE_CAPACITY = 256
MAX_EVENT_PAGE_SIZE = 16
MAX_PROTOCOL_PAGE_SIZE = 4
MAX_CAPTURE_PAGE_SIZE = 4
MAX_CAPTURE_LABEL_BYTES = 64
MAX_EVENT_CODE_BYTES = 64
MAX_DATA_FIELDS = 8
MAX_DATA_ITEMS = 8
MAX_DATA_DEPTH = 2
MAX_DATA_TEXT_BYTES = 96
MAX_DATA_NODES = 16
MAX_DATA_TOTAL_TEXT_BYTES = 192
MAX_SOURCE_EVENTS_PER_STEP = 32
MAX_PROTOCOL_ACTIVITIES_PER_STEP = 4
MAX_PROTOCOL_RAW_BYTES = 512

_SEVERITIES = ("info", "warning", "error")
_SENSITIVE_PARTS = ("password", "credential", "secret", "csrf", "token")
_PRIVATE_KEYS = ("message", "reason", "error", "last_error", "exception")
_FRAME_ACTIVITIES = (
    "rx_frame",
    "tx_frame",
    "tx_blocked",
    "tx_partial",
    "tx_error",
)


class DiagnosticsConflictError(RuntimeError):
    """The requested capture transition conflicts with current state."""


class DiagnosticsUnavailableError(RuntimeError):
    """A completed capture is not available for export."""


def _require_integer(name, value, minimum=0, maximum=None):
    if type(value) is not int or value < minimum:
        raise ValueError("{} must be an integer".format(name))
    if maximum is not None and value > maximum:
        raise ValueError("{} exceeds its bound".format(name))
    return value


def _bounded_text(name, value, maximum, allow_empty=False):
    if type(value) is not str or (not value and not allow_empty):
        raise ValueError("{} must be a bounded string".format(name))
    try:
        encoded = value.encode("utf-8")
    except (UnicodeError, ValueError):
        raise ValueError("{} must be valid UTF-8".format(name)) from None
    if len(encoded) > maximum:
        raise ValueError("{} exceeds its byte bound".format(name))
    for character in value:
        if ord(character) < 0x20 or ord(character) == 0x7F:
            raise ValueError("{} contains a control character".format(name))
    return value


def _sensitive_key(value):
    lowered = value.lower()
    if lowered in _PRIVATE_KEYS:
        return True
    for part in _SENSITIVE_PARTS:
        if part in lowered:
            return True
    return False


def _safe_scalar(value):
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str:
        try:
            return _bounded_text("diagnostic text", value, MAX_DATA_TEXT_BYTES, True)
        except ValueError:
            return None
    return None


def _sanitize_data(value, depth=0, budget=None):
    """Return bounded JSON data while dropping secret-shaped fields."""

    if budget is None:
        budget = [MAX_DATA_NODES, MAX_DATA_TOTAL_TEXT_BYTES]
    if depth > MAX_DATA_DEPTH or budget[0] <= 0:
        return None
    budget[0] -= 1
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str:
        scalar = _safe_scalar(value)
        if scalar is None:
            return None
        size = len(scalar.encode("utf-8"))
        if size > budget[1]:
            return None
        budget[1] -= size
        return scalar
    if type(value) is list or type(value) is tuple:
        result = []
        for item in value[:MAX_DATA_ITEMS]:
            safe = _sanitize_data(item, depth + 1, budget)
            if safe is not None:
                result.append(safe)
        return result
    if type(value) is dict:
        result = {}
        count = 0
        for key, item in value.items():
            if count >= MAX_DATA_FIELDS:
                break
            if type(key) is not str or _sensitive_key(key):
                continue
            try:
                safe_key = _bounded_text(
                    "diagnostic data key", key, MAX_EVENT_CODE_BYTES
                )
            except ValueError:
                continue
            key_size = len(safe_key.encode("utf-8"))
            if key_size > budget[1]:
                break
            budget[1] -= key_size
            safe = _sanitize_data(item, depth + 1, budget)
            if safe is None and item is not None:
                continue
            result[safe_key] = safe
            count += 1
        return result
    return None


def _clone_json(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is list:
        return [_clone_json(item) for item in value]
    if type(value) is dict:
        return {key: _clone_json(item) for key, item in value.items()}
    raise ValueError("diagnostic value is not JSON-compatible")


def _raw_hex(raw):
    value = hexlify(raw)
    if type(value) is bytes:
        return value.decode("ascii")
    return value


def _severity_for(code):
    lowered = code.lower()
    for part in ("error", "failed", "failure", "fault", "lost"):
        if part in lowered:
            return "error"
    for part in ("warning", "stale", "blocked", "rejected", "timeout"):
        if part in lowered:
            return "warning"
    return "info"


class _BoundedRing:
    __slots__ = ("capacity", "_items", "_head", "_size", "_next", "dropped")

    def __init__(self, capacity):
        self.capacity = capacity
        self._items = [None] * capacity
        self._head = 0
        self._size = 0
        self._next = 1
        self.dropped = 0

    def append(self, record):
        record["sequence"] = self._next
        self._next += 1
        if self._size == self.capacity:
            self._items[self._head] = None
            self._head = (self._head + 1) % self.capacity
            self._size -= 1
            self.dropped += 1
        index = (self._head + self._size) % self.capacity
        self._items[index] = record
        self._size += 1
        return record

    def _at(self, index):
        return self._items[(self._head + index) % self.capacity]

    def page(self, after, limit):
        _require_integer("after", after)
        _require_integer("limit", limit, 1)
        oldest = self._at(0)["sequence"] if self._size else None
        latest = self._at(self._size - 1)["sequence"] if self._size else None
        items = []
        has_more = False
        for index in range(self._size):
            record = self._at(index)
            if record["sequence"] <= after:
                continue
            if len(items) < limit:
                items.append(_clone_json(record))
            else:
                has_more = True
                break
        next_after = after if not items else items[-1]["sequence"]
        gap = oldest is not None and after + 1 < oldest
        return {
            "after": after,
            "next_after": next_after,
            "oldest_sequence": oldest,
            "latest_sequence": latest,
            "has_more": has_more,
            "gap": gap,
            "dropped": self.dropped,
            "items": items,
        }

    def clear(self):
        for index in range(self.capacity):
            self._items[index] = None
        self._head = 0
        self._size = 0

    def __len__(self):
        return self._size


class DiagnosticsHub:
    """Collect bounded event and protocol evidence without owning hardware."""

    __slots__ = (
        "_events",
        "_protocol",
        "_capture_capacity",
        "_capture",
        "_next_capture_id",
        "_event_sources",
        "_source_index",
        "_protocol_transport",
        "_protocol_parser",
        "_ticks_ms",
        "_operation_active",
        "_closed",
        "collection_errors",
        "record_errors",
        "source_events_dropped",
        "protocol_activities_ignored",
    )

    def __init__(
        self,
        event_sources=(),
        protocol_transport=None,
        protocol_parser=None,
        ticks_ms=None,
        event_capacity=DEFAULT_EVENT_CAPACITY,
        protocol_capacity=DEFAULT_PROTOCOL_CAPACITY,
        capture_capacity=DEFAULT_CAPTURE_CAPACITY,
    ):
        _require_integer("event_capacity", event_capacity, 1, MAX_EVENT_CAPACITY)
        _require_integer(
            "protocol_capacity", protocol_capacity, 1, MAX_PROTOCOL_CAPACITY
        )
        _require_integer(
            "capture_capacity", capture_capacity, 1, MAX_CAPTURE_CAPACITY
        )
        if type(event_sources) not in (tuple, list):
            raise ValueError("event_sources must be a bounded sequence")
        if len(event_sources) > 8:
            raise ValueError("event_sources exceeds its bound")
        sources = []
        for item in event_sources:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise ValueError("event source must contain category and owner")
            category, owner = item
            _bounded_text("event category", category, 32)
            drain = getattr(owner, "drain_events", None)
            if not callable(drain):
                raise ValueError("event source must provide drain_events()")
            sources.append((category, drain))
        drain_activity = None
        if protocol_transport is not None:
            drain_activity = getattr(protocol_transport, "drain_activity", None)
            if not callable(drain_activity):
                raise ValueError(
                    "protocol_transport must provide drain_activity()"
                )
        if protocol_parser is not None and not callable(protocol_parser):
            raise ValueError("protocol_parser must be callable")
        if ticks_ms is None:
            ticks_ms = lambda: 0
        if not callable(ticks_ms):
            raise ValueError("ticks_ms must be callable")

        self._events = _BoundedRing(event_capacity)
        self._protocol = _BoundedRing(protocol_capacity)
        self._capture_capacity = capture_capacity
        self._capture = None
        self._next_capture_id = 1
        self._event_sources = tuple(sources)
        self._source_index = 0
        self._protocol_transport = drain_activity
        self._protocol_parser = protocol_parser
        self._ticks_ms = ticks_ms
        self._operation_active = False
        self._closed = False
        self.collection_errors = 0
        self.record_errors = 0
        self.source_events_dropped = 0
        self.protocol_activities_ignored = 0

    @property
    def closed(self):
        return self._closed

    def _capture_item(self, stream, record):
        capture = self._capture
        if capture is None or not capture["active"]:
            return
        if len(capture["items"]) >= self._capture_capacity:
            capture["complete"] = False
            capture["items_dropped"] += 1
            return
        capture["items"].append({"stream": stream, "record": record})

    def record_event(
        self,
        category,
        code,
        now_ms,
        details=None,
        severity=None,
    ):
        """Record one sanitized event; failures never escape to its producer."""

        try:
            if self._closed:
                return False
            category = _bounded_text("event category", category, 32)
            code = _bounded_text("event code", code, MAX_EVENT_CODE_BYTES)
            _require_integer("event time", now_ms)
            if severity is None:
                severity = _severity_for(code)
            if severity not in _SEVERITIES:
                raise ValueError("event severity is invalid")
            data = _sanitize_data({} if details is None else details)
            if type(data) is not dict:
                data = {}
            record = self._events.append({
                "time_ms": now_ms,
                "severity": severity,
                "category": category,
                "code": code,
                "message": code.replace("_", " "),
                "data": data,
            })
            self._capture_item("event", record)
            return True
        except (MemoryError, Exception):
            try:
                self.record_errors += 1
            except (MemoryError, Exception):
                pass
            return False

    def record_protocol_activity(self, activity):
        """Record one immutable UART activity tuple outside the poll path."""

        try:
            if self._closed:
                return False
            if type(activity) not in (tuple, list) or len(activity) != 4:
                raise ValueError("protocol activity is malformed")
            name, now_ms, raw, details = activity
            name = _bounded_text("protocol activity", name, 32)
            _require_integer("protocol time", now_ms)
            if type(raw) not in (bytes, bytearray, memoryview):
                raise ValueError("protocol payload must be bytes-like")
            raw = bytes(raw)
            if len(raw) > MAX_PROTOCOL_RAW_BYTES:
                raise ValueError("protocol payload exceeds its bound")
            if name not in _FRAME_ACTIVITIES:
                self.protocol_activities_ignored += 1
                if name.endswith("error"):
                    self.record_event("protocol", name, now_ms, details)
                return False

            direction = "rx" if name.startswith("rx_") else "tx"
            command = raw[4] if len(raw) > 4 else None
            command_name = None
            crc_valid = None
            if raw and self._protocol_parser is not None:
                try:
                    parsed = self._protocol_parser(raw)
                    if type(parsed) is dict:
                        candidate = parsed.get("command")
                        if type(candidate) is int and 0 <= candidate <= 255:
                            command = candidate
                        candidate = parsed.get("command_name")
                        if type(candidate) is str:
                            command_name = _safe_scalar(candidate)
                        candidate = parsed.get("crc_valid")
                        if type(candidate) is bool:
                            crc_valid = candidate
                except (ValueError, TypeError):
                    pass
            record = self._protocol.append({
                "time_ms": now_ms,
                "direction": direction,
                "activity": name,
                "raw_hex": _raw_hex(raw),
                "length": len(raw),
                "command": command,
                "command_name": command_name,
                "crc_valid": crc_valid,
            })
            self._capture_item("protocol", record)
            return True
        except (MemoryError, Exception):
            try:
                self.record_errors += 1
            except (MemoryError, Exception):
                pass
            return False

    def _drain_one_event_source(self, now_ms):
        if not self._event_sources:
            return 0
        category, drain = self._event_sources[self._source_index]
        self._source_index = (self._source_index + 1) % len(self._event_sources)
        values = drain()
        if type(values) not in (tuple, list):
            raise ValueError("event source returned a malformed sequence")
        if len(values) > MAX_SOURCE_EVENTS_PER_STEP:
            self.source_events_dropped += (
                len(values) - MAX_SOURCE_EVENTS_PER_STEP
            )
            values = values[:MAX_SOURCE_EVENTS_PER_STEP]
        recorded = 0
        for value in values:
            if type(value) is not dict:
                self.record_errors += 1
                continue
            code = value.get("type", value.get("code"))
            event_time = value.get("at_ms", now_ms)
            details = value.get("details", {})
            if "profile_id" in value and type(details) is dict:
                details = dict(details)
                details["profile_id"] = value["profile_id"]
            if self.record_event(category, code, event_time, details):
                recorded += 1
        return recorded

    def _drain_protocol(self):
        if self._protocol_transport is None:
            return 0
        values = self._protocol_transport(MAX_PROTOCOL_ACTIVITIES_PER_STEP)
        if type(values) not in (tuple, list):
            raise ValueError("protocol transport returned a malformed sequence")
        if len(values) > MAX_PROTOCOL_ACTIVITIES_PER_STEP:
            raise ValueError("protocol transport exceeded the drain bound")
        recorded = 0
        for value in values:
            if self.record_protocol_activity(value):
                recorded += 1
        return recorded

    def step(self, now_ms=None):
        """Drain one event source and four UART activities at most."""

        if self._closed:
            return 0
        if self._operation_active:
            self.collection_errors += 1
            return 0
        self._operation_active = True
        count = 0
        try:
            if now_ms is None:
                now_ms = self._ticks_ms()
            _require_integer("diagnostic time", now_ms)
            try:
                count += self._drain_one_event_source(now_ms)
            except (MemoryError, Exception):
                self.collection_errors += 1
            try:
                count += self._drain_protocol()
            except (MemoryError, Exception):
                self.collection_errors += 1
        finally:
            self._operation_active = False
        return count

    @staticmethod
    def _page_query(after, limit, maximum):
        _require_integer("after", after)
        _require_integer("limit", limit, 1, maximum)

    def events_page(self, after=0, limit=MAX_EVENT_PAGE_SIZE):
        self._page_query(after, limit, MAX_EVENT_PAGE_SIZE)
        return self._events.page(after, limit)

    def protocol_page(self, after=0, limit=MAX_PROTOCOL_PAGE_SIZE):
        self._page_query(after, limit, MAX_PROTOCOL_PAGE_SIZE)
        return self._protocol.page(after, limit)

    def start_capture(self, label, now_ms, metadata=None):
        if self._closed:
            raise DiagnosticsUnavailableError("diagnostics are closed")
        if self._capture is not None and self._capture["active"]:
            raise DiagnosticsConflictError("a capture is already active")
        label = _bounded_text(
            "capture label", label, MAX_CAPTURE_LABEL_BYTES
        )
        _require_integer("capture start time", now_ms)
        safe_metadata = _sanitize_data({} if metadata is None else metadata)
        if type(safe_metadata) is not dict:
            raise ValueError("capture metadata is malformed")
        capture_id = self._next_capture_id
        self._next_capture_id += 1
        self._capture = {
            "id": capture_id,
            "label": label,
            "started_ms": now_ms,
            "ended_ms": None,
            "active": True,
            "complete": True,
            "items_dropped": 0,
            "metadata": safe_metadata,
            "items": [],
        }
        self.record_event(
            "diagnostics", "capture_started", now_ms, {"capture_id": capture_id}
        )
        return self.capture_status()

    def stop_capture(self, now_ms):
        if self._capture is None or not self._capture["active"]:
            raise DiagnosticsConflictError("no capture is active")
        _require_integer("capture end time", now_ms)
        capture_id = self._capture["id"]
        self.record_event(
            "diagnostics", "capture_stopped", now_ms, {"capture_id": capture_id}
        )
        self._capture["active"] = False
        self._capture["ended_ms"] = now_ms
        return self.capture_status()

    def capture_status(self):
        capture = self._capture
        if capture is None:
            return {
                "available": False,
                "active": False,
                "id": None,
                "label": None,
                "started_ms": None,
                "ended_ms": None,
                "complete": None,
                "items_total": 0,
                "items_dropped": 0,
            }
        return {
            "available": not capture["active"],
            "active": capture["active"],
            "id": capture["id"],
            "label": capture["label"],
            "started_ms": capture["started_ms"],
            "ended_ms": capture["ended_ms"],
            "complete": capture["complete"],
            "items_total": len(capture["items"]),
            "items_dropped": capture["items_dropped"],
        }

    def capture_page(self, offset=0, limit=MAX_CAPTURE_PAGE_SIZE):
        _require_integer("offset", offset)
        _require_integer("limit", limit, 1, MAX_CAPTURE_PAGE_SIZE)
        capture = self._capture
        if capture is None or capture["active"]:
            raise DiagnosticsUnavailableError(
                "a completed capture is not available"
            )
        total = len(capture["items"])
        items = capture["items"][offset:offset + limit]
        return {
            "schema": "landy-heater.protocol-capture",
            "version": 1,
            "id": capture["id"],
            "label": capture["label"],
            "started_ms": capture["started_ms"],
            "ended_ms": capture["ended_ms"],
            "complete": capture["complete"],
            "items_dropped": capture["items_dropped"],
            "metadata": _clone_json(capture["metadata"]),
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + len(items) < total,
            "items": [_clone_json(item) for item in items],
        }

    def snapshot(self):
        return {
            "closed": self._closed,
            "operation_active": self._operation_active,
            "event_count": len(self._events),
            "event_capacity": self._events.capacity,
            "events_dropped": self._events.dropped,
            "protocol_count": len(self._protocol),
            "protocol_capacity": self._protocol.capacity,
            "protocol_dropped": self._protocol.dropped,
            "capture_capacity": self._capture_capacity,
            "capture": self.capture_status(),
            "collection_errors": self.collection_errors,
            "record_errors": self.record_errors,
            "source_events_dropped": self.source_events_dropped,
            "protocol_activities_ignored": self.protocol_activities_ignored,
        }

    def deinit(self):
        self._closed = True
        self._events.clear()
        self._protocol.clear()
        if self._capture is not None:
            self._capture["items"] = []
            self._capture["metadata"] = {}
        self._capture = None


__all__ = (
    "DiagnosticsConflictError",
    "DiagnosticsUnavailableError",
    "DiagnosticsHub",
    "MAX_EVENT_PAGE_SIZE",
    "MAX_PROTOCOL_PAGE_SIZE",
    "MAX_CAPTURE_PAGE_SIZE",
)
