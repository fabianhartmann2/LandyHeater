"""Origin, Host and ephemeral CSRF policy for the local REST listener.

This service performs no random or network operation at import or
construction time.  The owner explicitly calls :meth:`start` with an injected
hardware random provider before accepting HTTP traffic.  Mutation authority
is available only on a listener that was physically bound to the AP ingress;
an optional station listener remains read-only regardless of headers.
"""


CSRF_HEADER = "x-landy-csrf"
INGRESS_ACCESS_POINT = "ap"
INGRESS_STATION = "sta"
TOKEN_BYTES = 32
TOKEN_HEX_LENGTH = TOKEN_BYTES * 2
MAX_ALLOWED_HOSTS = 4
_HEX = "0123456789abcdef"


class RestSecurityError(RuntimeError):
    pass


class RestSecurityDenied(RestSecurityError):
    pass


class RestSecurityUnavailable(RestSecurityError):
    pass


def _normalize_configured_host(value):
    if type(value) is not str or not value or len(value) > 64:
        raise ValueError("REST host must be a bounded string")
    try:
        encoded = value.encode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("REST host must use ASCII")
    if value != value.strip() or b"\x00" in encoded:
        raise ValueError("REST host is malformed")
    normalized = value.lower()
    for character in normalized:
        if not (
            "a" <= character <= "z"
            or "0" <= character <= "9"
            or character in ".-"
        ):
            raise ValueError("REST host is malformed")
    if normalized.startswith((".", "-")) or normalized.endswith((".", "-")):
        raise ValueError("REST host is malformed")
    return normalized


def _normalize_request_host(value):
    if type(value) is not str or not value or len(value) > 80:
        raise RestSecurityDenied("request Host is invalid")
    try:
        value.encode("ascii")
    except (UnicodeError, ValueError):
        raise RestSecurityDenied("request Host is invalid")
    if value != value.strip():
        raise RestSecurityDenied("request Host is invalid")
    value = value.lower()
    if value.endswith(":80"):
        value = value[:-3]
    if ":" in value or "/" in value or "@" in value or "\\" in value:
        raise RestSecurityDenied("request Host is invalid")
    try:
        return _normalize_configured_host(value)
    except ValueError:
        raise RestSecurityDenied("request Host is invalid") from None


class RestSecurityPolicy:
    """Own one boot-ephemeral CSRF token and listener ingress policy."""

    __slots__ = (
        "__random_bytes",
        "__allowed_hosts",
        "__ingress",
        "__token",
        "__started",
        "__faulted",
        "__last_error",
        "__operation_active",
        "__operation_reentered",
    )

    def __init__(self, random_bytes, allowed_hosts, ingress):
        if not callable(random_bytes):
            raise ValueError("random_bytes must be callable")
        if (
            type(allowed_hosts) not in (list, tuple)
            or not allowed_hosts
            or len(allowed_hosts) > MAX_ALLOWED_HOSTS
        ):
            raise ValueError("allowed_hosts must be a bounded sequence")
        normalized = []
        for host in allowed_hosts:
            host = _normalize_configured_host(host)
            if host in normalized:
                raise ValueError("allowed_hosts must be unique")
            normalized.append(host)
        if ingress not in (INGRESS_ACCESS_POINT, INGRESS_STATION):
            raise ValueError("REST ingress is invalid")
        self.__random_bytes = random_bytes
        self.__allowed_hosts = tuple(normalized)
        self.__ingress = ingress
        self.__token = None
        self.__started = False
        self.__faulted = False
        self.__last_error = None
        self.__operation_active = False
        self.__operation_reentered = False

    @property
    def mutation_api_available(self):
        return (
            self.__started
            and not self.__faulted
            and self.__token is not None
            and self.__ingress == INGRESS_ACCESS_POINT
        )

    def _effective_ingress(self, ingress):
        if ingress is None:
            ingress = self.__ingress
        if ingress not in (INGRESS_ACCESS_POINT, INGRESS_STATION):
            raise RestSecurityDenied("request ingress is invalid")
        return ingress

    def _clear_token(self):
        if self.__token is not None:
            for index in range(len(self.__token)):
                self.__token[index] = 0
        self.__token = None

    def _begin_operation(self):
        if self.__operation_active:
            self.__operation_reentered = True
            self.__faulted = True
            self.__last_error = "rest_security_reentrancy_detected"
            raise RestSecurityUnavailable(
                "mutation security operation was re-entered"
            )
        self.__operation_active = True
        self.__operation_reentered = False

    def _finish_operation(self, primary_error):
        reentered = self.__operation_reentered
        self.__operation_active = False
        self.__operation_reentered = False
        if reentered:
            self._clear_token()
            self.__started = False
            self.__faulted = True
            self.__last_error = "rest_security_reentrancy_detected"
            if primary_error is None:
                raise RestSecurityUnavailable(
                    "mutation security operation was re-entered"
                )

    def start(self):
        self._begin_operation()
        primary_error = None
        try:
            if self.__started:
                return False
            self._clear_token()
            generated = None
            out_of_memory = False
            try:
                generated = self.__random_bytes(TOKEN_BYTES)
            except MemoryError:
                out_of_memory = True
            except BaseException:
                self.__faulted = True
                self.__last_error = "csrf_rng_failed"
                raise RestSecurityUnavailable(
                    "mutation security initialization failed"
                ) from None
            if out_of_memory:
                self.__faulted = True
                self.__last_error = "csrf_rng_out_of_memory"
                raise MemoryError() from None
            if type(generated) is not bytes or len(generated) != TOKEN_BYTES:
                self.__faulted = True
                self.__last_error = "csrf_rng_contract_failed"
                raise RestSecurityUnavailable(
                    "mutation security initialization failed"
                )
            self.__token = bytearray(generated)
            self.__started = True
            self.__faulted = False
            self.__last_error = None
            return True
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def deinit(self):
        self._begin_operation()
        primary_error = None
        try:
            self._clear_token()
            self.__started = False
            return None
        except BaseException as error:
            primary_error = error
            raise
        finally:
            self._finish_operation(primary_error)

    def _validated_host(self, headers, local_ip=None):
        if type(headers) is not dict:
            raise RestSecurityDenied("request headers are invalid")
        host = _normalize_request_host(headers.get("host"))
        if local_ip is not None:
            try:
                local_ip = _normalize_configured_host(local_ip)
            except ValueError:
                raise RestSecurityDenied("request local address is invalid") from None
        if host not in self.__allowed_hosts and host != local_ip:
            raise RestSecurityDenied("request Host is not allowed")
        return host

    @staticmethod
    def _origin_matches(headers, host, required):
        origin = headers.get("origin")
        if origin is None:
            if required:
                raise RestSecurityDenied("same-origin request is required")
            return True
        if type(origin) is not str:
            raise RestSecurityDenied("request Origin is invalid")
        if origin not in ("http://" + host, "http://" + host + ":80"):
            raise RestSecurityDenied("request Origin is not allowed")
        return True

    def validate_read(self, headers, ingress=None, local_ip=None):
        self._effective_ingress(ingress)
        host = self._validated_host(headers, local_ip)
        self._origin_matches(headers, host, False)
        return host

    def _token_matches(self, supplied):
        if type(supplied) is not str or len(supplied) != TOKEN_HEX_LENGTH:
            return False
        difference = 0
        try:
            supplied.encode("ascii")
        except (UnicodeError, ValueError):
            return False
        for index, byte in enumerate(self.__token):
            difference |= ord(supplied[index * 2]) ^ ord(_HEX[byte >> 4])
            difference |= ord(supplied[index * 2 + 1]) ^ ord(
                _HEX[byte & 0x0F]
            )
        return difference == 0

    def security_context(self, headers, ingress=None, local_ip=None):
        ingress = self._effective_ingress(ingress)
        self.validate_read(headers, ingress, local_ip)
        if not (
            self.__started
            and not self.__faulted
            and self.__token is not None
            and ingress == INGRESS_ACCESS_POINT
        ):
            raise RestSecurityUnavailable("mutation API is unavailable")
        characters = bytearray(TOKEN_HEX_LENGTH)
        for index, byte in enumerate(self.__token):
            characters[index * 2] = ord(_HEX[byte >> 4])
            characters[index * 2 + 1] = ord(_HEX[byte & 0x0F])
        return {
            "csrf_token": bytes(characters).decode("ascii"),
            "mutation_api_available": True,
        }

    def authorize_mutation(self, headers, ingress=None, local_ip=None):
        ingress = self._effective_ingress(ingress)
        host = self._validated_host(headers, local_ip)
        self._origin_matches(headers, host, True)
        if not (
            self.__started
            and not self.__faulted
            and self.__token is not None
            and ingress == INGRESS_ACCESS_POINT
        ):
            raise RestSecurityUnavailable("mutation API is unavailable")
        if not self._token_matches(headers.get(CSRF_HEADER)):
            raise RestSecurityDenied("CSRF token is invalid")
        return True

    def snapshot(self):
        return {
            "started": self.__started,
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "ingress": self.__ingress,
            "allowed_host_count": len(self.__allowed_hosts),
            "mutation_api_available": self.mutation_api_available,
            "operation_active": self.__operation_active,
        }
