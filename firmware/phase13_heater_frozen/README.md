# Phase 13 heater activation frozen-module candidate

This directory defines the exact 48-file DFR0975-U N16R8 source closure for
the accepted Phase-13 sensor product plus the heater lifecycle owner, direct
UART transport and explicit activation profile. Its private `board_config.py`
differs from the safe repository default only by opening
`UART_PINS_APPROVED=True` and `UART_PROTOCOL_TX_ENABLED=True`.

Construction and normal `boot.py`/`main.py` behavior remain passive. UART opens
only when an explicitly invoked owner calls `ConfiguredHeaterRuntime.start()`.
The selected interface is the owner-approved direct level shifter; the absent
hardware gate remains `False`. I2C and radio approvals remain closed.

The ledger binds the exact current source bytes. `BUILD_INFO.md` records the
pinned inputs, two byte-identical canonical-path builds, artifact hashes and
layout. The retained candidate adds reboot-volatile browser time while keeping
the RTC path. Its offline gates passed, but it has not been authorized,
flashed or tested on the board. No artifact in this directory authorizes a
board flash. The app-only image is 2,112,512 bytes with SHA-256
`741ad9f13d106035d8ffe45ed0d84e92815e3d54396cf1f80400f7d349c27d18`
for a possible future write at offset `0x10000` without a full erase.
