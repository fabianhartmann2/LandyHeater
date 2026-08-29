"""Lightweight public interface for the Autoterm protocol core.

Hardware transport modules are deliberately not imported here.  Callers that
need one must import ``protocol.uart_transport`` or
``protocol.rx_only_transport`` explicitly, avoiding unnecessary UART code in
hardware-independent application-state imports.
"""

from .autoterm_protocol import (
    CMD_INIT,
    CMD_SETTINGS,
    CMD_SHUTDOWN,
    CMD_START,
    CMD_STATUS,
    CMD_TEMPERATURE,
    CONTROL_MODE_CABIN_TEMPERATURE,
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
    DEFAULT_EXTERNAL_TEMPERATURE_INTERVAL_MS,
    DEVICE_CONTROLLER,
    DEVICE_HEATER,
    FRAME_START,
    FrameStreamParser,
    build_external_temperature,
    build_frame,
    build_init_request,
    build_shutdown_request,
    build_start_for_mode,
    build_start_power,
    build_start_temperature,
    build_status_request,
    encode_external_temperature,
    parse_frame,
    protocol_mode_for_control_mode,
)
from .autoterm_frames import MAX_PROTOCOL_FRAME_LENGTH, RawFrameStreamParser
from .crc16 import append_crc, crc16, crc_bytes

__all__ = (
    "CMD_INIT",
    "CMD_SETTINGS",
    "CMD_SHUTDOWN",
    "CMD_START",
    "CMD_STATUS",
    "CMD_TEMPERATURE",
    "CONTROL_MODE_CABIN_TEMPERATURE",
    "CONTROL_MODE_POWER",
    "CONTROL_MODE_ROOF_TENT_TEMPERATURE",
    "DEFAULT_EXTERNAL_TEMPERATURE_INTERVAL_MS",
    "DEVICE_CONTROLLER",
    "DEVICE_HEATER",
    "FRAME_START",
    "FrameStreamParser",
    "MAX_PROTOCOL_FRAME_LENGTH",
    "RawFrameStreamParser",
    "append_crc",
    "build_external_temperature",
    "build_frame",
    "build_init_request",
    "build_shutdown_request",
    "build_start_for_mode",
    "build_start_power",
    "build_start_temperature",
    "build_status_request",
    "crc16",
    "crc_bytes",
    "encode_external_temperature",
    "parse_frame",
    "protocol_mode_for_control_mode",
)
