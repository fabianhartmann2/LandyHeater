"""Bounded AP-only DNS responder used for captive-portal discovery.

Import and construction are inert.  ``start()`` opens one non-blocking UDP
socket on the explicit AP address.  Each ``step()`` consumes at most one
datagram and emits at most one reply; it never forwards traffic or stores the
queried name.
"""


DNS_PORT = 53
MAX_DNS_PACKET_BYTES = 512
DNS_TTL_SECONDS = 30
_WOULD_BLOCK_ERRNOS = (11, 35, 10035)


class CaptiveDNSError(RuntimeError):
    pass


def _canonical_ipv4(value):
    if type(value) is not str or not value or len(value) > 15:
        raise ValueError("AP address must be an explicit IPv4 address")
    parts = value.split(".")
    if len(parts) != 4:
        raise ValueError("AP address must be an explicit IPv4 address")
    octets = []
    for part in parts:
        if not part or (len(part) > 1 and part[0] == "0"):
            raise ValueError("AP address must be canonical IPv4")
        if any(character < "0" or character > "9" for character in part):
            raise ValueError("AP address must be an explicit IPv4 address")
        number = int(part)
        if number > 255:
            raise ValueError("AP address must be an explicit IPv4 address")
        octets.append(number)
    if octets[0] == 0 or octets[0] >= 224 or octets == [255, 255, 255, 255]:
        raise ValueError("AP address must be unicast IPv4")
    return value, bytes(octets)


def _default_socket_factory():
    import socket

    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _would_block(error):
    values = getattr(error, "args", ())
    return bool(values) and values[0] in _WOULD_BLOCK_ERRNOS


def _make_nonblocking(port):
    setter = getattr(port, "setblocking", None)
    if callable(setter):
        result = setter(False)
    else:
        setter = getattr(port, "settimeout", None)
        if not callable(setter):
            raise CaptiveDNSError("dns_socket_nonblocking_contract_failed")
        result = setter(0)
    if result is not None:
        raise CaptiveDNSError("dns_socket_nonblocking_contract_failed")


def _question_end(packet):
    if len(packet) < 17:
        raise ValueError("dns_query_malformed")
    flags = (packet[2] << 8) | packet[3]
    questions = (packet[4] << 8) | packet[5]
    if flags & 0x8000 or flags & 0x7800 or questions != 1:
        raise ValueError("dns_query_unsupported")
    offset = 12
    name_bytes = 0
    labels = 0
    while True:
        if offset >= len(packet):
            raise ValueError("dns_query_malformed")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0 or length > 63:
            raise ValueError("dns_query_malformed")
        if offset + length > len(packet):
            raise ValueError("dns_query_malformed")
        name_bytes += length + 1
        labels += 1
        if name_bytes > 253 or labels > 32:
            raise ValueError("dns_query_malformed")
        offset += length
    if labels == 0 or offset + 4 != len(packet):
        raise ValueError("dns_query_malformed")
    return offset + 4


def _response_for(packet, address_bytes):
    if type(packet) is not bytes or len(packet) > MAX_DNS_PACKET_BYTES:
        raise ValueError("dns_query_malformed")
    end = _question_end(packet)
    query_type = (packet[end - 4] << 8) | packet[end - 3]
    query_class = (packet[end - 2] << 8) | packet[end - 1]
    answer = query_type == 1 and query_class == 1
    header = bytearray(packet[:2])
    header.extend(b"\x81\x80\x00\x01")
    header.extend(b"\x00\x01" if answer else b"\x00\x00")
    header.extend(b"\x00\x00\x00\x00")
    header.extend(packet[12:end])
    if answer:
        header.extend(b"\xc0\x0c\x00\x01\x00\x01")
        header.extend(bytes((0, 0, 0, DNS_TTL_SECONDS, 0, 4)))
        header.extend(address_bytes)
    return bytes(header)


class MicroPythonCaptiveDNS:
    """Own one explicit AP-bound UDP/53 responder."""

    __slots__ = (
        "__ap_address",
        "__address_bytes",
        "__socket_factory",
        "__port",
        "__started",
        "__closed",
        "__faulted",
        "__last_error",
        "__operation_active",
        "__received",
        "__answered",
        "__ignored",
        "__socket_errors",
    )

    def __init__(self, ap_address, socket_factory=None):
        self.__ap_address, self.__address_bytes = _canonical_ipv4(ap_address)
        if socket_factory is None:
            socket_factory = _default_socket_factory
        if not callable(socket_factory):
            raise ValueError("socket_factory must be callable")
        self.__socket_factory = socket_factory
        self.__port = None
        self.__started = False
        self.__closed = False
        self.__faulted = False
        self.__last_error = None
        self.__operation_active = False
        self.__received = 0
        self.__answered = 0
        self.__ignored = 0
        self.__socket_errors = 0

    @property
    def started(self):
        return self.__started and self.__port is not None

    def start(self):
        if self.__closed:
            raise CaptiveDNSError("dns_closed")
        if self.started:
            return False
        port = None
        try:
            port = self.__socket_factory()
            for method in ("bind", "recvfrom", "sendto", "close"):
                if not callable(getattr(port, method, None)):
                    raise CaptiveDNSError("dns_socket_contract_failed")
            _make_nonblocking(port)
            if port.bind((self.__ap_address, DNS_PORT)) is not None:
                raise CaptiveDNSError("dns_bind_contract_failed")
        except MemoryError:
            if port is not None:
                try:
                    port.close()
                except BaseException:
                    pass
            raise
        except BaseException:
            if port is not None:
                try:
                    port.close()
                except BaseException:
                    pass
            self.__faulted = True
            self.__last_error = "dns_start_failed"
            raise CaptiveDNSError("dns_start_failed") from None
        self.__port = port
        self.__started = True
        self.__faulted = False
        self.__last_error = None
        return True

    def step(self):
        if not self.started or self.__operation_active:
            if self.__operation_active:
                self.__faulted = True
                self.__last_error = "dns_reentrancy_detected"
                raise CaptiveDNSError("dns_reentrancy_detected")
            return False
        self.__operation_active = True
        try:
            try:
                packet, peer = self.__port.recvfrom(MAX_DNS_PACKET_BYTES + 1)
            except OSError as error:
                if _would_block(error):
                    return False
                self.__socket_errors += 1
                self.__last_error = "dns_receive_failed"
                return False
            if type(packet) is not bytes or type(peer) not in (tuple, list):
                self.__ignored += 1
                return False
            self.__received += 1
            try:
                response = _response_for(packet, self.__address_bytes)
            except ValueError:
                self.__ignored += 1
                return False
            try:
                sent = self.__port.sendto(response, peer)
            except OSError as error:
                if _would_block(error):
                    return False
                self.__socket_errors += 1
                self.__last_error = "dns_send_failed"
                return False
            if sent != len(response):
                self.__socket_errors += 1
                self.__last_error = "dns_send_contract_failed"
                return False
            self.__answered += 1
            return True
        finally:
            self.__operation_active = False

    def deinit(self):
        port = self.__port
        self.__port = None
        self.__started = False
        self.__closed = True
        if port is not None:
            try:
                if port.close() is not None:
                    raise CaptiveDNSError("dns_close_contract_failed")
            except MemoryError:
                self.__port = port
                raise
            except BaseException:
                self.__port = port
                self.__faulted = True
                self.__last_error = "dns_close_failed"
                raise CaptiveDNSError("dns_close_failed") from None
        return None

    def snapshot(self):
        return {
            "started": self.started,
            "closed": self.__closed,
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "received": self.__received,
            "answered": self.__answered,
            "ignored": self.__ignored,
            "socket_errors": self.__socket_errors,
            "bind_address": self.__ap_address,
            "port": DNS_PORT,
            "max_packet_bytes": MAX_DNS_PACKET_BYTES,
        }
