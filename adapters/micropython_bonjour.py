"""Lifecycle adapter for the fixed native Bonjour HTTP advertisement.

Import and construction perform no native import or network operation.  The
owner explicitly calls :meth:`start`, then passes the already-validated
station mDNS readiness truth to :meth:`update`.  The native module exposes no
generic service-registration surface: it can only advertise or remove the
fixed Landy Heater HTTP service.
"""


BONJOUR_INSTANCE = "Landy Heater"
BONJOUR_SERVICE = "_http._tcp"
BONJOUR_PORT = 80
BONJOUR_PATH = "/"


class BonjourHTTPError(RuntimeError):
    pass


def _load_native_module():
    import landy_mdns

    return landy_mdns


class MicroPythonBonjourService:
    """Own one fixed ``_http._tcp`` publication and its cleanup."""

    __slots__ = (
        "__native",
        "__started",
        "__closed",
        "__advertised",
        "__station_mdns_ready",
        "__faulted",
        "__last_error",
        "__operation_active",
        "__registrations",
        "__removals",
    )

    def __init__(self, native_module=None):
        self.__native = native_module
        self.__started = False
        self.__closed = False
        self.__advertised = False
        self.__station_mdns_ready = False
        self.__faulted = False
        self.__last_error = None
        self.__operation_active = False
        self.__registrations = 0
        self.__removals = 0

    @property
    def started(self):
        return self.__started

    @property
    def advertised(self):
        return self.__advertised

    def _native_method(self, name):
        method = getattr(self.__native, name, None)
        if not callable(method):
            raise BonjourHTTPError("bonjour_native_contract_failed")
        return method

    def _begin(self):
        if self.__operation_active:
            self.__faulted = True
            self.__last_error = "bonjour_reentrancy_detected"
            raise BonjourHTTPError("bonjour_reentrancy_detected")
        self.__operation_active = True

    def _finish(self):
        self.__operation_active = False

    def start(self):
        if self.__closed:
            raise BonjourHTTPError("bonjour_closed")
        if self.__started:
            return False
        self._begin()
        try:
            try:
                if self.__native is None:
                    self.__native = _load_native_module()
                self._native_method("advertise_http")
                self._native_method("remove_http")
            except MemoryError:
                self.__faulted = True
                self.__last_error = "bonjour_start_failed"
                raise
            except BaseException:
                self.__faulted = True
                self.__last_error = "bonjour_start_failed"
                raise BonjourHTTPError("bonjour_start_failed") from None
            self.__started = True
            self.__faulted = False
            self.__last_error = None
            return True
        finally:
            self._finish()

    @staticmethod
    def _require_native_result(result):
        if type(result) is not bool:
            raise BonjourHTTPError("bonjour_native_contract_failed")
        return result

    def update(self, station_mdns_ready):
        if type(station_mdns_ready) is not bool:
            raise ValueError("station_mdns_ready must be boolean")
        if not self.__started or self.__closed:
            return False
        if self.__faulted:
            raise BonjourHTTPError("bonjour_faulted")
        if (
            station_mdns_ready is self.__station_mdns_ready
            and station_mdns_ready is self.__advertised
        ):
            return False
        self._begin()
        try:
            self.__station_mdns_ready = station_mdns_ready
            if station_mdns_ready and not self.__advertised:
                try:
                    result = self._native_method("advertise_http")()
                    self._require_native_result(result)
                except MemoryError:
                    self.__faulted = True
                    self.__last_error = "bonjour_advertise_failed"
                    raise
                except BaseException:
                    self.__faulted = True
                    self.__last_error = "bonjour_advertise_failed"
                    raise BonjourHTTPError(
                        "bonjour_advertise_failed"
                    ) from None
                self.__advertised = True
                self.__registrations += 1
                return True
            if not station_mdns_ready and self.__advertised:
                try:
                    result = self._native_method("remove_http")()
                    self._require_native_result(result)
                except MemoryError:
                    self.__faulted = True
                    self.__last_error = "bonjour_remove_failed"
                    raise
                except BaseException:
                    self.__faulted = True
                    self.__last_error = "bonjour_remove_failed"
                    raise BonjourHTTPError("bonjour_remove_failed") from None
                self.__advertised = False
                self.__removals += 1
                return True
            return False
        finally:
            self._finish()

    def deinit(self):
        if self.__closed:
            return None
        if self.__operation_active:
            self.__faulted = True
            self.__last_error = "bonjour_reentrancy_detected"
            raise BonjourHTTPError("bonjour_reentrancy_detected")
        if self.__advertised:
            self._begin()
            try:
                try:
                    result = self._native_method("remove_http")()
                    self._require_native_result(result)
                except MemoryError:
                    self.__faulted = True
                    self.__last_error = "bonjour_cleanup_failed"
                    raise
                except BaseException:
                    self.__faulted = True
                    self.__last_error = "bonjour_cleanup_failed"
                    raise BonjourHTTPError("bonjour_cleanup_failed") from None
                self.__advertised = False
                self.__removals += 1
            finally:
                self._finish()
        self.__station_mdns_ready = False
        self.__started = False
        self.__closed = True
        return None

    def snapshot(self):
        return {
            "started": self.__started,
            "closed": self.__closed,
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "station_mdns_ready": self.__station_mdns_ready,
            "advertised": self.__advertised,
            "registrations": self.__registrations,
            "removals": self.__removals,
            "instance": BONJOUR_INSTANCE,
            "service": BONJOUR_SERVICE,
            "port": BONJOUR_PORT,
            "path": BONJOUR_PATH,
        }
