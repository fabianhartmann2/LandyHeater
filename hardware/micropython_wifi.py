"""Lazy, credential-safe MicroPython-v1.28 WLAN binding.

Importing this module performs no ``network`` import and no radio operation.
The public factory first checks the board-level Phase-7 lock, then imports the
MicroPython driver and leases its singleton AP/STA interfaces.  Passwords are
passed directly to the driver and are never retained, returned or interpolated
into an exception.
"""

from app.network_manager import (
    NETWORK_AP_SSID,
    NETWORK_HOSTNAME,
    MAX_NETWORK_ID_BYTES,
    MAX_SSID_BYTES,
    MAX_STA_PSK_BYTES,
    MAX_WIFI_PASSWORD_BYTES,
    MIN_WIFI_PASSWORD_BYTES,
    NetworkPortContractError,
    NetworkPortError,
    STA_CONNECTING,
    STA_CONNECT_FAIL,
    STA_GOT_IP,
    STA_IDLE,
    STA_NO_AP,
    STA_WRONG_PASSWORD,
)


ERROR_ALREADY_OWNED = "micropython_wifi_already_owned"
ERROR_AP_CONFIGURATION = "micropython_wifi_ap_configuration_failed"
ERROR_AP_REQUIRED = "micropython_wifi_ap_required"
ERROR_AP_STATUS = "micropython_wifi_ap_status_failed"
ERROR_CLOSED = "micropython_wifi_closed"
ERROR_CONNECT = "micropython_wifi_connect_failed"
ERROR_DISCONNECT = "micropython_wifi_disconnect_failed"
ERROR_DRIVER_CONTRACT = "micropython_wifi_driver_contract_failed"
ERROR_FACTORY = "micropython_wifi_factory_failed"
ERROR_FACTORY_CLEANUP = "micropython_wifi_factory_cleanup_failed"
ERROR_HOSTNAME = "micropython_wifi_hostname_failed"
ERROR_STATION_PREPARE = "micropython_wifi_station_prepare_failed"
ERROR_STATION_STATUS = "micropython_wifi_station_status_failed"
ERROR_CLEANUP = "micropython_wifi_cleanup_failed"


_WIFI_LEASED = False
_WIFI_LEASE_POISONED = False


def _require_none(result):
    if result is not None:
        raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)


def _bounded_utf8(name, value, maximum):
    if type(value) is not str or not value:
        raise ValueError("{} must be a non-empty string".format(name))
    try:
        encoded = value.encode("utf-8")
    except (UnicodeError, ValueError):
        raise ValueError("{} must be UTF-8 encodable".format(name))
    if len(encoded) > maximum or b"\x00" in encoded:
        raise ValueError("{} exceeds its byte bound".format(name))
    return value


def _validate_hostname(value):
    value = _bounded_utf8("network hostname", value, 32)
    if value != NETWORK_HOSTNAME:
        raise ValueError("network hostname must be heater")
    return value


def _validate_ssid(value, access_point=False):
    value = _bounded_utf8("Wi-Fi SSID", value, MAX_SSID_BYTES)
    if access_point and value != NETWORK_AP_SSID:
        raise ValueError("access point SSID must be Landy Heater")
    return value


def _validate_profile_id(value):
    value = _bounded_utf8(
        "network profile id", value, MAX_NETWORK_ID_BYTES
    )
    if "|" in value:
        raise ValueError("network profile id contains a reserved delimiter")
    return value


def _validate_password(value, allow_open=False, station=False):
    if value is None:
        if allow_open:
            return None
        raise ValueError("Wi-Fi password is required")
    if type(value) is not str:
        raise ValueError("Wi-Fi password must be a string")
    try:
        encoded = value.encode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("Wi-Fi password must use printable ASCII")
    length = len(encoded)
    valid = MIN_WIFI_PASSWORD_BYTES <= length <= MAX_WIFI_PASSWORD_BYTES
    if station and length == MAX_STA_PSK_BYTES:
        valid = True
        for character in value:
            if character not in "0123456789abcdefABCDEF":
                valid = False
                break
    if not valid:
        raise ValueError("Wi-Fi password has an invalid length")
    for byte in encoded:
        if byte < 32 or byte > 126:
            raise ValueError("Wi-Fi password must use printable ASCII")
    return value


def _require_boolean(name, value):
    if type(value) is not bool:
        raise NetworkPortContractError(
            "{} returned a non-boolean".format(name)
        )
    return value


def _require_active_transition(name, value, expected):
    """Validate the v1.28 ESP32 ``WLAN.active(bool)`` return contract."""

    value = _require_boolean(name, value)
    if value is not expected:
        raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
    return value


def _require_ipv4_text(name, value, allow_unspecified=False):
    if type(value) is not str or not value or len(value) > 15:
        raise NetworkPortContractError(
            "{} returned an invalid IPv4 value".format(name)
        )
    parts = value.split(".")
    if len(parts) != 4:
        raise NetworkPortContractError(
            "{} returned an invalid IPv4 value".format(name)
        )
    for part in parts:
        if not part or len(part) > 3:
            raise NetworkPortContractError(
                "{} returned an invalid IPv4 value".format(name)
            )
        number = 0
        for character in part:
            if character < "0" or character > "9":
                raise NetworkPortContractError(
                    "{} returned an invalid IPv4 value".format(name)
                )
            number = number * 10 + ord(character) - ord("0")
        if number > 255:
            raise NetworkPortContractError(
                "{} returned an invalid IPv4 value".format(name)
            )
    if not allow_unspecified and value == "0.0.0.0":
        raise NetworkPortContractError(
            "{} returned an unspecified IPv4 value".format(name)
        )
    return value


def _require_ifconfig(name, value):
    if type(value) not in (tuple, list) or len(value) != 4:
        raise NetworkPortContractError(
            "{} returned a malformed interface configuration".format(name)
        )
    ip = _require_ipv4_text(name, value[0])
    _require_ipv4_text(name, value[1])
    gateway = _require_ipv4_text(name, value[2], True)
    dns = _require_ipv4_text(name, value[3], True)
    return ip, gateway, dns


def _driver_error(code, operation, *args):
    failed = False
    memory_failed = False
    try:
        return operation(*args)
    except MemoryError:
        memory_failed = True
    except Exception:
        failed = True
    if memory_failed:
        # A vendor allocation error can echo a credential in its message.
        # Preserve the fatal type, but raise outside the handler so neither
        # text nor exception context can retain driver-provided secrets.
        raise MemoryError() from None
    if failed:
        # Raise outside the except suite.  In addition to suppressing printed
        # chaining this keeps the vendor exception out of ``__context__``.
        raise NetworkPortError(code)


def _serialized_mutation(method):
    """Keep one port mutation active until its outer driver call returns."""

    def guarded(self, *args, **kwargs):
        self._begin_mutation()
        try:
            return method(self, *args, **kwargs)
        finally:
            self._end_mutation()

    return guarded


class MicroPythonWiFiPort:
    """Normalize MicroPython's singleton WLAN interfaces to NetworkManager."""

    __slots__ = (
        "__hostname",
        "__ap",
        "__sta",
        "__wpa2",
        "__max_clients",
        "__station_reconnects",
        "__states",
        "__idle_status",
        "__got_ip_status",
        "__hostname_configured",
        "__station_prepared",
        "__profile_id",
        "__ssid",
        "__closed",
        "__sta_disconnected",
        "__sta_inactive",
        "__ap_inactive",
        "__lease_owner",
        "__mutation_active",
        "__cleanup_finalized",
    )

    def __init__(
        self,
        network_module,
        access_point,
        station,
        max_clients,
        wpa2_security,
        status_values,
        station_reconnects=0,
    ):
        hostname = getattr(network_module, "hostname", None)
        if not callable(hostname):
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        if access_point is station:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        for interface, methods in (
            (access_point, ("active", "config", "status", "ifconfig")),
            (
                station,
                (
                    "active",
                    "config",
                    "status",
                    "isconnected",
                    "ifconfig",
                    "connect",
                    "disconnect",
                ),
            ),
        ):
            for method_name in methods:
                if not callable(getattr(interface, method_name, None)):
                    raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        if type(max_clients) is not int or not 1 <= max_clients <= 10:
            raise ValueError("WIFI_AP_MAX_CLIENTS must be between 1 and 10")
        if type(station_reconnects) is not int or station_reconnects != 0:
            raise ValueError("station reconnects must be disabled")
        if type(wpa2_security) is not int:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        if type(status_values) is not tuple or len(status_values) != 6:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        for value in status_values:
            if type(value) is not int:
                raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        if len(set(status_values)) != len(status_values):
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)

        self.__hostname = hostname
        self.__ap = access_point
        self.__sta = station
        self.__wpa2 = wpa2_security
        self.__max_clients = max_clients
        self.__station_reconnects = station_reconnects
        self.__states = {
            status_values[0]: STA_IDLE,
            status_values[1]: STA_CONNECTING,
            status_values[2]: STA_WRONG_PASSWORD,
            status_values[3]: STA_NO_AP,
            status_values[4]: STA_CONNECT_FAIL,
            status_values[5]: STA_GOT_IP,
        }
        self.__idle_status = status_values[0]
        self.__got_ip_status = status_values[5]
        self.__hostname_configured = False
        self.__station_prepared = False
        self.__profile_id = None
        self.__ssid = None
        self.__closed = False
        self.__sta_disconnected = False
        self.__sta_inactive = False
        self.__ap_inactive = False
        self.__lease_owner = False
        self.__mutation_active = False
        self.__cleanup_finalized = False

    @property
    def closed(self):
        return self.__closed

    @property
    def cleanup_complete(self):
        return (
            self.__sta_disconnected
            and self.__sta_inactive
            and self.__ap_inactive
        )

    def _claim_lease(self):
        self.__lease_owner = True
        self.__cleanup_finalized = False

    def _begin_mutation(self):
        if self.__mutation_active:
            raise NetworkPortError(ERROR_ALREADY_OWNED)
        self.__mutation_active = True

    def _end_mutation(self):
        self.__mutation_active = False

    def _require_open(self):
        if self.__closed:
            raise NetworkPortError(ERROR_CLOSED)

    def _ap_is_active(self):
        result = _driver_error(
            ERROR_AP_STATUS, self.__ap.active
        )
        return _require_boolean("access point active()", result)

    def _station_is_active(self):
        result = _driver_error(
            ERROR_STATION_STATUS, self.__sta.active
        )
        return _require_boolean("station active()", result)

    def _rollback_station_prepare(self):
        """Best-effort radio-off after a post-activation prepare failure."""

        self.__station_prepared = False
        for _ in range(2):
            try:
                result = self.__sta.active(False)
                _require_active_transition(
                    "station active(False)", result, False
                )
                confirmed = self.__sta.active()
                _require_active_transition(
                    "station active() confirmation", confirmed, False
                )
                self.__sta_inactive = True
                self.__sta_disconnected = True
                return True
            except BaseException:
                # The primary prepare failure remains authoritative.  A false
                # return leaves the port conservatively marked active so a
                # later deinit can retry and cannot claim cleanup_complete.
                pass
        return False

    @_serialized_mutation
    def configure_hostname(self, name):
        self._require_open()
        name = _validate_hostname(name)
        # A failed retry must revoke earlier mDNS readiness immediately.
        self.__hostname_configured = False
        result = _driver_error(ERROR_HOSTNAME, self.__hostname, name)
        _require_none(result)
        self._require_open()
        confirmed = _driver_error(ERROR_HOSTNAME, self.__hostname)
        self._require_open()
        if type(confirmed) is not str or confirmed != name:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        self.__hostname_configured = True
        return None

    @_serialized_mutation
    def ensure_access_point(self, ssid, password):
        self._require_open()
        ssid = _validate_ssid(ssid, True)
        password = _validate_password(password)
        try:
            result = self.__ap.config(
                ssid=ssid,
                security=self.__wpa2,
                key=password,
                max_clients=self.__max_clients,
            )
        except MemoryError:
            memory_failed = True
            failed = False
        except Exception:
            memory_failed = False
            failed = True
        else:
            memory_failed = False
            failed = False
        password = None
        if memory_failed:
            # Preserve the OOM signal but discard a vendor-supplied message
            # which could contain the key passed to config().
            raise MemoryError()
        if failed:
            raise NetworkPortError(ERROR_AP_CONFIGURATION)
        _require_none(result)
        self._require_open()
        # Treat the interface as potentially live before entering the driver.
        # A reentrant close may run inside active(True), after which the outer
        # vendor call can still finish by switching the singleton back on.
        self.__ap_inactive = False
        result = _driver_error(
            ERROR_AP_CONFIGURATION, self.__ap.active, True
        )
        _require_active_transition("access point active(True)", result, True)
        self.__ap_inactive = False
        self._require_open()
        status = self.access_point_status()
        if status["active"] is not True:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        self.__ap_inactive = False
        return status

    def access_point_status(self):
        self._require_open()
        active = self._ap_is_active()
        if not active:
            return {"active": False, "ip": None, "clients": 0}
        raw_ifconfig = _driver_error(
            ERROR_AP_STATUS, self.__ap.ifconfig
        )
        ip, _, _ = _require_ifconfig("access point", raw_ifconfig)
        stations = _driver_error(
            ERROR_AP_STATUS, self.__ap.status, "stations"
        )
        if type(stations) not in (tuple, list):
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        clients = len(stations)
        if clients > self.__max_clients:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        return {"active": True, "ip": ip, "clients": clients}

    @_serialized_mutation
    def prepare_station(self):
        self._require_open()
        if not self._ap_is_active():
            raise NetworkPortError(ERROR_AP_REQUIRED)
        self.__station_prepared = False
        self.__sta_inactive = False
        # ESP32 MicroPython v1.28 rejects config(reconnects=0) while IF_STA is
        # inactive.  Activate and confirm first, then configure the driver's
        # reconnect policy before any connect() call is allowed.
        result = _driver_error(
            ERROR_STATION_PREPARE, self.__sta.active, True
        )
        _require_active_transition("station active(True)", result, True)
        self.__sta_inactive = False
        self._require_open()
        if not self._station_is_active():
            self._rollback_station_prepare()
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)

        memory_failed = False
        driver_failed = False
        contract_failed = False
        keyboard_interrupted = False
        system_exit = False
        try:
            result = self.__sta.config(
                reconnects=self.__station_reconnects
            )
        except MemoryError:
            memory_failed = True
        except KeyboardInterrupt:
            keyboard_interrupted = True
        except SystemExit:
            system_exit = True
        except Exception:
            driver_failed = True
        if not (
            memory_failed
            or driver_failed
            or keyboard_interrupted
            or system_exit
        ) and result is not None:
            contract_failed = True
        if (
            memory_failed
            or driver_failed
            or contract_failed
            or keyboard_interrupted
            or system_exit
        ):
            self._rollback_station_prepare()
            if memory_failed:
                raise MemoryError()
            if keyboard_interrupted:
                raise KeyboardInterrupt()
            if system_exit:
                raise SystemExit()
            if contract_failed:
                raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
            raise NetworkPortError(ERROR_STATION_PREPARE)

        self._require_open()
        self.__station_prepared = True
        self.__sta_inactive = False
        self.__sta_disconnected = True
        return None

    @_serialized_mutation
    def connect_station(self, profile_id, ssid, password):
        self._require_open()
        if not self.__station_prepared:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        if not self._ap_is_active():
            raise NetworkPortError(ERROR_AP_REQUIRED)
        if not self._station_is_active():
            self.__station_prepared = False
            raise NetworkPortError(ERROR_STATION_PREPARE)
        profile_id = _validate_profile_id(profile_id)
        ssid = _validate_ssid(ssid)
        password = _validate_password(password, True, True)
        self.__profile_id = profile_id
        self.__ssid = ssid
        # A connect callback can close the owner reentrantly and then resume.
        # Keep cleanup conservative until the returned operation is proven to
        # belong to an open port.
        self.__sta_disconnected = False
        try:
            if password is None:
                result = self.__sta.connect(ssid)
            else:
                result = self.__sta.connect(ssid, password)
        except MemoryError:
            memory_failed = True
            failed = False
        except Exception:
            memory_failed = False
            failed = True
        else:
            memory_failed = False
            failed = False
        password = None
        if memory_failed:
            # Keep NetworkManager's dedicated MemoryError path while ensuring
            # a driver cannot smuggle the station key into the exception.
            raise MemoryError()
        if failed:
            raise NetworkPortError(ERROR_CONNECT)
        _require_none(result)
        self._require_open()
        self.__sta_disconnected = False
        return None

    def station_status(self):
        self._require_open()
        active = self._station_is_active()
        if not active:
            return {
                "state": STA_IDLE,
                "raw_status": self.__idle_status,
                "connected": False,
                "profile_id": self.__profile_id,
                "ssid": self.__ssid,
                "ip": None,
                "gateway": None,
                "dns": None,
                "rssi": None,
                "mdns_ready": False,
            }
        raw_status = _driver_error(
            ERROR_STATION_STATUS, self.__sta.status
        )
        if type(raw_status) is not int:
            raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        connected = _driver_error(
            ERROR_STATION_STATUS, self.__sta.isconnected
        )
        connected = _require_boolean("station isconnected()", connected)

        normalized = self.__states.get(raw_status, STA_CONNECT_FAIL)
        ip = None
        gateway = None
        dns = None
        rssi = None
        if connected:
            # ``isconnected`` is the ESP32 driver's authoritative IP-level
            # truth.  Normalise a transiently stale raw status around it.
            normalized = STA_GOT_IP
            raw_ifconfig = _driver_error(
                ERROR_STATION_STATUS, self.__sta.ifconfig
            )
            ip, gateway, dns = _require_ifconfig(
                "station", raw_ifconfig
            )
            rssi = _driver_error(
                ERROR_STATION_STATUS, self.__sta.status, "rssi"
            )
            if type(rssi) is not int:
                raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
        elif normalized == STA_GOT_IP:
            normalized = STA_CONNECT_FAIL

        return {
            "state": normalized,
            "raw_status": raw_status,
            "connected": connected,
            "profile_id": self.__profile_id,
            "ssid": self.__ssid,
            "ip": ip,
            "gateway": gateway,
            "dns": dns,
            "rssi": rssi,
            "mdns_ready": bool(
                connected
                and raw_status == self.__got_ip_status
                and self.__hostname_configured
            ),
        }

    @_serialized_mutation
    def disconnect_station(self):
        self._require_open()
        if self._station_is_active():
            result = _driver_error(
                ERROR_DISCONNECT, self.__sta.disconnect
            )
            _require_none(result)
        self.__profile_id = None
        self.__ssid = None
        self.__sta_disconnected = True
        return None

    def deinit(self):
        """Close first, then authoritatively switch both WLAN singletons off."""

        global _WIFI_LEASED, _WIFI_LEASE_POISONED
        self.__closed = True
        self.__hostname_configured = False
        self.__station_prepared = False
        self.__profile_id = None
        self.__ssid = None
        nested_mutation = self.__mutation_active
        if self.__cleanup_finalized:
            return None
        # A fully released old port must never switch off a newer owner of the
        # process-wide WLAN singletons.  During a reentrant close of this same
        # port the nested cleanup has released the global lease, so the outer
        # finish path still reaches the authoritative physical recheck below.
        if not self.__lease_owner and _WIFI_LEASED:
            return None
        first_error = None
        # Cached flags are deliberately not used as a skip condition.  The
        # ESP32 WLAN objects are process-wide singletons and a suspended outer
        # driver callback may reactivate one after an inner cleanup returned.
        station_active = None
        try:
            station_active = self._station_is_active()
        except BaseException as error:
            first_error = error
        if station_active is True:
            try:
                result = self.__sta.disconnect()
                _require_none(result)
            except BaseException as error:
                if first_error is None:
                    first_error = error

        self.__sta_disconnected = False
        self.__sta_inactive = False
        try:
            result = self.__sta.active(False)
            _require_active_transition(
                "station active(False)", result, False
            )
            inactive = self.__sta.active()
            _require_active_transition(
                "station active() confirmation", inactive, False
            )
            self.__sta_inactive = True
            # An inactive STA cannot retain a live connection even when
            # disconnect() itself failed transiently.
            self.__sta_disconnected = True
        except BaseException as error:
            if first_error is None:
                first_error = error

        self.__ap_inactive = False
        try:
            result = self.__ap.active(False)
            _require_active_transition(
                "access point active(False)", result, False
            )
            inactive = self.__ap.active()
            _require_active_transition(
                "access point active() confirmation", inactive, False
            )
            self.__ap_inactive = True
        except BaseException as error:
            if first_error is None:
                first_error = error

        if self.cleanup_complete and not nested_mutation:
            if self.__lease_owner:
                _WIFI_LEASED = False
                _WIFI_LEASE_POISONED = False
                self.__lease_owner = False
            self.__cleanup_finalized = True
        elif not self.cleanup_complete and self.__lease_owner:
            _WIFI_LEASE_POISONED = True
        if first_error is not None:
            if isinstance(first_error, MemoryError):
                first_error = None
                raise MemoryError() from None
            if isinstance(first_error, KeyboardInterrupt):
                first_error = None
                raise KeyboardInterrupt() from None
            if isinstance(first_error, SystemExit):
                first_error = None
                raise SystemExit() from None
            raise NetworkPortError(ERROR_CLEANUP) from None
        return None


def _raw_interface_cleanup(access_point, station):
    def deactivate(interface, disconnect_first):
        if interface is None:
            return True
        for _ in range(2):
            active = getattr(interface, "active", None)
            if not callable(active):
                continue
            was_active = None
            try:
                was_active = _require_boolean(
                    "WLAN active()", active()
                )
            except BaseException:
                # Unknown state must not authorize disconnect(), but it must
                # not prevent the independent radio-off attempt below.
                pass
            if disconnect_first and was_active is True:
                try:
                    disconnect = getattr(interface, "disconnect", None)
                    if callable(disconnect):
                        _require_none(disconnect())
                except BaseException:
                    # active(False) is authoritative cleanup even if the
                    # driver's optional disconnect step fails.
                    pass
            try:
                _require_active_transition(
                    "WLAN active(False)", active(False), False
                )
                confirmed = active()
                _require_active_transition(
                    "WLAN active() confirmation", confirmed, False
                )
                return True
            except BaseException:
                pass
        return False

    station_ok = deactivate(station, True)
    access_point_ok = deactivate(access_point, False)
    return station_ok and access_point_ok


def _network_constant(network_module, wlan, name):
    value = getattr(wlan, name, None)
    if value is None:
        value = getattr(network_module, name, None)
    if type(value) is not int:
        raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
    return value


def open_wifi_from_board_config():
    """Lease the approved WLAN driver without activating either interface."""

    global _WIFI_LEASED, _WIFI_LEASE_POISONED
    import board_config

    board_config.require_wifi_configuration()
    if _WIFI_LEASED or _WIFI_LEASE_POISONED:
        raise NetworkPortError(ERROR_ALREADY_OWNED)
    country_code = board_config.WIFI_COUNTRY_CODE
    max_clients = board_config.WIFI_AP_MAX_CLIENTS
    station_reconnects = board_config.WIFI_STA_RECONNECTS

    try:
        import network
    except ImportError:
        raise NetworkPortError(ERROR_FACTORY) from None

    wlan = getattr(network, "WLAN", None)
    if not callable(wlan):
        raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
    country = getattr(network, "country", None)
    if not callable(country):
        raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
    memory_failed = False
    try:
        result = country(country_code)
        _require_none(result)
        confirmed_country = country()
    except MemoryError:
        memory_failed = True
    except NetworkPortContractError:
        raise
    except Exception:
        failed = True
    else:
        failed = False
    if memory_failed:
        raise MemoryError() from None
    if failed:
        raise NetworkPortError(ERROR_FACTORY)
    if type(confirmed_country) is not str or confirmed_country != country_code:
        raise NetworkPortContractError(ERROR_DRIVER_CONTRACT)
    ap_id = _network_constant(network, wlan, "IF_AP")
    sta_id = _network_constant(network, wlan, "IF_STA")
    wpa2 = _network_constant(network, wlan, "SEC_WPA2")
    status_values = (
        _network_constant(network, wlan, "STAT_IDLE"),
        _network_constant(network, wlan, "STAT_CONNECTING"),
        _network_constant(network, wlan, "STAT_WRONG_PASSWORD"),
        _network_constant(network, wlan, "STAT_NO_AP_FOUND"),
        _network_constant(network, wlan, "STAT_CONNECT_FAIL"),
        _network_constant(network, wlan, "STAT_GOT_IP"),
    )

    access_point = None
    station = None
    port = None
    memory_failed = False
    try:
        access_point = wlan(ap_id)
        station = wlan(sta_id)
        port = MicroPythonWiFiPort(
            network,
            access_point,
            station,
            max_clients,
            wpa2,
            status_values,
            station_reconnects=station_reconnects,
        )
        port._claim_lease()
        _WIFI_LEASED = True
        return port
    except BaseException as primary:
        _WIFI_LEASED = False
        if port is not None:
            cleanup_ok = False
            for _ in range(2):
                try:
                    port.deinit()
                except BaseException:
                    pass
                if port.cleanup_complete:
                    cleanup_ok = True
                    break
        else:
            cleanup_ok = _raw_interface_cleanup(access_point, station)
        if not cleanup_ok:
            _WIFI_LEASE_POISONED = True
            raise NetworkPortError(ERROR_FACTORY_CLEANUP) from None
        if isinstance(primary, MemoryError):
            # Factory construction has no useful OOM detail, and a vendor
            # message must never survive as a credential-bearing context.
            memory_failed = True
        else:
            raise
    if memory_failed:
        # Raise outside the handler so the original vendor error is not kept
        # in ``__context__`` on CPython or MicroPython.
        raise MemoryError()
