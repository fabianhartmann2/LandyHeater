import ast
import copy
import inspect
import json
import unittest

import app.rest_application as rest_module
from app.configuration_api_gateway import (
    ConfigurationAPIConflictError,
    ConfigurationAPIInvariantError,
    ConfigurationAPINotFoundError,
    ConfigurationAPIResourceConflictError,
    ConfigurationAPIValidationError,
)
from app.manual_control_gateway import (
    ManualControlConfigurationConflictError,
    ManualControlConflictError,
    ManualControlInvariantError,
    ManualControlSessionConflictError,
    ManualControlStateConflictError,
    ManualControlUnavailableError,
)
from app.rest_application import RestApplication
from services.config_manager import (
    ConfigurationStateError,
)
from services.http_protocol import parse_request
from services.rest_security import RestSecurityPolicy
from services.rest_rate_limiter import RestRateLimiter


HOST = "192.168.4.1"
ORIGIN = "http://192.168.4.1"
TOKEN = "".join("{:02x}".format(value) for value in range(32))


def timer(timer_id="morning", name="Morning"):
    return {
        "id": timer_id,
        "name": name,
        "enabled": True,
        "weekdays": [0, 1, 2, 3, 4],
        "start": "06:30",
        "mode": "power",
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
    }


def public_configuration():
    return {
        "schema_version": 1,
        "system": {"setup_complete": True, "device_name": "Landy"},
        "heater": {
            "quick_start": {
                "mode": "power",
                "target_temperature": None,
                "power_level": 4,
                "runtime_minutes": 45,
            }
        },
        "sensors": {"active_role": "cabin"},
        "time": {"timezone": "Europe/Zurich"},
        "network": {
            "access_point": {
                "ssid": "Landy-Heater",
                "password_configured": True,
            },
            "known_networks": [{
                "id": "home",
                "ssid": "Home WiFi",
                "password_configured": True,
            }],
        },
        "timers": [],
    }


def controller_snapshot(revision=3, requested_on=False):
    requested = {
        "on": requested_on,
        "mode": "power",
        "target_temperature": None,
        "power_level": 5,
        "runtime_minutes": 30,
        "source": "manual",
    }
    actual = {
        "communication": "ok",
        "initialized": True,
        "synchronized": True,
        "heater_state": "off",
        "heater_state_raw": 0,
        "voltage": 12.5,
        "glow_plug_raw": 0,
        "fan_raw": 0,
        "last_status_ms": 9000,
    }
    session = None
    if requested_on:
        session = {
            "id": 1,
            "source": "manual",
            "mode": "power",
            "target": 5,
            "started_at_ms": 1000,
            "expires_at_ms": 62000,
            "runtime_minutes": 30,
            "confirmed_active": False,
            "expired": False,
        }
    return {
        "phase": "synchronized",
        "request_revision": revision,
        "requested": requested,
        "actual": actual,
        "session": session,
        "control_transition_pending": False,
        "control_faulted": False,
        "restart_blocked": False,
        "sensor_stop_latched": False,
        "active_sensor": None,
        "counters": {
            "invalid_frames": 0,
            "ignored_frames": 0,
            "communication_failures": 0,
            "control_failures": 0,
            "events_dropped": 0,
            "event_errors": 0,
        },
    }


def make_request(method="GET", target="/api/v1/status", headers=None, body=b""):
    headers = {} if headers is None else dict(headers)
    headers.setdefault("Host", HOST)
    if method in ("POST", "PUT", "PATCH"):
        headers.setdefault("Content-Length", str(len(body)))
    lines = ["{} {} HTTP/1.1".format(method, target)]
    for name, value in headers.items():
        lines.append("{}: {}".format(name, value))
    raw = "\r\n".join(lines).encode("ascii") + b"\r\n\r\n" + body
    return parse_request(raw)


def json_request(method, target, body, mutation_headers=None, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if mutation_headers is not None:
        headers.update(mutation_headers)
    if extra_headers is not None:
        headers.update(extra_headers)
    return make_request(method, target, headers, body)


def error_code(response):
    return response.body["error"]["code"]


class FakeConfigManager:
    def __init__(self):
        self.generation = 7

    def public_status(self):
        return {
            "loaded": True,
            "ledger_loaded": True,
            "generation": self.generation,
            "ledger_generation": 4,
            "load_status": "loaded",
            "ledger_load_status": "loaded",
            "migration_pending": False,
            "setup_complete": True,
            "faulted": False,
            "config_faulted": False,
            "ledger_faulted": False,
            "operational_faulted": False,
            "operation_active": False,
            "timer_start_allowed": True,
            "network_start_allowed": True,
            "events_pending": 0,
            "events_dropped": 0,
            "event_errors": 0,
            "config_store": {"available": True},
            "ledger_store": {"available": True},
        }


class FakeRuntime:
    def __init__(self):
        self.restart = False

    def snapshot(self):
        return {
            "configuration_generation": 7,
            "ledger_generation": 4,
            "setup_complete": True,
            "persistent_start_gate_open": True,
            "quick_start": {
                "mode": "power",
                "target_temperature": None,
                "power_level": 4,
                "runtime_minutes": 45,
            },
            "clock_valid": True,
            "scheduler_armed": True,
        }

    def restart_required(self, manager):
        return self.restart or manager.generation != 7


class FakeController:
    def __init__(self):
        self.maximum_runtime_minutes = 120
        self.value = controller_snapshot()
        self.on_snapshot = None

    @property
    def requested_on(self):
        return self.value["requested"]["on"]

    def public_snapshot(self):
        if self.on_snapshot is not None:
            self.on_snapshot()
        return copy.deepcopy(self.value)


class FakeConfigurationGateway:
    def __init__(self, manager):
        self.manager = manager
        self.configuration = public_configuration()
        self.timers = [timer("morning"), timer("evening", "Evening")]
        self.configuration["timers"] = copy.deepcopy(self.timers)
        self.calls = []
        self.failures = {}

    def _raise(self, name):
        failure = self.failures.get(name)
        if failure is not None:
            raise failure

    def settings_snapshot(self):
        self._raise("settings_snapshot")
        value = copy.deepcopy(self.configuration)
        value.pop("timers")
        return {
            "generation": self.manager.generation,
            "restart_required": False,
            **value,
        }

    def timers_snapshot(self):
        self._raise("timers_snapshot")
        return {
            "generation": self.manager.generation,
            "restart_required": False,
            "timers": copy.deepcopy(self.timers),
        }

    def patch_settings(self, patch, generation):
        self.calls.append(("patch_settings", copy.deepcopy(patch), generation))
        self._raise("patch_settings")
        for key, value in patch.items():
            self.configuration[key] = copy.deepcopy(value)
        self.manager.generation += 1
        return {
            "changed": True,
            "generation": self.manager.generation,
            "restart_required": True,
            "configuration": copy.deepcopy(self.configuration),
        }

    def complete_setup(self, setup, generation):
        self.calls.append(("complete_setup", copy.deepcopy(setup), generation))
        self._raise("complete_setup")
        for key in ("heater", "sensors", "time"):
            self.configuration[key] = copy.deepcopy(setup[key])
        self.configuration["system"]["setup_complete"] = True
        public_network = {
            "hostname": "heater",
            "access_point": {
                "ssid": "Landy Heater",
                "password_configured": True,
            },
            "known_networks": [],
        }
        for profile in setup["network"]["known_networks"]:
            public_network["known_networks"].append({
                "id": profile["id"],
                "ssid": profile["ssid"],
                "password_configured": profile["password_action"] != "open",
            })
        self.configuration["network"] = public_network
        self.manager.generation += 1
        return {
            "changed": True,
            "generation": self.manager.generation,
            "restart_required": True,
            "configuration": copy.deepcopy(self.configuration),
        }

    def create_timer(self, value, generation):
        self.calls.append(("create_timer", copy.deepcopy(value), generation))
        self._raise("create_timer")
        self.timers.append(copy.deepcopy(value))
        self.configuration["timers"] = copy.deepcopy(self.timers)
        self.manager.generation += 1
        return {
            "changed": True,
            "generation": self.manager.generation,
            "restart_required": True,
            "configuration": copy.deepcopy(self.configuration),
        }

    def replace_timer(self, timer_id, value, generation):
        self.calls.append((
            "replace_timer", timer_id, copy.deepcopy(value), generation
        ))
        self._raise("replace_timer")
        for index, existing in enumerate(self.timers):
            if existing["id"] == timer_id:
                self.timers[index] = copy.deepcopy(value)
                break
        self.configuration["timers"] = copy.deepcopy(self.timers)
        self.manager.generation += 1
        return {
            "changed": True,
            "generation": self.manager.generation,
            "restart_required": True,
            "configuration": copy.deepcopy(self.configuration),
        }

    def delete_timer(self, timer_id, generation):
        self.calls.append(("delete_timer", timer_id, generation))
        self._raise("delete_timer")
        self.timers = [item for item in self.timers if item["id"] != timer_id]
        self.configuration["timers"] = copy.deepcopy(self.timers)
        self.manager.generation += 1
        return {
            "changed": True,
            "generation": self.manager.generation,
            "restart_required": True,
            "configuration": copy.deepcopy(self.configuration),
        }

    def snapshot(self):
        return {
            "faulted": False,
            "last_error": None,
            "commits": 0,
            "noops": 0,
            "operation_active": False,
        }


class FakeManualGateway:
    def __init__(self, controller):
        self.controller = controller
        self.calls = []
        self.failures = {}
        self.start_changed = True
        self.quick_changed = True
        self.stop_changed = True

    def _raise(self, name):
        failure = self.failures.get(name)
        if failure is not None:
            raise failure

    def request_start(self, generation, revision, mode, target, power, runtime):
        self.calls.append((
            "start", generation, revision, mode, target, power, runtime
        ))
        self._raise("start")
        if self.start_changed:
            self.controller.value = controller_snapshot(revision + 1, True)
        return self.start_changed

    def request_quick_start(self, generation, revision):
        self.calls.append(("quick_start", generation, revision))
        self._raise("quick_start")
        if self.quick_changed:
            self.controller.value = controller_snapshot(revision + 1, True)
        return self.quick_changed

    def request_stop(self):
        self.calls.append(("stop",))
        failure = self.failures.get("stop")
        if failure is not None:
            if self.failures.get("stop_committed"):
                self.controller.value = controller_snapshot(
                    self.controller.value["request_revision"] + 1, False
                )
            raise failure
        if self.stop_changed:
            self.controller.value = controller_snapshot(
                self.controller.value["request_revision"] + 1, False
            )
        return self.stop_changed

    def request_session_update(
        self,
        generation,
        revision,
        target_temperature=None,
        extend_minutes=0,
    ):
        self.calls.append((
            "session_update",
            generation,
            revision,
            target_temperature,
            extend_minutes,
        ))
        self._raise("session_update")
        self.controller.value = controller_snapshot(revision + 1, True)
        session = self.controller.value["session"]
        if target_temperature is not None:
            self.controller.value["requested"]["mode"] = "temperature"
            self.controller.value["requested"]["target_temperature"] = target_temperature
            self.controller.value["requested"]["power_level"] = None
            session["mode"] = "temperature"
            session["target"] = target_temperature
        if extend_minutes:
            self.controller.value["requested"]["runtime_minutes"] += extend_minutes
            session["runtime_minutes"] += extend_minutes
            session["expires_at_ms"] += extend_minutes * 60000
        return True

    def snapshot(self):
        return {
            "faulted": False,
            "last_error": None,
            "request_revision": self.controller.value["request_revision"],
            "starts": 0,
            "stops": 0,
            "operation_active": False,
        }


class FakeTemperatureManager:
    def snapshot(self, now_ms):
        sensors = {}
        for role, value in (("roof_tent", 17), ("cabin", 19), ("outside", 8)):
            sensors[role] = {
                "value_c": value,
                "age_ms": now_ms - 9000,
                "health": "ok",
                "usable": True,
                "present": True,
                "rom": "secret-device-id",
            }
        return {
            "sensors": sensors,
            "assignments": {
                "roof_tent": None,
                "cabin": None,
                "outside": None,
            },
            "discovered_rom_ids": (),
            "last_error": "private-driver-text",
        }


class FakeTimeService:
    def snapshot(self, now_ms):
        return {
            "valid": True,
            "health": "ok",
            "rtc_health": "ok",
            "rtc_write_pending": False,
            "rtc_commit_revision": None,
            "source": "rtc",
            "timezone": "Europe/Zurich",
            "timezone_rule": "CET/CEST",
            "timezone_rule_version": 1,
            "utc_offset_minutes": 120,
            "is_dst": True,
            "sync_age_ms": 50,
            "utc_seconds": 1786400000,
            "local": "2026-08-11T10:00:00",
            "last_error": "private-clock-text",
        }


class FakeScheduler:
    def public_snapshot(self):
        return {
            "armed": True,
            "faulted": False,
            "configuration_revision": 7,
            "timer_count": 2,
            "active_occurrence_key": None,
            "active_occurrence": None,
            "consumed_local_high_water": None,
            "events_pending": 0,
            "events_dropped": 0,
            "event_errors": 0,
        }

    def next_occurrence(self, now_ms):
        return {
            "occurrence_key": "morning|2026-08-12|06:30",
            "timer_id": "morning",
            "local_date": "2026-08-12",
            "start": "06:30",
            "weekday": 2,
            "minutes_from_now": 1230,
        }


class FakeSchedulerGateway:
    def snapshot(self):
        return {
            "faulted": False,
            "last_error": None,
            "pending_override_key": None,
            "applied": 0,
            "rejected": 0,
            "manual_stops": 0,
            "persistence_enabled": True,
            "checkpoints": 0,
            "checkpoint_failures": 0,
            "operation_active": False,
        }


class FakeNetworkManager:
    def snapshot(self):
        return {
            "running": True,
            "closed": False,
            "faulted": False,
            "last_error": "private-network-text",
            "state": "running",
            "access_point": {
                "ssid": "Landy-Heater",
                "active": True,
                "ip": HOST,
                "clients": 1,
                "password_configured": True,
                "password": "NETWORK-SECRET",
            },
            "station": {
                "state": "idle",
                "connected": False,
                "profile_id": None,
                "ssid": None,
                "ip": None,
                "gateway": None,
                "dns": None,
                "rssi": None,
                "known_networks": [{
                    "id": "home",
                    "ssid": "Home WiFi",
                    "password_configured": True,
                }],
            },
            "mdns": {
                "hostname": "heater.local",
                "ready": True,
                "ap_only_guaranteed": False,
            },
            "internet_likely_available": False,
            "counters": {
                "attempts": 0,
                "connections": 0,
                "disconnects": 0,
                "ap_repairs": 0,
                "port_errors": 0,
                "events_dropped": 0,
                "event_errors": 0,
            },
        }


class Clock:
    def __init__(self):
        self.calls = 0
        self.value = 10000

    def __call__(self):
        self.calls += 1
        return self.value


class Fixture:
    def __init__(self, ingress="ap", rate_limiter=None):
        self.config_manager = FakeConfigManager()
        self.runtime = FakeRuntime()
        self.controller = FakeController()
        self.configuration = FakeConfigurationGateway(self.config_manager)
        self.manual = FakeManualGateway(self.controller)
        self.temperature = FakeTemperatureManager()
        self.time = FakeTimeService()
        self.scheduler = FakeScheduler()
        self.scheduler_gateway = FakeSchedulerGateway()
        self.network = FakeNetworkManager()
        self.security = RestSecurityPolicy(
            lambda size: bytes(range(size)),
            (HOST, "heater.local"),
            ingress,
        )
        self.security.start()
        self.clock = Clock()
        self.app = RestApplication(
            self.configuration,
            self.manual,
            self.config_manager,
            self.runtime,
            self.controller,
            self.temperature,
            self.time,
            self.scheduler,
            self.scheduler_gateway,
            self.security,
            network_manager=self.network,
            ticks_ms=self.clock,
            ticks_diff=lambda newer, older: newer - older,
            mem_free=lambda: 54321,
            rate_limiter=rate_limiter,
        )

    def mutation_headers(self, generation=None):
        headers = {
            "Origin": ORIGIN,
            "X-Landy-CSRF": TOKEN,
        }
        if generation is not None:
            headers["If-Match"] = '"config-{}"'.format(generation)
        return headers


class TestRestReadRoutes(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def test_status_is_allowlisted_and_samples_one_time_value(self):
        response = self.fixture.app.handle(make_request())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["api_version"], 1)
        self.assertEqual(response.body["request_id"], 1)
        self.assertEqual(response.body["configuration"]["stored_generation"], 7)
        self.assertEqual(response.body["heater"]["request_revision"], 3)
        self.assertEqual(response.body["heater"]["remaining_seconds"], None)
        self.assertEqual(response.body["temperatures"]["cabin"]["value_c"], 19)
        self.assertEqual(self.fixture.clock.calls, 1)
        rendered = repr(response.body)
        self.assertNotIn("secret-device-id", rendered)
        self.assertNotIn("private-driver-text", rendered)
        self.assertNotIn("private-clock-text", rendered)
        self.assertNotIn("private-network-text", rendered)
        self.assertNotIn("NETWORK-SECRET", rendered)
        self.assertNotIn("password'", rendered)

    def test_status_reports_requested_and_actual_separately_with_remaining_time(self):
        self.fixture.controller.value = controller_snapshot(9, True)
        response = self.fixture.app.handle(make_request())
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["heater"]["requested"]["on"])
        self.assertEqual(
            response.body["heater"]["actual"]["heater_state"], "off"
        )
        self.assertEqual(response.body["heater"]["remaining_seconds"], 52)

    def test_security_context_returns_token_only_on_ap_route(self):
        response = self.fixture.app.handle(
            make_request(target="/api/v1/security-context")
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["csrf_token"], TOKEN)
        self.assertTrue(response.body["mutation_api_available"])
        diagnostics = self.fixture.app.handle(
            make_request(target="/api/v1/diagnostics")
        )
        self.assertEqual(diagnostics.status, 200)
        self.assertNotIn(TOKEN, repr(diagnostics.body))
        self.assertNotIn("csrf_token", repr(diagnostics.body))

    def test_station_listener_is_read_only_and_has_no_token_endpoint(self):
        fixture = Fixture("sta")
        status = fixture.app.handle(make_request())
        self.assertEqual(status.status, 200)
        context = fixture.app.handle(
            make_request(target="/api/v1/security-context")
        )
        self.assertEqual(context.status, 503)
        self.assertEqual(error_code(context), "mutation_security_unavailable")
        start = fixture.app.handle(json_request(
            "POST",
            "/api/v1/heater/start",
            b'{"expected_request_revision":3,"mode":"power",'
            b'"target_temperature":null,"power_level":5,"runtime_minutes":30}',
            fixture.mutation_headers(7),
        ))
        self.assertEqual(start.status, 503)
        self.assertEqual(fixture.manual.calls, [])

    def test_host_origin_and_accept_are_enforced_on_reads(self):
        hostile = self.fixture.app.handle(make_request(
            headers={"Host": "attacker.invalid"}
        ))
        self.assertEqual(hostile.status, 403)
        cross_origin = self.fixture.app.handle(make_request(
            headers={"Origin": "http://attacker.invalid"}
        ))
        self.assertEqual(cross_origin.status, 403)
        non_json = self.fixture.app.handle(make_request(
            headers={"Accept": "text/html"}
        ))
        self.assertEqual(non_json.status, 406)
        accepted = self.fixture.app.handle(make_request(
            headers={"Accept": "text/plain, application/json; q=0.5"}
        ))
        self.assertEqual(accepted.status, 200)
        for value in (
            "application/json;q=0",
            "application/json;q=bogus",
            "application/json;q=0.5;q=1",
        ):
            with self.subTest(accept=value):
                rejected = self.fixture.app.handle(make_request(
                    headers={"Accept": value}
                ))
                self.assertEqual(rejected.status, 406)

    def test_settings_and_timer_reads_include_etag_and_bounded_pagination(self):
        settings = self.fixture.app.handle(
            make_request(target="/api/v1/settings")
        )
        self.assertEqual(settings.status, 200)
        self.assertEqual(settings.headers["ETag"], '"config-7"')
        self.assertNotIn("'password':", repr(settings.body))

        timers = self.fixture.app.handle(
            make_request(target="/api/v1/timers?offset=1&limit=1")
        )
        self.assertEqual(timers.status, 200)
        self.assertEqual(timers.body["total"], 2)
        self.assertEqual(timers.body["offset"], 1)
        self.assertEqual(timers.body["limit"], 1)
        self.assertEqual([item["id"] for item in timers.body["items"]], ["evening"])
        self.assertEqual(timers.headers["ETag"], '"config-7"')

    def test_setup_read_is_inert_redacted_and_reports_unperformed_checks(self):
        response = self.fixture.app.handle(
            make_request(target="/api/v1/setup")
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["ETag"], '"config-7"')
        self.assertTrue(response.body["system"]["setup_complete"])
        self.assertEqual(response.body["checks"]["sensors"]["state"], "not_run")
        self.assertFalse(
            response.body["checks"]["sensors"]["active_probe_performed"]
        )
        self.assertFalse(
            response.body["checks"]["autoterm"]["active_test_performed"]
        )
        self.assertNotIn("password", repr(response.body).replace(
            "password_configured", ""
        ))

    def test_timer_item_decodes_utf8_id_and_rejects_encoded_slash(self):
        self.fixture.configuration.timers.append(timer("küche", "Küche"))
        item = self.fixture.app.handle(
            make_request(target="/api/v1/timers/k%C3%BCche")
        )
        self.assertEqual(item.status, 200)
        self.assertEqual(item.body["timer"]["id"], "küche")
        invalid = self.fixture.app.handle(
            make_request(target="/api/v1/timers/a%2Fb")
        )
        self.assertEqual(invalid.status, 400)
        self.assertEqual(error_code(invalid), "invalid_timer_id")

    def test_unknown_deferred_and_invalid_query_routes_are_closed(self):
        for path in (
            "/api/v1/session",
            "/api/v1/events",
            "/api/v1/protocol-capture",
            "/api/v2/status",
        ):
            with self.subTest(path=path):
                response = self.fixture.app.handle(make_request(target=path))
                self.assertEqual(response.status, 404)
        query = self.fixture.app.handle(
            make_request(target="/api/v1/status?verbose=1")
        )
        self.assertEqual(query.status, 400)
        self.assertEqual(error_code(query), "query_not_supported")
        for query_string in (
            "offset=00", "limit=0", "limit=9", "offset=-1",
            "offset=1&offset=2", "page=1", "offset=%31",
        ):
            with self.subTest(query=query_string):
                response = self.fixture.app.handle(make_request(
                    target="/api/v1/timers?" + query_string
                ))
                self.assertEqual(response.status, 400)

    def test_known_and_unknown_methods_return_fixed_allow_headers(self):
        response = self.fixture.app.handle(
            make_request("POST", "/api/v1/status", body=b"")
        )
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers["Allow"], "GET")
        unknown = self.fixture.app.handle(
            make_request("OPTIONS", "/api/v1/status")
        )
        self.assertEqual(unknown.status, 405)
        self.assertEqual(unknown.headers["Allow"], "GET")
        unknown_path = self.fixture.app.handle(
            make_request("OPTIONS", "/api/v1/nope")
        )
        self.assertEqual(unknown_path.status, 405)
        self.assertNotIn("OPTIONS", repr(unknown_path.body))


class TestRestManualMutations(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.start_body = (
            b'{"expected_request_revision":3,"mode":"power",'
            b'"target_temperature":null,"power_level":5,"runtime_minutes":30}'
        )

    def _start(self, body=None, headers=None):
        if body is None:
            body = self.start_body
        if headers is None:
            headers = self.fixture.mutation_headers(7)
        return self.fixture.app.handle(json_request(
            "POST", "/api/v1/heater/start", body, headers
        ))

    def test_start_delegates_all_preconditions_and_returns_requested_not_actual(self):
        response = self._start()
        self.assertEqual(response.status, 202)
        self.assertTrue(response.body["changed"])
        self.assertTrue(response.body["heater"]["requested"]["on"])
        self.assertEqual(
            response.body["heater"]["actual"]["heater_state"], "off"
        )
        self.assertEqual(response.headers["ETag"], '"config-7"')
        self.assertEqual(self.fixture.manual.calls, [
            ("start", 7, 3, "power", None, 5, 30)
        ])
        self.assertEqual(self.fixture.app.snapshot()["mutations"], 1)

    def test_start_is_rolled_off_if_safe_success_response_cannot_be_built(self):
        def fail_readback():
            self.fixture.controller.on_snapshot = None
            raise OSError("private readback failure")

        self.fixture.controller.on_snapshot = fail_readback
        response = self._start()
        self.assertEqual(response.status, 503)
        self.assertEqual(error_code(response), "application_io_failed")
        self.assertFalse(self.fixture.controller.requested_on)
        self.assertEqual(
            self.fixture.manual.calls,
            [("start", 7, 3, "power", None, 5, 30), ("stop",)],
        )
        self.assertTrue(self.fixture.app.snapshot()["faulted"])

    def test_start_readback_oom_closes_response_path_and_rolls_requested_off(self):
        def fail_readback():
            self.fixture.controller.on_snapshot = None
            raise MemoryError("private allocator detail")

        self.fixture.controller.on_snapshot = fail_readback
        with self.assertRaises(MemoryError) as caught:
            self._start()
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertFalse(self.fixture.controller.requested_on)
        self.assertEqual(self.fixture.manual.calls[-1], ("stop",))
        self.assertTrue(self.fixture.app.snapshot()["faulted"])

    def test_post_dispatch_oom_cannot_leave_an_unacknowledged_start_on(self):
        class _CompleteOOMLimiter:
            def authorize(self, peer, method, path, now_ms):
                return (peer, False, now_ms)

            def complete(self, ticket, committed, completed_at_ms=None):
                raise MemoryError("private limiter detail")

            def snapshot(self):
                return {"faulted": False}

        fixture = Fixture(rate_limiter=_CompleteOOMLimiter())
        with self.assertRaises(MemoryError) as caught:
            fixture.app.handle(
                json_request(
                    "POST",
                    "/api/v1/heater/quick-start",
                    b'{"expected_request_revision":3}',
                    fixture.mutation_headers(7),
                ),
                "192.168.4.2",
            )
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertFalse(fixture.controller.requested_on)
        self.assertEqual(fixture.manual.calls[-1], ("stop",))
        self.assertTrue(fixture.app.snapshot()["faulted"])

    def test_idempotent_start_and_quick_start_return_200(self):
        self.fixture.manual.start_changed = False
        response = self._start()
        self.assertEqual(response.status, 200)
        self.assertFalse(response.body["changed"])

        self.fixture.manual.quick_changed = False
        quick = self.fixture.app.handle(json_request(
            "POST",
            "/api/v1/heater/quick-start",
            b'{"expected_request_revision":3}',
            self.fixture.mutation_headers(7),
        ))
        self.assertEqual(quick.status, 200)
        self.assertEqual(self.fixture.manual.calls[-1], ("quick_start", 7, 3))

    def test_mutation_requires_same_origin_csrf_and_allowed_host_before_gateway(self):
        cases = (
            {"If-Match": '"config-7"', "X-Landy-CSRF": TOKEN},
            {"If-Match": '"config-7"', "Origin": ORIGIN},
            {
                "If-Match": '"config-7"',
                "Origin": "http://attacker.invalid",
                "X-Landy-CSRF": TOKEN,
            },
            {
                "If-Match": '"config-7"',
                "Origin": ORIGIN,
                "X-Landy-CSRF": "f" * 64,
            },
        )
        for headers in cases:
            with self.subTest(headers=headers):
                response = self._start(headers=headers)
                self.assertEqual(response.status, 403)
        hostile = self.fixture.mutation_headers(7)
        hostile["Host"] = "attacker.invalid"
        self.assertEqual(self._start(headers=hostile).status, 403)
        self.assertEqual(self.fixture.manual.calls, [])

    def test_if_match_is_required_strict_and_current(self):
        missing = self._start(headers=self.fixture.mutation_headers())
        self.assertEqual(missing.status, 428)
        self.assertEqual(error_code(missing), "configuration_precondition_required")
        stale = self._start(headers=self.fixture.mutation_headers(6))
        self.assertEqual(stale.status, 412)
        self.assertEqual(stale.headers["ETag"], '"config-7"')
        self.assertEqual(stale.body["error"]["current_generation"], 7)
        for value in (
            "config-7", 'W/"config-7"', '"config-07"', '"config-x"',
            "*", '"config-2147483648"', '"config-7", "config-8"',
        ):
            with self.subTest(value=value):
                headers = self.fixture.mutation_headers()
                headers["If-Match"] = value
                response = self._start(headers=headers)
                self.assertEqual(response.status, 400)
        self.assertEqual(self.fixture.manual.calls, [])

    def test_json_is_strict_exact_and_not_echoed_on_errors(self):
        bodies = (
            b'{"expected_request_revision":3,"expected_request_revision":3,'
            b'"mode":"power","target_temperature":null,"power_level":5,'
            b'"runtime_minutes":30}',
            b'{"expected_request_revision":3,"mode":"power",'
            b'"target_temperature":null,"power_level":5,"runtime_minutes":30,'
            b'"password":"TOP-SECRET"}',
            b'{"expected_request_revision":3,"mode":"power",'
            b'"target_temperature":null,"power_level":5}',
            b'{"expected_request_revision":3.0,"mode":"power",'
            b'"target_temperature":null,"power_level":5,"runtime_minutes":30}',
            b'not-json-TOP-SECRET',
        )
        for body in bodies:
            with self.subTest(body=body):
                response = self._start(body=body)
                self.assertIn(response.status, (400, 422))
                self.assertNotIn("TOP-SECRET", repr(response.body))
        wrong_type = self.fixture.app.handle(make_request(
            "POST",
            "/api/v1/heater/start",
            {
                **self.fixture.mutation_headers(7),
                "Content-Type": "text/plain",
            },
            self.start_body,
        ))
        self.assertEqual(wrong_type.status, 415)
        self.assertEqual(self.fixture.manual.calls, [])

    def test_start_semantics_are_validated_before_manual_gateway(self):
        import json
        base = {
            "expected_request_revision": 3,
            "mode": "power",
            "target_temperature": None,
            "power_level": 5,
            "runtime_minutes": 30,
        }
        invalid_changes = (
            {"expected_request_revision": -1},
            {"expected_request_revision": True},
            {"mode": "unsupported"},
            {"power_level": 0},
            {"runtime_minutes": 121},
        )
        for change in invalid_changes:
            with self.subTest(change=change):
                body = dict(base)
                body.update(change)
                response = self._start(
                    json.dumps(body, separators=(",", ":")).encode()
                )
                self.assertEqual(response.status, 422)
                self.assertEqual(error_code(response), "validation_failed")
        self.assertEqual(self.fixture.manual.calls, [])

    def test_manual_conflicts_and_unavailability_have_distinct_safe_codes(self):
        cases = (
            (ManualControlStateConflictError("private"), 409, "heater_start_conflict"),
            (ManualControlConflictError("private"), 409, "control_precondition_failed"),
            (ManualControlUnavailableError("private"), 503, "application_unavailable"),
            (ManualControlInvariantError("private"), 503, "application_invariant_failed"),
        )
        for failure, status, code in cases:
            with self.subTest(failure=type(failure).__name__):
                fixture = Fixture()
                fixture.manual.failures["start"] = failure
                response = fixture.app.handle(json_request(
                    "POST",
                    "/api/v1/heater/start",
                    self.start_body,
                    fixture.mutation_headers(7),
                ))
                self.assertEqual(response.status, status)
                self.assertEqual(error_code(response), code)
                self.assertNotIn("private", repr(response.body))

    def test_configuration_race_during_start_is_a_412_not_requested_state_409(self):
        self.fixture.manual.failures["start"] = (
            ManualControlConfigurationConflictError("private")
        )
        response = self._start()
        self.assertEqual(response.status, 412)
        self.assertEqual(
            error_code(response), "configuration_precondition_failed"
        )
        self.assertEqual(response.headers["ETag"], '"config-7"')
        self.assertEqual(response.body["error"]["current_generation"], 7)
        self.assertNotIn("private", repr(response.body))

    def test_stop_has_no_etag_and_requires_an_empty_body(self):
        response = self.fixture.app.handle(make_request(
            "POST",
            "/api/v1/heater/stop",
            {**self.fixture.mutation_headers(), "Content-Length": "0"},
        ))
        self.assertEqual(response.status, 202)
        self.assertEqual(self.fixture.manual.calls, [("stop",)])
        self.assertNotIn("ETag", response.headers)

        with_json_type = self.fixture.app.handle(make_request(
            "POST",
            "/api/v1/heater/stop",
            {
                **self.fixture.mutation_headers(),
                "Content-Length": "0",
                "Content-Type": "application/json",
            },
        ))
        self.assertEqual(with_json_type.status, 422)

    def test_active_session_patch_is_exact_fenced_and_bounded(self):
        body = (
            b'{"expected_request_revision":3,"target_temperature":null,'
            b'"extend_minutes":15}'
        )
        response = self.fixture.app.handle(json_request(
            "PATCH",
            "/api/v1/heater/session",
            body,
            self.fixture.mutation_headers(7),
        ))
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["updated"])
        self.assertEqual(response.headers["ETag"], '"config-7"')
        self.assertEqual(self.fixture.manual.calls, [
            ("session_update", 7, 3, None, 15)
        ])

        for invalid in (
            b'{"expected_request_revision":3,"target_temperature":null,'
            b'"extend_minutes":0}',
            b'{"expected_request_revision":3,"target_temperature":31,'
            b'"extend_minutes":0}',
            b'{"expected_request_revision":3,"target_temperature":20,'
            b'"extend_minutes":10}',
        ):
            fixture = Fixture()
            rejected = fixture.app.handle(json_request(
                "PATCH",
                "/api/v1/heater/session",
                invalid,
                fixture.mutation_headers(7),
            ))
            self.assertEqual(rejected.status, 422)
            self.assertEqual(fixture.manual.calls, [])

        fixture = Fixture()
        fixture.manual.failures["session_update"] = (
            ManualControlSessionConflictError("private")
        )
        conflict = fixture.app.handle(json_request(
            "PATCH",
            "/api/v1/heater/session",
            body,
            fixture.mutation_headers(7),
        ))
        self.assertEqual(conflict.status, 409)
        self.assertEqual(error_code(conflict), "heater_session_conflict")
        self.assertNotIn("private", repr(conflict.body))

    def test_stop_failure_after_requested_off_reports_committed_truth(self):
        self.fixture.controller.value = controller_snapshot(3, True)
        self.fixture.manual.failures["stop"] = OSError("driver secret")
        self.fixture.manual.failures["stop_committed"] = True
        response = self.fixture.app.handle(make_request(
            "POST",
            "/api/v1/heater/stop",
            {**self.fixture.mutation_headers(), "Content-Length": "0"},
        ))
        self.assertEqual(response.status, 503)
        self.assertEqual(error_code(response), "stop_bookkeeping_failed")
        self.assertTrue(response.body["error"]["requested_off_committed"])
        self.assertNotIn("driver secret", repr(response.body))

    def test_stop_failure_before_off_maps_without_false_commit_claim(self):
        self.fixture.controller.value = controller_snapshot(3, True)
        self.fixture.manual.failures["stop"] = OSError("driver secret")
        response = self.fixture.app.handle(make_request(
            "POST",
            "/api/v1/heater/stop",
            {**self.fixture.mutation_headers(), "Content-Length": "0"},
        ))
        self.assertEqual(response.status, 503)
        self.assertEqual(error_code(response), "application_io_failed")
        self.assertNotIn("requested_off_committed", repr(response.body))


class TestRestConfigurationMutations(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def test_setup_completion_requires_mutation_guards_and_delegates_once(self):
        configuration = self.fixture.configuration.configuration
        setup = {
            "heater": copy.deepcopy(configuration["heater"]),
            "sensors": copy.deepcopy(configuration["sensors"]),
            "time": copy.deepcopy(configuration["time"]),
            "network": {
                "access_point": {
                    "password_action": "replace",
                    "password": "PrivateSetup92",
                },
                "known_networks": [{
                    "id": "home",
                    "ssid": "Home WiFi",
                    "password_action": "open",
                    "password": None,
                }],
            },
            "checks": {"sensors": "deferred", "autoterm": "deferred"},
        }
        body = json.dumps(setup, separators=(",", ":")).encode("utf-8")
        response = self.fixture.app.handle(json_request(
            "PUT",
            "/api/v1/setup",
            body,
            self.fixture.mutation_headers(7),
        ))
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["system"]["setup_complete"])
        self.assertEqual(response.headers["ETag"], '"config-8"')
        self.assertEqual(self.fixture.configuration.calls[0][0], "complete_setup")
        self.assertNotIn("PrivateSetup92", repr(response.body))

        rejected = Fixture().app.handle(json_request(
            "PUT", "/api/v1/setup", body, {}
        ))
        self.assertEqual(rejected.status, 403)

    def test_settings_patch_delegates_complete_groups_and_returns_public_readback(self):
        body = b'{"time":{"timezone":"UTC"}}'
        response = self.fixture.app.handle(json_request(
            "PATCH",
            "/api/v1/settings",
            body,
            self.fixture.mutation_headers(7),
        ))
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["changed"])
        self.assertTrue(response.body["restart_required"])
        self.assertEqual(response.body["time"], {"timezone": "UTC"})
        self.assertEqual(response.headers["ETag"], '"config-8"')
        self.assertEqual(self.fixture.configuration.calls, [
            ("patch_settings", {"time": {"timezone": "UTC"}}, 7)
        ])
        self.assertNotIn("'password':", repr(response.body))

    def test_settings_patch_rejects_empty_unknown_and_duplicate_groups(self):
        for body in (
            b"{}",
            b'{"network":{}}',
            b'{"time":{},"time":{}}',
            b'{"time":{},"password":"TOP-SECRET"}',
        ):
            with self.subTest(body=body):
                response = self.fixture.app.handle(json_request(
                    "PATCH",
                    "/api/v1/settings",
                    body,
                    self.fixture.mutation_headers(7),
                ))
                self.assertIn(response.status, (400, 422))
                self.assertNotIn("TOP-SECRET", repr(response.body))
        self.assertEqual(self.fixture.configuration.calls, [])

    def test_timer_crud_routes_generation_location_and_encoded_identity(self):
        value = timer("night heat", "Night")
        import json
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        created = self.fixture.app.handle(json_request(
            "POST",
            "/api/v1/timers",
            body,
            self.fixture.mutation_headers(7),
        ))
        self.assertEqual(created.status, 201)
        self.assertEqual(created.headers["ETag"], '"config-8"')
        self.assertEqual(
            created.headers["Location"],
            "/api/v1/timers/~id/6e696768742068656174",
        )
        self.assertEqual(created.body["timer"], value)

        value["power_level"] = 7
        replacement = json.dumps(value, separators=(",", ":")).encode("utf-8")
        replaced = self.fixture.app.handle(json_request(
            "PUT",
            "/api/v1/timers/night%20heat",
            replacement,
            self.fixture.mutation_headers(8),
        ))
        self.assertEqual(replaced.status, 200)
        self.assertEqual(replaced.headers["ETag"], '"config-9"')
        self.assertEqual(self.fixture.configuration.calls[-1][1], "night heat")

        deleted = self.fixture.app.handle(make_request(
            "DELETE",
            "/api/v1/timers/night%20heat",
            self.fixture.mutation_headers(9),
        ))
        self.assertEqual(deleted.status, 200)
        self.assertTrue(deleted.body["changed"])
        self.assertTrue(deleted.body["deleted"])
        self.assertEqual(deleted.headers["ETag"], '"config-10"')

    def test_all_persistable_timer_ids_have_a_safe_round_trip_location(self):
        import json
        invalid_ids = (
            "a/b",
            "a\\b",
            "a\x00b",
            "a\nb",
            "€" * 21,
        )
        for timer_id in invalid_ids:
            with self.subTest(timer_id=repr(timer_id)):
                fixture = Fixture()
                body = json.dumps(
                    timer(timer_id), separators=(",", ":")
                ).encode("utf-8")
                response = fixture.app.handle(json_request(
                    "POST", "/api/v1/timers", body,
                    fixture.mutation_headers(7),
                ))
                self.assertEqual(response.status, 201)
                location = response.headers["Location"]
                self.assertLessEqual(len(location), 192)
                self.assertNotIn(timer_id, location)
                fetched = fixture.app.handle(make_request(target=location))
                self.assertEqual(fetched.status, 200)
                self.assertEqual(fetched.body["timer"]["id"], timer_id)

                replacement = timer(timer_id)
                replacement["power_level"] = 6
                updated = fixture.app.handle(json_request(
                    "PUT",
                    location,
                    json.dumps(
                        replacement, separators=(",", ":")
                    ).encode("utf-8"),
                    fixture.mutation_headers(8),
                ))
                self.assertEqual(updated.status, 200)
                removed = fixture.app.handle(make_request(
                    "DELETE",
                    location,
                    fixture.mutation_headers(9),
                ))
                self.assertEqual(removed.status, 200)

    def test_hex_timer_resource_path_has_no_alias_collision(self):
        import json

        fixture = Fixture()
        values = (timer("m"), timer("~id/6d"))
        locations = []
        generation = 7
        for value in values:
            response = fixture.app.handle(json_request(
                "POST",
                "/api/v1/timers",
                json.dumps(value, separators=(",", ":")).encode(),
                fixture.mutation_headers(generation),
            ))
            self.assertEqual(response.status, 201)
            locations.append(response.headers["Location"])
            generation += 1
        self.assertNotEqual(locations[0], locations[1])
        self.assertEqual(
            fixture.app.handle(make_request(target=locations[0])).body["timer"]["id"],
            "m",
        )
        self.assertEqual(
            fixture.app.handle(make_request(target=locations[1])).body["timer"]["id"],
            "~id/6d",
        )

    def test_timer_replacement_path_and_body_identity_must_match(self):
        import json

        response = self.fixture.app.handle(json_request(
            "PUT",
            "/api/v1/timers/morning",
            json.dumps(timer("evening"), separators=(",", ":")).encode(),
            self.fixture.mutation_headers(7),
        ))
        self.assertEqual(response.status, 422)
        self.assertEqual(error_code(response), "validation_failed")
        self.assertEqual(self.fixture.configuration.calls, [])

    def test_noncanonical_timer_is_rejected_instead_of_echoing_false_readback(self):
        import json
        for mutation in (
            lambda value: value.__setitem__("weekdays", [4, 0]),
            lambda value: value.__setitem__("name", 123),
        ):
            with self.subTest(mutation=mutation):
                fixture = Fixture()
                value = timer("new")
                mutation(value)
                response = fixture.app.handle(json_request(
                    "POST",
                    "/api/v1/timers",
                    json.dumps(value, separators=(",", ":")).encode(),
                    fixture.mutation_headers(7),
                ))
                self.assertEqual(response.status, 422)
                self.assertEqual(fixture.configuration.calls, [])

    def test_timer_gateway_error_mapping_is_specific_and_redacted(self):
        cases = (
            ("create_timer", ConfigurationAPIResourceConflictError("secret"), 409, "resource_conflict"),
            ("create_timer", ConfigurationAPIConflictError("secret"), 412, "configuration_precondition_failed"),
            ("create_timer", ConfigurationAPIInvariantError("secret"), 503, "application_invariant_failed"),
            ("create_timer", ConfigurationAPIValidationError("secret"), 422, "validation_failed"),
            ("create_timer", ConfigurationStateError("secret"), 503, "application_unavailable"),
        )
        import json
        body = json.dumps(timer("new"), separators=(",", ":")).encode("utf-8")
        for operation, failure, status, code in cases:
            with self.subTest(failure=type(failure).__name__):
                fixture = Fixture()
                fixture.configuration.failures[operation] = failure
                response = fixture.app.handle(json_request(
                    "POST", "/api/v1/timers", body,
                    fixture.mutation_headers(7),
                ))
                self.assertEqual(response.status, status)
                self.assertEqual(error_code(response), code)
                self.assertNotIn("secret", repr(response.body))

        fixture = Fixture()
        fixture.configuration.failures["replace_timer"] = (
            ConfigurationAPINotFoundError("secret")
        )
        missing = fixture.app.handle(json_request(
            "PUT", "/api/v1/timers/missing",
            json.dumps(timer("missing"), separators=(",", ":")).encode(),
            fixture.mutation_headers(7),
        ))
        self.assertEqual(missing.status, 404)
        self.assertEqual(error_code(missing), "resource_not_found")

    def test_stale_generation_is_rechecked_by_gateway_and_current_etag_returned(self):
        import json
        self.fixture.configuration.failures["create_timer"] = (
            ConfigurationAPIConflictError("raced")
        )
        self.fixture.config_manager.generation = 8
        request = json_request(
            "POST",
            "/api/v1/timers",
            json.dumps(timer("new"), separators=(",", ":")).encode(),
            self.fixture.mutation_headers(8),
        )
        response = self.fixture.app.handle(request)
        self.assertEqual(response.status, 412)
        self.assertEqual(response.headers["ETag"], '"config-8"')
        self.assertEqual(response.body["error"]["current_generation"], 8)


class TestRestHardening(unittest.TestCase):
    def test_peer_rate_limit_returns_429_but_never_blocks_stop(self):
        limiter = RestRateLimiter(lambda newer, older: newer - older)
        fixture = Fixture(rate_limiter=limiter)
        for _ in range(10):
            response = fixture.app.handle(
                make_request(target="/api/v1/status"), "192.168.4.2"
            )
            self.assertEqual(response.status, 200)
        limited = fixture.app.handle(
            make_request(target="/api/v1/status"), "192.168.4.2"
        )
        self.assertEqual(limited.status, 429)
        self.assertEqual(error_code(limited), "rate_limit_exceeded")
        self.assertEqual(limited.headers["Retry-After"], "10")

        fixture.controller.value = controller_snapshot(3, True)
        stopped = fixture.app.handle(
            make_request(
                "POST",
                "/api/v1/heater/stop",
                fixture.mutation_headers(),
            ),
            "192.168.4.2",
        )
        self.assertEqual(stopped.status, 202)
        self.assertFalse(fixture.controller.requested_on)

    def test_durable_timer_delete_starts_the_config_write_cooldown(self):
        limiter = RestRateLimiter(lambda newer, older: newer - older)
        fixture = Fixture(rate_limiter=limiter)
        deleted = fixture.app.handle(
            make_request(
                "DELETE",
                "/api/v1/timers/morning",
                fixture.mutation_headers(7),
            ),
            "192.168.4.2",
        )
        self.assertEqual(deleted.status, 200)
        self.assertTrue(deleted.body["changed"])
        fixture.clock.value = 11000
        limited = fixture.app.handle(
            json_request(
                "PATCH",
                "/api/v1/settings",
                b'{"time":{"timezone":"UTC"}}',
                fixture.mutation_headers(8),
            ),
            "192.168.4.2",
        )
        self.assertEqual(limited.status, 429)
        self.assertEqual(limiter.snapshot()["config_commits"], 1)

    def test_constructor_rejects_missing_routed_quick_start_dependency(self):
        fixture = Fixture()
        fixture.manual.request_quick_start = None
        with self.assertRaises(ValueError):
            RestApplication(
                fixture.configuration,
                fixture.manual,
                fixture.config_manager,
                fixture.runtime,
                fixture.controller,
                fixture.temperature,
                fixture.time,
                fixture.scheduler,
                fixture.scheduler_gateway,
                fixture.security,
            )

    def test_public_configuration_projection_cannot_leak_a_password_field(self):
        fixture = Fixture()
        fixture.configuration.configuration["network"]["access_point"][
            "password"
        ] = "ROUTER-SECRET"
        response = fixture.app.handle(make_request(target="/api/v1/settings"))
        self.assertEqual(response.status, 503)
        self.assertEqual(error_code(response), "application_invariant_failed")
        self.assertNotIn("ROUTER-SECRET", repr(response.body))

    def test_reentrant_callback_rejects_both_visible_operations_and_faults(self):
        fixture = Fixture()
        inner = []

        def reenter():
            fixture.controller.on_snapshot = None
            inner.append(fixture.app.handle(make_request(
                target="/api/v1/status"
            )))

        fixture.controller.on_snapshot = reenter
        outer = fixture.app.handle(make_request(target="/api/v1/status"))
        self.assertEqual(inner[0].status, 503)
        self.assertEqual(error_code(inner[0]), "rest_reentrancy_rejected")
        self.assertEqual(outer.status, 503)
        self.assertEqual(error_code(outer), "rest_reentrancy_detected")
        snapshot = fixture.app.snapshot()
        self.assertTrue(snapshot["faulted"])
        self.assertEqual(snapshot["last_error"], "rest_application_reentrancy_detected")
        self.assertFalse(snapshot["operation_active"])

        mutation = fixture.app.handle(json_request(
            "POST",
            "/api/v1/heater/quick-start",
            b'{"expected_request_revision":3}',
            fixture.mutation_headers(7),
        ))
        self.assertEqual(mutation.status, 503)
        self.assertEqual(error_code(mutation), "rest_service_faulted")
        self.assertEqual(fixture.manual.calls, [])

    def test_reentrant_start_is_rolled_off_and_stop_bypasses_fault_latch(self):
        fixture = Fixture()
        inner = []

        def reentrant_quick(generation, revision):
            inner.append(fixture.app.handle(make_request(
                target="/api/v1/status"
            )))
            fixture.controller.value = controller_snapshot(revision + 1, True)
            return True

        fixture.manual.request_quick_start = reentrant_quick
        outer = fixture.app.handle(json_request(
            "POST",
            "/api/v1/heater/quick-start",
            b'{"expected_request_revision":3}',
            fixture.mutation_headers(7),
        ))
        self.assertEqual(inner[0].status, 503)
        self.assertEqual(outer.status, 503)
        self.assertEqual(error_code(outer), "rest_reentrancy_detected")
        self.assertFalse(fixture.controller.requested_on)
        self.assertIn(("stop",), fixture.manual.calls)
        self.assertTrue(fixture.app.snapshot()["faulted"])

        fixture.controller.value = controller_snapshot(5, True)
        stopped = fixture.app.handle(make_request(
            "POST",
            "/api/v1/heater/stop",
            fixture.mutation_headers(),
        ))
        self.assertEqual(stopped.status, 202)
        self.assertFalse(fixture.controller.requested_on)

    def test_dependency_errors_are_fixed_and_request_content_is_never_echoed(self):
        cases = (
            (ValueError("TOP-SECRET"), 503, "application_contract_failed"),
            (OSError("TOP-SECRET"), 503, "application_io_failed"),
            (RuntimeError("TOP-SECRET"), 503, "application_operation_failed"),
            (KeyError("TOP-SECRET"), 500, "internal_error"),
        )
        for failure, status, code in cases:
            with self.subTest(failure=type(failure).__name__):
                fixture = Fixture()
                fixture.configuration.failures["settings_snapshot"] = failure
                response = fixture.app.handle(make_request(
                    target="/api/v1/settings"
                ))
                self.assertEqual(response.status, status)
                self.assertEqual(error_code(response), code)
                self.assertNotIn("TOP-SECRET", repr(response.body))

    def test_malformed_public_snapshot_is_an_internal_invariant_not_client_422(self):
        fixture = Fixture()
        fixture.controller.value = {"phase": "malformed"}
        response = fixture.app.handle(make_request(target="/api/v1/status"))
        self.assertEqual(response.status, 503)
        self.assertEqual(error_code(response), "application_contract_failed")

    def test_invalid_tick_source_is_contained_as_a_fixed_service_error(self):
        fixture = Fixture()
        fixture.app._RestApplication__ticks_ms = lambda: "not-an-integer"
        response = fixture.app.handle(make_request(target="/api/v1/status"))
        self.assertEqual(response.status, 503)
        self.assertEqual(error_code(response), "application_contract_failed")
        self.assertFalse(fixture.app.snapshot()["operation_active"])

    def test_memory_error_propagates_and_operation_guard_is_cleared(self):
        fixture = Fixture()
        failure = MemoryError("allocator detail")
        fixture.configuration.failures["settings_snapshot"] = failure
        with self.assertRaises(MemoryError) as caught:
            fixture.app.handle(make_request(target="/api/v1/settings"))
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertIsNot(caught.exception, failure)
        self.assertFalse(fixture.app.snapshot()["operation_active"])

    def test_request_ids_are_monotonic_and_error_counters_are_bounded_state(self):
        fixture = Fixture()
        first = fixture.app.handle(make_request())
        second = fixture.app.handle(make_request(target="/api/v1/nope"))
        third = fixture.app.handle(make_request())
        self.assertEqual(first.body["request_id"], 1)
        self.assertEqual(second.body["error"]["request_id"], 2)
        self.assertEqual(third.body["request_id"], 3)
        self.assertEqual(fixture.app.snapshot()["requests"], 3)
        self.assertEqual(fixture.app.snapshot()["errors"], 1)

    def test_diagnostics_does_not_consume_events_or_protocol_capture(self):
        source = inspect.getsource(rest_module.RestApplication._diagnostics_data)
        self.assertNotIn("drain_events", source)
        self.assertNotIn("protocol", source.lower())
        fixture = Fixture()
        response = fixture.app.handle(make_request(
            target="/api/v1/diagnostics"
        ))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["heap_free_bytes"], 54321)

    def test_module_has_no_socket_hardware_protocol_or_direct_control_imports(self):
        source = inspect.getsource(rest_module)
        tree = ast.parse(source)
        imports = set()
        attributes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Attribute):
                attributes.add(node.attr)
        for forbidden in (
            "socket", "network", "machine", "hardware",
            "protocol.autoterm_protocol",
        ):
            self.assertNotIn(forbidden, imports)
        for forbidden_call in ("step", "send_start", "send_stop", "drain_events"):
            self.assertNotIn(forbidden_call, attributes)


if __name__ == "__main__":
    unittest.main()
