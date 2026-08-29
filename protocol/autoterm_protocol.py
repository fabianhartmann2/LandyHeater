"""Hardware-independent Autoterm / Planar protocol primitives.

Only behaviour supported by the reference Node-RED flow is interpreted.
Unknown bytes and commands are retained in parsed frames rather than being
guessed.  Incoming CRC validity is reported globally; real INIT and STATUS
captures confirm their CRC representation, and safety consumers enforce it
for those known commands without discarding unobserved response types.
"""

import math

from .autoterm_frames import (
    FRAME_START,
    MAX_PROTOCOL_FRAME_LENGTH,
    RawFrameStreamParser,
)
from .crc16 import append_crc, crc16


DEVICE_CONTROLLER = 0x03
DEVICE_HEATER = 0x04

RESERVED_DEFAULT = 0x00

CMD_START = 0x01
CMD_SETTINGS = 0x02
CMD_SHUTDOWN = 0x03
CMD_INIT = 0x04
CMD_STATUS = 0x0F
CMD_TEMPERATURE = 0x11

HEATER_MODE_TEMPERATURE = 0x02
HEATER_MODE_POWER = 0x04

CONTROL_MODE_POWER = "power"
CONTROL_MODE_ROOF_TENT_TEMPERATURE = "roof_tent_temperature"
CONTROL_MODE_CABIN_TEMPERATURE = "cabin_temperature"

# Compatibility default derived from the one-second Node-RED heartbeat.
# Scheduling this report belongs to HeaterController in a later milestone.
DEFAULT_EXTERNAL_TEMPERATURE_INTERVAL_MS = 1000

HEATER_STATE_OFF = 0
HEATER_STATE_STARTING = 1
HEATER_STATE_RUNNING = 4
HEATER_STATE_SHUTTING_DOWN = 5
HEATER_STATE_TEMP_MONITORING = 6

COMMAND_NAMES = {
    CMD_START: "start",
    CMD_SETTINGS: "settings",
    CMD_SHUTDOWN: "shutdown",
    CMD_INIT: "init",
    CMD_STATUS: "status",
    CMD_TEMPERATURE: "temperature",
}

HEATER_STATE_NAMES = {
    HEATER_STATE_OFF: "off",
    HEATER_STATE_STARTING: "starting",
    HEATER_STATE_RUNNING: "running",
    HEATER_STATE_SHUTTING_DOWN: "shutting_down",
    HEATER_STATE_TEMP_MONITORING: "temp_monitoring",
}

CONTROL_MODE_TO_HEATER_MODE = {
    CONTROL_MODE_POWER: HEATER_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE: HEATER_MODE_TEMPERATURE,
    CONTROL_MODE_CABIN_TEMPERATURE: HEATER_MODE_TEMPERATURE,
}


def _require_byte(name, value):
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= 0xFF
    ):
        raise ValueError("{} must be an integer from 0 to 255".format(name))


def _require_integer(name, value, minimum, maximum):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("{} must be an integer".format(name))
    if not minimum <= value <= maximum:
        raise ValueError(
            "{} must be between {} and {}".format(name, minimum, maximum)
        )


def command_name(command):
    """Return a readable command name without rejecting unknown commands."""

    return COMMAND_NAMES.get(command, "unknown")


def heater_state_name(state):
    """Return a readable state name without guessing unknown state values."""

    return HEATER_STATE_NAMES.get(state, "unknown")


def protocol_mode_for_control_mode(control_mode):
    """Map one named application mode to its Autoterm protocol mode."""

    try:
        return CONTROL_MODE_TO_HEATER_MODE[control_mode]
    except (KeyError, TypeError):
        raise ValueError("unknown control mode: {}".format(control_mode))


def build_frame(
    command,
    payload=b"",
    device=DEVICE_CONTROLLER,
    reserved=RESERVED_DEFAULT,
):
    """Build one complete frame, including its high-byte-first CRC."""

    _require_byte("command", command)
    _require_byte("device", device)
    _require_byte("reserved", reserved)

    if isinstance(payload, (int, bool)):
        raise ValueError("payload must be a byte sequence, not a length")

    try:
        payload = bytes(payload)
    except (TypeError, ValueError):
        raise ValueError("payload must contain byte values")

    if len(payload) > 0xFF:
        raise ValueError("payload cannot exceed 255 bytes")

    header = bytes((
        FRAME_START,
        device,
        len(payload),
        reserved,
        command,
    ))
    return append_crc(header + payload)


def build_init_request():
    """Build the controller-to-heater initialization request."""

    return build_frame(CMD_INIT)


def build_status_request():
    """Build the controller-to-heater status request."""

    return build_frame(CMD_STATUS)


def build_shutdown_request():
    """Build the controller-to-heater controlled-shutdown request."""

    return build_frame(CMD_SHUTDOWN)


def build_start_power(power_level):
    """Build START for power mode using a level from 1 through 9."""

    _require_integer("power_level", power_level, 1, 9)
    payload = bytes((
        0xFF,
        0xFF,
        HEATER_MODE_POWER,
        0xFF,
        0x01,
        power_level,
    ))
    return build_frame(CMD_START, payload)


def build_start_temperature(target_temperature):
    """Build START for either application-level temperature mode.

    Roof-tent and cabin control both map to protocol mode ``0x02``.  The
    application chooses which physical sensor is subsequently reported.
    """

    _require_integer("target_temperature", target_temperature, 5, 30)
    payload = bytes((
        0xFF,
        0xFF,
        HEATER_MODE_TEMPERATURE,
        target_temperature,
        0x01,
        0x03,
    ))
    return build_frame(CMD_START, payload)


def build_start_for_mode(
    control_mode,
    target_temperature=None,
    power_level=None,
):
    """Build START from a named application-level control mode."""

    protocol_mode = protocol_mode_for_control_mode(control_mode)
    if protocol_mode == HEATER_MODE_POWER:
        return build_start_power(power_level)
    return build_start_temperature(target_temperature)


def encode_external_temperature(temperature):
    """Encode a sensor reading using the Node-RED ``Math.ceil`` behaviour.

    The known wire representation is one unsigned byte.  Negative values are
    rejected because their protocol encoding is still an open question.
    """

    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ValueError("temperature must be a number")
    if temperature < 0 or temperature > 0xFF:
        raise ValueError("temperature encoding is only known for 0..255")

    try:
        encoded = int(math.ceil(temperature))
    except (ValueError, OverflowError):
        raise ValueError("temperature must be a finite number")

    if not 0 <= encoded <= 0xFF:
        raise ValueError("temperature encoding is only known for 0..255")
    return encoded


def build_external_temperature(temperature):
    """Build the known one-byte external/panel temperature report.

    Negative-temperature encoding is not documented by the reference flow.
    Positive fractional readings preserve its explicit ceiling conversion.
    """

    encoded = encode_external_temperature(temperature)
    return build_frame(CMD_TEMPERATURE, bytes((encoded,)))


def parse_status_fields(raw):
    """Extract only status fields whose absolute offsets are documented."""

    raw = bytes(raw)
    result = {
        "voltage": None,
        "glow_plug_raw": None,
        "heater_state": None,
        "heater_state_name": "unknown",
        "fan_raw": None,
    }

    # Status offsets are absolute frame indexes, but the final two bytes are
    # CRC.  Compare against the declared payload boundary so a short response
    # can never expose CRC bytes as telemetry.
    if len(raw) >= 5:
        payload_end = min(5 + raw[2], max(5, len(raw) - 2))
    else:
        payload_end = 0

    if payload_end > 11:
        result["voltage"] = raw[11] / 10.0
    if payload_end > 13:
        result["glow_plug_raw"] = raw[13]
    if payload_end > 14:
        state = raw[14]
        result["heater_state"] = state
        result["heater_state_name"] = heater_state_name(state)
    if payload_end > 19:
        result["fan_raw"] = raw[19]

    return result


def parse_frame(raw):
    """Parse one complete frame and return a dictionary of known fields.

    A CRC mismatch sets ``crc_valid`` to ``False`` but does not discard the
    frame.  Consumers responsible for safety decisions must explicitly
    require a valid CRC once the RX behaviour has been confirmed on hardware.
    """

    try:
        raw = bytes(raw)
    except (TypeError, ValueError):
        raise ValueError("frame must contain byte values")

    if len(raw) < 7:
        raise ValueError("frame is too short")
    if raw[0] != FRAME_START:
        raise ValueError("invalid frame start marker")

    payload_length = raw[2]
    expected_length = payload_length + 7
    if len(raw) != expected_length:
        raise ValueError(
            "invalid frame length: expected {}, received {}".format(
                expected_length, len(raw)
            )
        )

    payload_end = 5 + payload_length
    received_crc = (raw[-2] << 8) | raw[-1]
    calculated_crc = crc16(raw[:-2])
    command = raw[4]

    frame = {
        "raw": raw,
        "device": raw[1],
        "payload_length": payload_length,
        "reserved": raw[3],
        "command": command,
        "command_name": command_name(command),
        "payload": raw[5:payload_end],
        "crc_received": received_crc,
        "crc_calculated": calculated_crc,
        "crc_valid": received_crc == calculated_crc,
    }

    if command == CMD_STATUS and raw[1] == DEVICE_HEATER:
        frame["status"] = parse_status_fields(raw)

    return frame


class FrameStreamParser:
    """Convenience parser returning semantically parsed frame dictionaries.

    Raw framing is delegated to ``RawFrameStreamParser``.  The production UART
    transport uses that raw framer directly; this combined class remains a
    convenience for protocol tests and non-transport callers.
    """

    def __init__(
        self,
        max_frame_length=MAX_PROTOCOL_FRAME_LENGTH,
        require_valid_crc_for_framing=False,
    ):
        if not isinstance(require_valid_crc_for_framing, bool):
            raise ValueError("require_valid_crc_for_framing must be boolean")

        validator = None
        if require_valid_crc_for_framing:
            validator = _frame_has_valid_assumed_crc

        self.raw_parser = RawFrameStreamParser(
            max_frame_length=max_frame_length,
            frame_validator=validator,
        )
        self.require_valid_crc_for_framing = require_valid_crc_for_framing

    @property
    def buffer(self):
        return self.raw_parser.buffer

    @property
    def max_frame_length(self):
        return self.raw_parser.max_frame_length

    @property
    def discarded_bytes(self):
        return self.raw_parser.discarded_bytes

    @property
    def rejected_candidates(self):
        return self.raw_parser.rejected_candidates

    def reset(self):
        """Clear buffered data and the discarded-byte counter."""

        self.raw_parser.reset()

    @staticmethod
    def _parse_all(raw_frames):
        return [parse_frame(raw_frame) for raw_frame in raw_frames]

    def feed(self, data):
        """Consume new UART bytes and return all newly completed frames."""

        return self._parse_all(self.raw_parser.feed(data))

    def recover_after_timeout(self):
        """Recover from a stalled incomplete candidate after RX timeout."""

        return self._parse_all(self.raw_parser.recover_after_timeout())


def _frame_has_valid_assumed_crc(raw):
    """Validate the currently assumed high-byte-first trailing RX CRC."""

    if len(raw) < 7:
        return False
    received_crc = (raw[-2] << 8) | raw[-1]
    return received_crc == crc16(raw[:-2])
