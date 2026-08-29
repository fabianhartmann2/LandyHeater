"""CRC helpers for the reverse-engineered Autoterm protocol.

The existing Node-RED controller uses a reflected CRC-16 with an initial
value of ``0xFFFF`` and polynomial ``0xA001``.  Unlike conventional Modbus
wire order, the resulting CRC is transmitted high byte first.

This module deliberately uses only language features available in both
CPython and MicroPython.
"""

CRC_INITIAL = 0xFFFF
CRC_POLYNOMIAL = 0xA001


def crc16(data):
    """Return the 16-bit Autoterm CRC for *data*.

    ``data`` may be ``bytes``, ``bytearray`` or any iterable yielding byte
    values.  Values outside the byte range are rejected instead of being
    silently truncated.
    """

    crc = CRC_INITIAL

    for value in data:
        if not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise ValueError("CRC input values must be bytes (0..255)")

        crc ^= value

        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ CRC_POLYNOMIAL
            else:
                crc >>= 1

    return crc & 0xFFFF


def crc_bytes(data):
    """Return the transmitted CRC bytes: high byte, then low byte."""

    value = crc16(data)
    return bytes(((value >> 8) & 0xFF, value & 0xFF))


def append_crc(data):
    """Return *data* followed by its two-byte Autoterm CRC."""

    frame = bytes(data)
    return frame + crc_bytes(frame)
