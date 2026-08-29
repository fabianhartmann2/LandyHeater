"""Hardware-independent AP/STA network state machine.

The manager keeps the local access point authoritative and independent from
station connectivity.  It performs no network, socket, UART, heater or board
I/O at import or construction time.  A small injected port is the only object
allowed to touch MicroPython's ``network`` module.

One call to :meth:`step` performs at most one bounded port action.  The ESP32
driver's automatic reconnect loop is disabled by the hardware port; this core
therefore owns profile rotation, connection timeouts and wrap-safe backoff.
Passwords are accepted only as private configuration and never appear in
snapshots, events or error strings.
"""

import time as _time

from app.network_configuration import (
    MAX_KNOWN_NETWORKS,
    MAX_NETWORK_ID_BYTES,
    MAX_SSID_BYTES,
    MAX_STA_PSK_BYTES,
    MAX_WIFI_PASSWORD_BYTES,
    MIN_WIFI_PASSWORD_BYTES,
    NETWORK_AP_SSID,
    NETWORK_HOSTNAME,
    NETWORK_MDNS_NAME,
    default_network_configuration,
    validate_network_configuration,
)

DEFAULT_EVENT_CAPACITY = 16
MAX_EVENT_CAPACITY = 64
DEFAULT_AP_CHECK_INTERVAL_MS = 5000
DEFAULT_STATION_POLL_INTERVAL_MS = 1000
DEFAULT_CONNECTION_TIMEOUT_MS = 15000
DEFAULT_PROFILE_GAP_MS = 1000
DEFAULT_ROUND_BACKOFF_MS = 5000
MAX_ROUND_BACKOFF_MS = 60000
MAX_BACKOFF_EXPONENT = 4

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_OFFLINE = "offline"
STATE_CONNECTING = "connecting"
STATE_ONLINE = "online"
STATE_BACKOFF = "backoff"
STATE_DEGRADED = "degraded"
STATE_FAULTED = "faulted"
STATE_CLOSED = "closed"

STA_IDLE = "idle"
STA_CONNECTING = "connecting"
STA_WRONG_PASSWORD = "wrong_password"
STA_NO_AP = "no_ap"
STA_CONNECT_FAIL = "connect_fail"
STA_GOT_IP = "got_ip"
STA_STATES = (
    STA_IDLE,
    STA_CONNECTING,
    STA_WRONG_PASSWORD,
    STA_NO_AP,
    STA_CONNECT_FAIL,
    STA_GOT_IP,
)

_AP_STATUS_FIELDS = frozenset(("active", "ip", "clients"))
_STA_STATUS_FIELDS = frozenset((
    "state",
    "raw_status",
    "connected",
    "profile_id",
    "ssid",
    "ip",
    "gateway",
    "dns",
    "rssi",
    "mdns_ready",
))
_PORT_METHODS = (
    "configure_hostname",
    "ensure_access_point",
    "access_point_status",
    "prepare_station",
    "connect_station",
    "station_status",
    "disconnect_station",
    "deinit",
)


class NetworkPortError(Exception):
    """Expected, recoverable WLAN driver or link error."""


class NetworkPortContractError(RuntimeError):
    """The injected WLAN port returned a malformed result."""


def _plain_ticks_diff(newer, older):
    return newer - older


def _plain_ticks_add(ticks, delta):
    return ticks + delta


_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)
_platform_ticks_add = getattr(_time, "ticks_add", _plain_ticks_add)


def _require_integer(name, value, minimum=None, maximum=None):
    if type(value) is not int:
        raise ValueError("{} must be an integer".format(name))
    if minimum is not None and value < minimum:
        raise ValueError("{} is below its minimum".format(name))
    if maximum is not None and value > maximum:
        raise ValueError("{} exceeds its maximum".format(name))
    return value


def _require_ticks(now_ms):
    _require_integer("now_ms", now_ms)


def _require_exact_dict(name, value, fields):
    if type(value) is not dict or frozenset(value) != fields:
        raise ValueError("{} has an invalid shape".format(name))
    for key in value:
        if type(key) is not str:
            raise ValueError("{} keys must be strings".format(name))
    return value


class NetworkManager:
    """Keep the AP available while fairly rotating bounded STA profiles."""

    __slots__ = (
        "_port",
        "_configuration",
        "_ticks_diff",
        "_ticks_add",
        "_ap_check_interval_ms",
        "_station_poll_interval_ms",
        "_connection_timeout_ms",
        "_profile_gap_ms",
        "_round_backoff_ms",
        "_event_capacity",
        "_events",
        "events_dropped",
        "event_errors",
        "_running",
        "_closed",
        "_faulted",
        "_operation_active",
        "_operation_reentered",
        "_phase",
        "_state",
        "_last_error",
        "_last_step_ms",
        "_next_action_ms",
        "_next_ap_check_ms",
        "_next_station_poll_ms",
        "_connection_started_ms",
        "_ap_check_deferred",
        "_resume_station_phase_after_ap",
        "_resume_station_deadline_after_ap",
        "_resume_station_state_after_ap",
        "_profile_index",
        "_round_backoff_exponent",
        "_station_phase",
        "_station_connected",
        "_station_raw_status",
        "_station_profile_id",
        "_station_ssid",
        "_station_ip",
        "_station_gateway",
        "_station_dns",
        "_station_rssi",
        "_mdns_ready",
        "_ap_active",
        "_ap_ip",
        "_ap_clients",
        "_attempts",
        "_connections",
        "_disconnects",
        "_ap_repairs",
        "_port_errors",
    )

    def __init__(
        self,
        port,
        network_configuration,
        ticks_diff=None,
        ticks_add=None,
        ap_check_interval_ms=DEFAULT_AP_CHECK_INTERVAL_MS,
        station_poll_interval_ms=DEFAULT_STATION_POLL_INTERVAL_MS,
        connection_timeout_ms=DEFAULT_CONNECTION_TIMEOUT_MS,
        profile_gap_ms=DEFAULT_PROFILE_GAP_MS,
        round_backoff_ms=DEFAULT_ROUND_BACKOFF_MS,
        event_capacity=DEFAULT_EVENT_CAPACITY,
    ):
        for method_name in _PORT_METHODS:
            if not callable(getattr(port, method_name, None)):
                raise ValueError("port must provide {}()".format(method_name))
        if (ticks_diff is None) != (ticks_add is None):
            raise ValueError("ticks_diff and ticks_add must be provided together")
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
            ticks_add = _platform_ticks_add
        if not callable(ticks_diff) or not callable(ticks_add):
            raise ValueError("tick helpers must be callable")
        for name, value, maximum in (
            ("ap_check_interval_ms", ap_check_interval_ms, 60000),
            ("station_poll_interval_ms", station_poll_interval_ms, 10000),
            ("connection_timeout_ms", connection_timeout_ms, 120000),
            ("profile_gap_ms", profile_gap_ms, 60000),
            ("round_backoff_ms", round_backoff_ms, MAX_ROUND_BACKOFF_MS),
            ("event_capacity", event_capacity, MAX_EVENT_CAPACITY),
        ):
            _require_integer(name, value, 1, maximum)
        if connection_timeout_ms < station_poll_interval_ms:
            raise ValueError("connection timeout is shorter than station poll")

        self._port = port
        self._configuration = validate_network_configuration(
            network_configuration, require_ap_password=True
        )
        self._ticks_diff = ticks_diff
        self._ticks_add = ticks_add
        self._ap_check_interval_ms = ap_check_interval_ms
        self._station_poll_interval_ms = station_poll_interval_ms
        self._connection_timeout_ms = connection_timeout_ms
        self._profile_gap_ms = profile_gap_ms
        self._round_backoff_ms = round_backoff_ms
        self._event_capacity = event_capacity
        self._events = []
        self.events_dropped = 0
        self.event_errors = 0
        self._running = False
        self._closed = False
        self._faulted = False
        self._operation_active = False
        self._operation_reentered = False
        self._phase = STATE_STOPPED
        self._state = STATE_STOPPED
        self._last_error = None
        self._last_step_ms = None
        self._next_action_ms = None
        self._next_ap_check_ms = None
        self._next_station_poll_ms = None
        self._connection_started_ms = None
        self._ap_check_deferred = False
        self._resume_station_phase_after_ap = None
        self._resume_station_deadline_after_ap = None
        self._resume_station_state_after_ap = None
        self._profile_index = 0
        self._round_backoff_exponent = 0
        self._station_phase = STA_IDLE
        self._station_connected = False
        self._station_raw_status = None
        self._station_profile_id = None
        self._station_ssid = None
        self._station_ip = None
        self._station_gateway = None
        self._station_dns = None
        self._station_rssi = None
        self._mdns_ready = False
        self._ap_active = False
        self._ap_ip = None
        self._ap_clients = 0
        self._attempts = 0
        self._connections = 0
        self._disconnects = 0
        self._ap_repairs = 0
        self._port_errors = 0

    @property
    def running(self):
        return self._running

    @property
    def faulted(self):
        return self._faulted

    @property
    def closed(self):
        return self._closed

    @property
    def access_point_available(self):
        return self._ap_active

    def _emit(self, code, profile_id=None):
        try:
            event = {"code": code}
            if profile_id is not None:
                event["profile_id"] = str(profile_id)[:MAX_NETWORK_ID_BYTES]
            if len(self._events) >= self._event_capacity:
                self._events.pop(0)
                self.events_dropped += 1
            self._events.append(event)
        except Exception:
            self.event_errors += 1

    def drain_events(self):
        events = self._events
        self._events = []
        return events

    def _begin_operation(self):
        if self._operation_active:
            self._operation_reentered = True
            self._faulted = True
            self._state = STATE_FAULTED
            self._last_error = "network_reentrant_operation"
            raise RuntimeError("network operation is already active")
        self._operation_active = True
        self._operation_reentered = False

    def _finish_operation(self):
        reentered = self._operation_reentered
        self._operation_reentered = False
        cleanup_error = None
        try:
            if not reentered:
                return
            self._faulted = True
            self._last_error = "network_reentrant_operation"
            if self._closed:
                # The interrupted driver call may continue after the nested
                # deinit and switch a radio interface back on.  Repeat the
                # closed-first cleanup after that outer call has returned.
                try:
                    result = self._port.deinit()
                    if result is not None:
                        raise NetworkPortContractError(
                            "network port deinit returned a value"
                        )
                except BaseException as error:
                    cleanup_error = error
                self._running = False
                self._phase = STATE_CLOSED
                self._state = STATE_CLOSED
                self._resume_station_phase_after_ap = None
                self._resume_station_deadline_after_ap = None
                self._resume_station_state_after_ap = None
                self._ap_active = False
                self._ap_ip = None
                self._ap_clients = 0
                self._station_phase = STA_IDLE
                self._station_connected = False
                self._station_ip = None
                self._station_gateway = None
                self._station_dns = None
                self._station_rssi = None
                self._mdns_ready = False
            else:
                self._state = STATE_FAULTED
        finally:
            self._operation_active = False
            self._operation_reentered = False
        if cleanup_error is not None:
            raise cleanup_error
        if reentered:
            raise RuntimeError("network operation was re-entered")

    def _latch_fault(self, code):
        self._faulted = True
        self._state = STATE_FAULTED
        self._last_error = code
        self._emit("network_faulted")

    def start(self, now_ms):
        _require_ticks(now_ms)
        if self._closed:
            raise RuntimeError("network manager is closed")
        if self._faulted:
            raise RuntimeError("network manager is faulted")
        if self._running:
            return False
        self._running = True
        self._phase = "hostname"
        self._state = STATE_STARTING
        self._last_step_ms = now_ms
        self._next_action_ms = now_ms
        self._next_ap_check_ms = now_ms
        self._next_station_poll_ms = now_ms
        self._ap_check_deferred = False
        self._resume_station_phase_after_ap = None
        self._resume_station_deadline_after_ap = None
        self._resume_station_state_after_ap = None
        self._emit("network_start_requested")
        return True

    def _validate_ap_status(self, status):
        status = _require_exact_dict("AP status", status, _AP_STATUS_FIELDS)
        if type(status["active"]) is not bool:
            raise NetworkPortContractError("network AP active is malformed")
        if status["ip"] is not None and type(status["ip"]) is not str:
            raise NetworkPortContractError("network AP IP is malformed")
        if type(status["clients"]) is not int or status["clients"] < 0:
            raise NetworkPortContractError("network AP clients are malformed")
        if status["active"] and not status["ip"]:
            raise NetworkPortContractError("active AP has no IP address")
        if not status["active"] and (
            status["ip"] is not None or status["clients"] != 0
        ):
            raise NetworkPortContractError("inactive AP truth is inconsistent")
        self._ap_active = status["active"]
        self._ap_ip = status["ip"]
        self._ap_clients = status["clients"]

    def _clear_ap_truth(self):
        self._ap_active = False
        self._ap_ip = None
        self._ap_clients = 0

    def _validate_station_status(self, status):
        status = _require_exact_dict("STA status", status, _STA_STATUS_FIELDS)
        if type(status["state"]) is not str or status["state"] not in STA_STATES:
            raise NetworkPortContractError("network STA state is malformed")
        if type(status["raw_status"]) is not int:
            raise NetworkPortContractError("network STA raw status is malformed")
        if type(status["connected"]) is not bool:
            raise NetworkPortContractError("network STA connected is malformed")
        for field in ("profile_id", "ssid", "ip", "gateway", "dns"):
            if status[field] is not None and type(status[field]) is not str:
                raise NetworkPortContractError("network STA text is malformed")
        if status["rssi"] is not None and type(status["rssi"]) is not int:
            raise NetworkPortContractError("network STA RSSI is malformed")
        if type(status["mdns_ready"]) is not bool:
            raise NetworkPortContractError("network mDNS state is malformed")
        if status["connected"] is not (status["state"] == STA_GOT_IP):
            raise NetworkPortContractError("network STA truth is inconsistent")
        if status["connected"] and (
            not status["ip"] or status["ip"] == "0.0.0.0"
        ):
            raise NetworkPortContractError("connected STA has no IP address")
        if status["connected"] and (
            not status["profile_id"] or not status["ssid"]
        ):
            raise NetworkPortContractError(
                "connected STA has no profile identity"
            )
        if not status["connected"] and (
            status["ip"] is not None
            or status["gateway"] is not None
            or status["dns"] is not None
            or status["rssi"] is not None
            or status["mdns_ready"]
        ):
            raise NetworkPortContractError(
                "disconnected STA truth is inconsistent"
            )
        self._station_phase = status["state"]
        self._station_connected = status["connected"]
        self._station_raw_status = status["raw_status"]
        self._station_profile_id = status["profile_id"]
        self._station_ssid = status["ssid"]
        self._station_ip = status["ip"]
        self._station_gateway = status["gateway"]
        self._station_dns = status["dns"]
        self._station_rssi = status["rssi"]
        self._mdns_ready = status["mdns_ready"]
        return status

    def _clear_station_reachability(self):
        self._station_phase = STA_IDLE
        self._station_connected = False
        self._station_ip = None
        self._station_gateway = None
        self._station_dns = None
        self._station_rssi = None
        self._mdns_ready = False

    def _remember_station_resume_after_ap(self):
        if self._phase in ("running", "station_prepare", "disconnect"):
            self._resume_station_phase_after_ap = self._phase
            self._resume_station_deadline_after_ap = self._next_action_ms
            self._resume_station_state_after_ap = self._state

    def _handle_port_error(self, code, now_ms, ap_error=False):
        self._port_errors += 1
        self._last_error = code
        if ap_error:
            self._remember_station_resume_after_ap()
            self._clear_ap_truth()
            self._ap_check_deferred = False
            self._phase = "ap"
            self._state = STATE_DEGRADED
            self._next_action_ms = self._ticks_add(
                now_ms, self._ap_check_interval_ms
            )
            self._emit("network_ap_retry")
        else:
            # A failed status read makes the previously observed STA link
            # untrusted immediately.  Clear all reachability/mDNS truth before
            # the later explicit disconnect action can run.
            self._clear_station_reachability()
            self._phase = "disconnect"
            self._state = STATE_DEGRADED
            self._next_action_ms = now_ms
            self._emit("network_station_error", self._station_profile_id)

    def _advance_profile(self, now_ms):
        profiles = self._configuration["known_networks"]
        if not profiles:
            self._profile_index = 0
            self._next_action_ms = None
            self._state = STATE_OFFLINE
            return
        self._profile_index += 1
        delay = self._profile_gap_ms
        if self._profile_index >= len(profiles):
            self._profile_index = 0
            if self._round_backoff_exponent < MAX_BACKOFF_EXPONENT:
                self._round_backoff_exponent += 1
            delay = self._round_backoff_ms << (
                self._round_backoff_exponent - 1
            )
            if delay > MAX_ROUND_BACKOFF_MS:
                delay = MAX_ROUND_BACKOFF_MS
            self._state = STATE_BACKOFF
        else:
            self._state = STATE_OFFLINE
        self._next_action_ms = self._ticks_add(now_ms, delay)

    def _step_core(self, now_ms):
        if not self._running or self._faulted or self._closed:
            return None
        if self._last_step_ms is not None and self._ticks_diff(
            now_ms, self._last_step_ms
        ) < 0:
            self._latch_fault("network_monotonic_time_reversed")
            raise ValueError("now_ms moved backwards")
        self._last_step_ms = now_ms
        action_due = (
            self._next_action_ms is None
            or self._ticks_diff(now_ms, self._next_action_ms) >= 0
        )
        ap_check_due = (
            self._phase in ("running", "station_prepare", "disconnect")
            and (
                self._next_ap_check_ms is None
                or self._ticks_diff(now_ms, self._next_ap_check_ms) >= 0
            )
        )
        station_action_preferred = bool(
            ap_check_due and action_due and self._ap_check_deferred
        )
        # A long station backoff must never postpone AP supervision.
        if not action_due and not ap_check_due:
            return None

        if self._phase == "hostname":
            try:
                result = self._port.configure_hostname(
                    self._configuration["hostname"]
                )
            except NetworkPortError:
                self._port_errors += 1
                self._last_error = "network_hostname_failed"
                self._state = STATE_DEGRADED
                # ``heater.local`` is a convenience, while the direct AP IP
                # is the mandatory offline recovery path.  A driver/hostname
                # failure must therefore never prevent the WPA2 AP from
                # starting.  A later explicit restart/reset retries hostname
                # setup; until then the port reports mDNS as unavailable.
                self._phase = "ap"
                self._next_action_ms = now_ms
                self._emit("network_hostname_unavailable")
                return "hostname_degraded"
            if result is not None:
                raise NetworkPortContractError(
                    "network hostname operation returned a value"
                )
            self._phase = "ap"
            self._next_action_ms = now_ms
            return "hostname_configured"

        if self._phase == "ap":
            ap = self._configuration["access_point"]
            # A failed/malformed/fatal observation must not preserve a stale
            # claim that the direct recovery AP remains reachable.
            self._clear_ap_truth()
            try:
                status = self._port.ensure_access_point(
                    ap["ssid"], ap["password"]
                )
                self._validate_ap_status(status)
            except NetworkPortError:
                self._handle_port_error(
                    "network_ap_operation_failed", now_ms, True
                )
                return "ap_retry"
            if not self._ap_active:
                self._handle_port_error(
                    "network_ap_not_active", now_ms, True
                )
                return "ap_retry"
            self._ap_repairs += 1
            self._next_ap_check_ms = self._ticks_add(
                now_ms, self._ap_check_interval_ms
            )
            self._ap_check_deferred = False
            resume_phase = self._resume_station_phase_after_ap
            resume_deadline = self._resume_station_deadline_after_ap
            resume_state = self._resume_station_state_after_ap
            self._resume_station_phase_after_ap = None
            self._resume_station_deadline_after_ap = None
            self._resume_station_state_after_ap = None
            if self._station_connected:
                # MicroPython keeps AP and STA link truth independent in
                # APSTA mode.  Repairing the AP must not reconnect a live STA.
                self._phase = "running"
                self._state = STATE_ONLINE
            elif resume_phase in ("running", "station_prepare", "disconnect"):
                self._phase = resume_phase
                self._state = resume_state
            elif self._configuration["known_networks"]:
                self._phase = "station_prepare"
                self._state = STATE_STARTING
            else:
                self._phase = "running"
                self._state = STATE_OFFLINE
            self._next_action_ms = (
                resume_deadline if resume_phase is not None else now_ms
            )
            self._emit("network_ap_available")
            return "ap_available"

        # Disconnect/driver-recovery can itself fail for a long time.  AP
        # supervision therefore remains independent from those station
        # phases instead of waiting for them to finish.
        if (
            self._phase in ("station_prepare", "disconnect")
            and ap_check_due
            and not station_action_preferred
        ):
            self._clear_ap_truth()
            try:
                self._validate_ap_status(self._port.access_point_status())
            except NetworkPortError:
                self._handle_port_error(
                    "network_ap_status_failed", now_ms, True
                )
                return "ap_retry"
            self._next_ap_check_ms = self._ticks_add(
                now_ms, self._ap_check_interval_ms
            )
            if not self._ap_active:
                self._ap_check_deferred = False
                self._remember_station_resume_after_ap()
                self._phase = "ap"
                self._state = STATE_DEGRADED
                self._next_action_ms = now_ms
                return "ap_lost"
            self._ap_check_deferred = bool(
                action_due and self._configuration["known_networks"]
            )
            return "ap_checked"

        if station_action_preferred:
            self._ap_check_deferred = False

        if self._phase == "station_prepare":
            try:
                result = self._port.prepare_station()
            except NetworkPortError:
                self._port_errors += 1
                self._last_error = "network_station_prepare_failed"
                self._state = STATE_DEGRADED
                self._next_action_ms = self._ticks_add(
                    now_ms, self._profile_gap_ms
                )
                self._emit("network_station_prepare_retry")
                return "station_retry"
            if result is not None:
                raise NetworkPortContractError(
                    "network station prepare returned a value"
                )
            self._phase = "running"
            self._state = STATE_OFFLINE
            self._next_action_ms = now_ms
            return "station_ready"

        if self._phase == "disconnect":
            try:
                result = self._port.disconnect_station()
            except NetworkPortError:
                self._port_errors += 1
                self._last_error = "network_station_disconnect_failed"
                self._next_action_ms = self._ticks_add(
                    now_ms, self._profile_gap_ms
                )
                return "disconnect_retry"
            if result is not None:
                raise NetworkPortContractError(
                    "network station disconnect returned a value"
                )
            self._disconnects += 1
            self._station_phase = STA_IDLE
            self._station_connected = False
            self._station_profile_id = None
            self._station_ssid = None
            self._station_ip = None
            self._station_gateway = None
            self._station_dns = None
            self._station_rssi = None
            self._mdns_ready = False
            self._phase = (
                "station_prepare"
                if self._configuration["known_networks"]
                else "running"
            )
            self._advance_profile(now_ms)
            return "station_disconnected"

        if self._phase != "running":
            raise NetworkPortContractError("network manager phase is invalid")

        if (
            (
                self._next_ap_check_ms is None
                or self._ticks_diff(now_ms, self._next_ap_check_ms) >= 0
            )
            and not station_action_preferred
        ):
            self._clear_ap_truth()
            try:
                self._validate_ap_status(self._port.access_point_status())
            except NetworkPortError:
                self._handle_port_error(
                    "network_ap_status_failed", now_ms, True
                )
                return "ap_retry"
            self._next_ap_check_ms = self._ticks_add(
                now_ms, self._ap_check_interval_ms
            )
            if not self._ap_active:
                self._ap_check_deferred = False
                self._remember_station_resume_after_ap()
                self._phase = "ap"
                self._state = STATE_DEGRADED
                self._next_action_ms = now_ms
                return "ap_lost"
            self._ap_check_deferred = bool(
                action_due and self._configuration["known_networks"]
            )
            return "ap_checked"

        if station_action_preferred:
            self._ap_check_deferred = False

        profiles = self._configuration["known_networks"]
        if not profiles:
            self._state = STATE_OFFLINE
            self._next_action_ms = self._next_ap_check_ms
            return None

        if self._station_phase in (STA_CONNECTING, STA_GOT_IP):
            if self._next_station_poll_ms is not None and self._ticks_diff(
                now_ms, self._next_station_poll_ms
            ) < 0:
                self._next_action_ms = self._next_station_poll_ms
                return None
            was_online = self._state == STATE_ONLINE
            # Revoke the previous IP/mDNS assertion before asking the driver.
            # Only a completely validated observation may restore it.
            self._clear_station_reachability()
            try:
                observation = self._validate_station_status(
                    self._port.station_status()
                )
            except NetworkPortError:
                self._handle_port_error(
                    "network_station_status_failed", now_ms, False
                )
                return "station_retry"
            self._next_station_poll_ms = self._ticks_add(
                now_ms, self._station_poll_interval_ms
            )
            if observation["connected"]:
                if not was_online:
                    self._connections += 1
                    self._emit(
                        "network_station_connected",
                        observation["profile_id"],
                    )
                self._state = STATE_ONLINE
                self._round_backoff_exponent = 0
                self._last_error = None
                self._next_action_ms = self._next_station_poll_ms
                return "station_connected"
            if observation["state"] == STA_CONNECTING:
                if self._connection_started_ms is not None and self._ticks_diff(
                    now_ms, self._connection_started_ms
                ) >= self._connection_timeout_ms:
                    self._last_error = "network_station_timeout"
                    self._state = STATE_OFFLINE
                    self._phase = "disconnect"
                    self._next_action_ms = now_ms
                    return "station_timeout"
                self._state = STATE_CONNECTING
                self._next_action_ms = self._next_station_poll_ms
                return "station_connecting"
            self._last_error = "network_station_{}".format(
                observation["state"]
            )
            self._state = STATE_OFFLINE
            self._phase = "disconnect"
            self._next_action_ms = now_ms
            return "station_failed"

        if self._next_action_ms is not None and self._ticks_diff(
            now_ms, self._next_action_ms
        ) < 0:
            return None
        profile = profiles[self._profile_index]
        try:
            result = self._port.connect_station(
                profile["id"], profile["ssid"], profile["password"]
            )
        except NetworkPortError:
            self._port_errors += 1
            self._last_error = "network_station_connect_failed"
            self._station_profile_id = profile["id"]
            self._phase = "disconnect"
            self._next_action_ms = now_ms
            return "station_retry"
        if result is not None:
            raise NetworkPortContractError(
                "network station connect returned a value"
            )
        self._attempts += 1
        self._station_phase = STA_CONNECTING
        self._station_connected = False
        self._station_profile_id = profile["id"]
        self._station_ssid = profile["ssid"]
        self._connection_started_ms = now_ms
        self._next_station_poll_ms = self._ticks_add(
            now_ms, self._station_poll_interval_ms
        )
        self._next_action_ms = self._next_station_poll_ms
        self._state = STATE_CONNECTING
        self._emit("network_station_connecting", profile["id"])
        return "station_connecting"

    def step(self, now_ms):
        _require_ticks(now_ms)
        self._begin_operation()
        primary = None
        result = None
        memory_failed = False
        try:
            result = self._step_core(now_ms)
        except NetworkPortError:
            # Port errors are normally contained at their call site.  An
            # unclassified one here is still recoverable but stops new STA
            # work until the AP-first startup path is retried.
            self._handle_port_error("network_port_failed", now_ms, True)
            result = "ap_retry"
        except MemoryError:
            primary = MemoryError()
            memory_failed = True
            try:
                self._latch_fault("network_memory_error")
            except MemoryError:
                # Truth is committed before best-effort event bookkeeping.
                pass
            # The injected port has just received a credential.  Preserve the
            # fatal allocation type while removing vendor text and exception
            # context that could echo that credential.
        except BaseException as error:
            primary = error
            self._latch_fault("network_contract_error")
            raise
        finally:
            try:
                self._finish_operation()
            except BaseException:
                if primary is None:
                    raise
        if memory_failed:
            # Raise outside the vendor exception handler so ``__context__``
            # cannot retain a credential-bearing driver exception.
            raise primary
        return result

    def reset_fault(self, now_ms):
        _require_ticks(now_ms)
        if self._closed:
            raise RuntimeError("network manager is closed")
        if not self._faulted:
            return False
        self._faulted = False
        self._last_error = None
        self._last_step_ms = now_ms
        self._phase = "hostname"
        self._state = STATE_STARTING
        self._next_action_ms = now_ms
        self._ap_check_deferred = False
        self._resume_station_phase_after_ap = None
        self._resume_station_deadline_after_ap = None
        self._resume_station_state_after_ap = None
        self._emit("network_fault_reset")
        return True

    def replace_configuration(self, candidate):
        canonical = validate_network_configuration(
            candidate, require_ap_password=True
        )
        if self._running or self._closed or self._operation_active:
            raise RuntimeError(
                "network configuration can change only while stopped"
            )
        if canonical == self._configuration:
            return False
        self._configuration = canonical
        self._profile_index = 0
        self._round_backoff_exponent = 0
        self._resume_station_phase_after_ap = None
        self._resume_station_deadline_after_ap = None
        self._resume_station_state_after_ap = None
        return True

    def deinit(self):
        owns_operation = not self._operation_active
        if owns_operation:
            self._begin_operation()
        else:
            self._operation_reentered = True
            # A recursive callback after the first close commit must not call
            # the hardware cleanup a second time in the same stack.
            if self._closed:
                return None
        primary = None
        try:
            self._closed = True
            self._running = False
            self._phase = STATE_CLOSED
            self._state = STATE_CLOSED
            self._resume_station_phase_after_ap = None
            self._resume_station_deadline_after_ap = None
            self._resume_station_state_after_ap = None
            self._ap_active = False
            self._ap_ip = None
            self._ap_clients = 0
            self._station_phase = STA_IDLE
            self._station_connected = False
            self._station_ip = None
            self._station_gateway = None
            self._station_dns = None
            self._station_rssi = None
            self._mdns_ready = False
            result = self._port.deinit()
            if result is not None:
                raise NetworkPortContractError(
                    "network port deinit returned a value"
                )
            self._emit("network_closed")
            return None
        except BaseException as error:
            primary = error
            raise
        finally:
            if owns_operation:
                try:
                    self._finish_operation()
                except BaseException:
                    if primary is None:
                        raise

    def snapshot(self):
        profiles = [
            {
                "id": item["id"],
                "ssid": item["ssid"],
                "password_configured": item["password"] is not None,
            }
            for item in self._configuration["known_networks"]
        ]
        internet_likely = (
            self._station_connected
            and self._station_ip not in (None, "0.0.0.0")
            and self._station_gateway not in (None, "0.0.0.0")
            and self._station_dns not in (None, "0.0.0.0")
        )
        return {
            "running": self._running,
            "closed": self._closed,
            "faulted": self._faulted,
            "state": self._state,
            "last_error": self._last_error,
            "access_point": {
                "ssid": NETWORK_AP_SSID,
                "active": self._ap_active,
                "ip": self._ap_ip,
                "clients": self._ap_clients,
                "password_configured": True,
            },
            "station": {
                "state": self._station_phase,
                "raw_status": self._station_raw_status,
                "connected": self._station_connected,
                "profile_id": self._station_profile_id,
                "ssid": self._station_ssid,
                "ip": self._station_ip,
                "gateway": self._station_gateway,
                "dns": self._station_dns,
                "rssi": self._station_rssi,
                "known_networks": profiles,
            },
            "mdns": {
                "hostname": NETWORK_MDNS_NAME,
                "ready": self._mdns_ready,
                "ap_only_guaranteed": False,
            },
            "internet_likely_available": bool(internet_likely),
            "counters": {
                "attempts": self._attempts,
                "connections": self._connections,
                "disconnects": self._disconnects,
                "ap_repairs": self._ap_repairs,
                "port_errors": self._port_errors,
                "events_dropped": self.events_dropped,
                "event_errors": self.event_errors,
            },
        }
