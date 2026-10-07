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
pinned inputs, two byte-identical canonical-path builds, artifact hashes,
layout and the remaining target gate. All offline gates passed on 2026-10-07.
No artifact in this directory authorizes a board flash. The retained app-only
image is 2,109,520 bytes with SHA-256
`f02d59e7d8c501c387e837cbbbc38ec9725387e8ac9db18c1395df084298ca3d`
for offset `0x10000` without a full erase.
