"""Concrete controller-facing service for the Autoterm protocol core.

The service translates named controller requests into canonical protocol
frames and delegates each frame to an injected transport exactly once.  It
does not open UART hardware, retry writes, correlate responses, or make heater
state decisions.

Only raw frame bytes are trusted on RX.  Metadata supplied by another layer is
discarded and rebuilt with :func:`protocol.autoterm_protocol.parse_frame`.
"""

from .autoterm_protocol import (
    CONTROL_MODE_POWER,
    build_init_request,
    build_shutdown_request,
    build_start_for_mode,
    build_status_request,
    parse_frame,
    protocol_mode_for_control_mode,
)


class AutotermProtocolServiceError(RuntimeError):
    """Raised when an injected transport violates the send contract."""


class AutotermProtocolTxDisabledError(AutotermProtocolServiceError):
    """Raised when a request reaches a deliberately RX-only service."""


_SERVICE_TRANSMIT_CAPABILITY = object()

# UARTTransport reads at most 512 bytes per production poll.  Even a stream of
# minimum-size seven-byte frames plus one recovered candidate remains below
# this cap.  Rejecting larger injected batches prevents unbounded parse-dict
# amplification on the ESP32.
_MAX_INBOUND_FRAMES_PER_POLL = 80


def _immutable_raw_frame(raw_frame):
    if isinstance(raw_frame, bytes):
        return raw_frame
    if isinstance(raw_frame, (bytearray, memoryview)):
        return bytes(raw_frame)
    raise ValueError("raw frame must be bytes-like")


class AutotermProtocolService:
    """Implement the narrow protocol port consumed by ``HeaterController``.

    The injected transport must provide ``poll(now_ms)``,
    ``send_frame(raw_frame)``, ``reset_rx()``, ``status()``, and ``deinit()``.
    The public constructor is TX-locked by default.  A successful authorized
    send returns the exact integer number of bytes accepted.  Transport
    exceptions deliberately propagate so the composition root or controller
    can fail closed.
    """

    __slots__ = (
        "__cleanup_complete",
        "__closed",
        "__deinit",
        "__drain_activity",
        "__poll",
        "__reset_rx",
        "__send_frame",
        "__status",
        "__transmit_capability",
    )

    def __init__(self, transport, _transmit_capability=None):
        poll = getattr(transport, "poll", None)
        send_frame = getattr(transport, "send_frame", None)
        reset_rx = getattr(transport, "reset_rx", None)
        status = getattr(transport, "status", None)
        deinit = getattr(transport, "deinit", None)
        drain_activity = getattr(transport, "drain_activity", None)
        if not callable(poll):
            raise ValueError("transport must provide poll()")
        if not callable(send_frame):
            raise ValueError("transport must provide send_frame()")
        if not callable(reset_rx):
            raise ValueError("transport must provide reset_rx()")
        if not callable(status):
            raise ValueError("transport must provide status()")
        if not callable(deinit):
            raise ValueError("transport must provide deinit()")
        if (
            _transmit_capability is not None
            and _transmit_capability is not _SERVICE_TRANSMIT_CAPABILITY
        ):
            raise ValueError("invalid service transmit capability")
        self.__poll = poll
        self.__send_frame = send_frame
        self.__reset_rx = reset_rx
        self.__status = status
        self.__deinit = deinit
        self.__drain_activity = (
            drain_activity if callable(drain_activity) else None
        )
        self.__transmit_capability = _transmit_capability
        self.__closed = False
        self.__cleanup_complete = False

    @property
    def closed(self):
        return self.__closed

    def _require_open(self):
        if self.__closed:
            raise AutotermProtocolServiceError("protocol service is closed")

    @staticmethod
    def parse_inbound_frame(raw_frame):
        """Parse one immutable copy of a complete raw transport frame."""

        return parse_frame(_immutable_raw_frame(raw_frame))

    def poll_inbound(self, now_ms=None):
        """Poll once and parse all complete raw frames in transport order."""

        self._require_open()
        raw_frames = self.__poll(now_ms)
        if not isinstance(raw_frames, (list, tuple)):
            raise AutotermProtocolServiceError(
                "transport poll must return a bounded frame sequence"
            )
        if len(raw_frames) > _MAX_INBOUND_FRAMES_PER_POLL:
            raise AutotermProtocolServiceError(
                "transport poll returned too many frames"
            )
        return [
            self.parse_inbound_frame(raw_frame)
            for raw_frame in raw_frames
        ]

    def transport_status(self):
        """Return a detached read-only snapshot of transport diagnostics."""

        status = self.__status()
        if not isinstance(status, dict):
            raise AutotermProtocolServiceError(
                "transport status must return a dictionary"
            )
        snapshot = dict(status)
        last_error = snapshot.get("last_error")
        if isinstance(last_error, dict):
            snapshot["last_error"] = dict(last_error)
        return snapshot

    def drain_activity(self, max_events):
        """Drain bounded transport copies for the separate diagnostics hub.

        Older injected transports without an activity queue remain valid and
        simply provide no protocol log.  The method never polls UART and never
        sends a frame.
        """

        if (
            type(max_events) is not int
            or max_events < 0
            or max_events > _MAX_INBOUND_FRAMES_PER_POLL
        ):
            raise ValueError("max_events is outside its bound")
        if self.__drain_activity is None:
            return []
        values = self.__drain_activity(max_events)
        if type(values) not in (list, tuple) or len(values) > max_events:
            raise AutotermProtocolServiceError(
                "transport activity drain is malformed"
            )
        return values

    def reset_inbound(self):
        """Explicitly reset only the transport RX/framing fault state."""

        self._require_open()
        self.__reset_rx()
        if self.transport_status().get("rx_faulted") is not False:
            raise AutotermProtocolServiceError(
                "transport remained RX-faulted after reset"
            )
        return True

    def validate_inbound_frame(self, frame):
        """Rebuild canonical frame metadata solely from ``frame['raw']``.

        Expected malformed-input errors return ``None`` so the controller can
        apply its bounded invalid-frame policy.  CRC mismatches remain a
        canonical parsed dictionary with ``crc_valid=False``; unknown commands
        and bytes are preserved.
        """

        if not isinstance(frame, dict):
            return None
        raw_frame = frame.get("raw")
        try:
            return self.parse_inbound_frame(raw_frame)
        except ValueError:
            return None

    def _send_once(self, raw_frame):
        self._require_open()
        if self.__transmit_capability is not _SERVICE_TRANSMIT_CAPABILITY:
            raise AutotermProtocolTxDisabledError(
                "protocol service TX is disabled"
            )
        raw_frame = _immutable_raw_frame(raw_frame)
        written = self.__send_frame(raw_frame)
        if (
            type(written) is not int
            or written != len(raw_frame)
        ):
            raise AutotermProtocolServiceError(
                "transport did not accept the complete frame"
            )
        return True

    def deinit(self):
        """Close immediately and retry transport cleanup until it succeeds."""

        self.__closed = True
        if self.__cleanup_complete:
            return
        self.__deinit()
        self.__cleanup_complete = True

    def request_initialization(self):
        """Request exactly one canonical INIT transmission."""

        return self._send_once(build_init_request())

    def request_status(self):
        """Request exactly one canonical STATUS transmission."""

        return self._send_once(build_status_request())

    def request_start(
        self,
        mode,
        target_temperature=None,
        power_level=None,
    ):
        """Request one validated START for an application-level mode."""

        # Resolve the mode before checking the mutually exclusive parameters
        # so unknown mode names are rejected centrally by the protocol layer.
        protocol_mode_for_control_mode(mode)
        if mode == CONTROL_MODE_POWER:
            if target_temperature is not None:
                raise ValueError(
                    "target_temperature is not valid in power mode"
                )
        elif power_level is not None:
            raise ValueError(
                "power_level is not valid in temperature mode"
            )

        raw_frame = build_start_for_mode(
            mode,
            target_temperature=target_temperature,
            power_level=power_level,
        )
        return self._send_once(raw_frame)

    def request_shutdown(self):
        """Request exactly one canonical controlled SHUTDOWN transmission."""

        return self._send_once(build_shutdown_request())
