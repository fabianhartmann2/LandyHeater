"""Raw byte-stream framing for the Autoterm protocol.

This module knows how to find complete frames from their marker and declared
payload length.  It intentionally does not interpret commands or status
fields.  ``UARTTransport`` therefore uses it directly and passes raw frames to
the semantic protocol layer, as required by ``ARCHITECTURE.md``.
"""

FRAME_START = 0xAA
MAX_PROTOCOL_FRAME_LENGTH = 0xFF + 7


class RawFrameStreamParser:
    """Incrementally split an incoming byte stream into complete raw frames.

    ``recover_after_timeout()`` is the explicit recovery hook for a plausible
    but incomplete length header.  The UART transport calls it after its
    configured inter-byte timeout expires and no driver bytes are waiting.
    """

    def __init__(
        self,
        max_frame_length=MAX_PROTOCOL_FRAME_LENGTH,
        frame_start=FRAME_START,
        frame_validator=None,
    ):
        if not isinstance(max_frame_length, int) or max_frame_length < 7:
            raise ValueError("max_frame_length must be at least 7")
        if max_frame_length > MAX_PROTOCOL_FRAME_LENGTH:
            raise ValueError(
                "max_frame_length cannot exceed {}".format(
                    MAX_PROTOCOL_FRAME_LENGTH
                )
            )
        if (
            not isinstance(frame_start, int)
            or isinstance(frame_start, bool)
            or not 0 <= frame_start <= 0xFF
        ):
            raise ValueError("frame_start must be an integer from 0 to 255")
        if frame_validator is not None and not callable(frame_validator):
            raise ValueError("frame_validator must be callable or None")

        self.buffer = bytearray()
        self.max_frame_length = max_frame_length
        self.frame_start = frame_start
        self.frame_validator = frame_validator
        self.discarded_bytes = 0
        self.rejected_candidates = 0

    def reset(self):
        """Clear all buffered data and diagnostics counters."""

        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.rejected_candidates = 0

    def _discard_first_byte(self):
        del self.buffer[0]
        self.discarded_bytes += 1

    def _extract_frames(self):
        frames = []

        while True:
            while self.buffer and self.buffer[0] != self.frame_start:
                self._discard_first_byte()

            if len(self.buffer) < 3:
                break

            expected_length = self.buffer[2] + 7
            if expected_length > self.max_frame_length:
                self._discard_first_byte()
                continue

            if len(self.buffer) < expected_length:
                break

            candidate = bytes(self.buffer[:expected_length])
            if (
                self.frame_validator is not None
                and not self.frame_validator(candidate)
            ):
                # Keep the remaining candidate bytes in the buffer and only
                # reject its first marker.  This lets the next embedded 0xAA
                # become a fresh candidate instead of losing a valid frame.
                self.rejected_candidates += 1
                self._discard_first_byte()
                continue

            frames.append(candidate)
            del self.buffer[:expected_length]

        return frames

    def feed(self, data):
        """Consume UART bytes and return every newly completed raw frame."""

        if data:
            try:
                self.buffer.extend(data)
            except (TypeError, ValueError):
                raise ValueError("stream data must contain byte values")

        return self._extract_frames()

    def recover_after_timeout(self):
        """Abandon the current incomplete candidate and resynchronize.

        A header can contain a plausible but corrupted payload length.  The
        parser cannot safely reject it while bytes may still be arriving.
        Once the UART inter-byte timeout has expired, this method discards the
        candidate marker, searches for the next marker and returns any raw
        frames that are already complete behind it.
        """

        if not self.buffer:
            return []

        self._discard_first_byte()
        while self.buffer and self.buffer[0] != self.frame_start:
            self._discard_first_byte()

        return self._extract_frames()
