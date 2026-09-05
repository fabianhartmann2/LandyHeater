import unittest

from protocol.autoterm_protocol import build_status_request, parse_frame
from services.diagnostics_hub import (
    DiagnosticsConflictError,
    DiagnosticsHub,
    DiagnosticsUnavailableError,
)


class EventSource:
    def __init__(self, values=None):
        self.values = [] if values is None else list(values)
        self.calls = 0

    def drain_events(self):
        self.calls += 1
        values = self.values
        self.values = []
        return values


class ProtocolTransport:
    def __init__(self, values=None):
        self.values = [] if values is None else list(values)
        self.limits = []

    def drain_activity(self, limit):
        self.limits.append(limit)
        values = self.values[:limit]
        self.values = self.values[limit:]
        return values


class TestDiagnosticsHub(unittest.TestCase):
    def test_event_history_is_bounded_paged_detached_and_redacted(self):
        hub = DiagnosticsHub(event_capacity=3)
        details = {
            "mode": "power",
            "password": "TOP-SECRET",
            "csrf_token": "TOKEN",
            "reason": "PRIVATE-DRIVER-REASON",
        }
        for index in range(4):
            self.assertTrue(hub.record_event(
                "heater", "session_started", index, details
            ))
        details["mode"] = "changed"

        first = hub.events_page(0, 2)
        self.assertTrue(first["gap"])
        self.assertTrue(first["has_more"])
        self.assertEqual(first["dropped"], 1)
        self.assertEqual(
            [item["sequence"] for item in first["items"]], [2, 3]
        )
        self.assertNotIn("TOP-SECRET", repr(first))
        self.assertNotIn("TOKEN", repr(first))
        self.assertNotIn("PRIVATE-DRIVER-REASON", repr(first))
        self.assertEqual(first["items"][0]["data"]["mode"], "power")
        first["items"][0]["data"]["mode"] = "caller"
        self.assertEqual(
            hub.events_page(1, 1)["items"][0]["data"]["mode"], "power"
        )

    def test_step_rotates_sources_and_bounds_protocol_drain(self):
        first = EventSource([{"code": "configuration_changed"}])
        second = EventSource([{
            "type": "sensor_health_changed",
            "at_ms": 12,
            "details": {"role": "cabin"},
        }])
        raw = build_status_request()
        transport = ProtocolTransport([
            ("rx_chunk", 10, raw, None),
            ("rx_frame", 11, raw, None),
            ("tx_blocked", 12, raw, {"reason": "board_safety_policy"}),
            ("rx_error", 13, b"", {"message": "private-driver-text"}),
            ("rx_frame", 14, raw, None),
        ])
        hub = DiagnosticsHub(
            (("configuration", first), ("sensor", second)),
            transport,
            parse_frame,
            ticks_ms=lambda: 20,
        )

        self.assertEqual(hub.step(), 3)
        self.assertEqual((first.calls, second.calls), (1, 0))
        self.assertEqual(transport.limits, [4])
        protocol = hub.protocol_page(0, 4)["items"]
        self.assertEqual(len(protocol), 2)
        self.assertEqual(protocol[0]["direction"], "rx")
        self.assertEqual(protocol[0]["command"], raw[4])
        self.assertTrue(protocol[0]["crc_valid"])
        self.assertEqual(protocol[1]["activity"], "tx_blocked")
        self.assertEqual(hub.snapshot()["protocol_activities_ignored"], 2)

        hub.step(21)
        self.assertEqual((first.calls, second.calls), (1, 1))
        events = hub.events_page(0, 16)["items"]
        self.assertEqual(
            [item["category"] for item in events],
            ["configuration", "protocol", "sensor"],
        )
        self.assertNotIn("private-driver-text", repr(events))

    def test_named_capture_has_bounded_export_and_explicit_state(self):
        hub = DiagnosticsHub(capture_capacity=3)
        started = hub.start_capture(
            "Cold start", 100, {"generation": 7, "password": "SECRET"}
        )
        self.assertTrue(started["active"])
        with self.assertRaises(DiagnosticsConflictError):
            hub.start_capture("Second", 101)
        hub.record_event("heater", "heater_started", 102)
        hub.record_protocol_activity(("rx_frame", 103, b"12345", None))
        stopped = hub.stop_capture(104)
        self.assertFalse(stopped["active"])
        self.assertTrue(stopped["available"])
        self.assertFalse(stopped["complete"])
        self.assertEqual(stopped["items_total"], 3)
        self.assertEqual(stopped["items_dropped"], 1)

        page = hub.capture_page(0, 2)
        self.assertEqual(page["schema"], "landy-heater.protocol-capture")
        self.assertEqual(page["label"], "Cold start")
        self.assertTrue(page["has_more"])
        self.assertNotIn("SECRET", repr(page))
        final = hub.capture_page(2, 2)
        self.assertFalse(final["has_more"])

    def test_capture_export_requires_a_completed_capture(self):
        hub = DiagnosticsHub()
        with self.assertRaises(DiagnosticsUnavailableError):
            hub.capture_page()
        hub.start_capture("test", 0)
        with self.assertRaises(DiagnosticsUnavailableError):
            hub.capture_page()
        hub.stop_capture(1)
        self.assertEqual(hub.capture_page()["total"], 2)

    def test_record_failures_and_collection_failures_do_not_escape(self):
        source = EventSource()
        source.drain_events = lambda: (_ for _ in ()).throw(MemoryError())
        hub = DiagnosticsHub((("source", source),))
        self.assertFalse(hub.record_event("event", "bad\ncode", 0))
        self.assertEqual(hub.record_errors, 1)
        self.assertEqual(hub.step(1), 0)
        self.assertEqual(hub.collection_errors, 1)
        self.assertFalse(hub.snapshot()["operation_active"])

    def test_deinit_erases_all_ephemeral_records_and_is_idempotent(self):
        hub = DiagnosticsHub()
        hub.record_event("system", "boot", 0)
        hub.start_capture("test", 1)
        self.assertIsNone(hub.deinit())
        self.assertIsNone(hub.deinit())
        self.assertTrue(hub.closed)
        self.assertEqual(hub.snapshot()["event_count"], 0)
        self.assertFalse(hub.capture_status()["available"])


if __name__ == "__main__":
    unittest.main()
