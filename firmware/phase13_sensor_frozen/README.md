# Phase 13 DS18B20 frozen-module candidate

This directory defines the exact 47-file DFR0975-U N16R8 source closure for
the accepted Phase-11 product plus the explicit DS18B20 lifecycle owner. The
root `board_config.py` is frozen for the first time so the electrically
accepted GPIO4 approval is part of the inspectable application artifact rather
than supplied by a mounted development tree.

Construction and normal `boot.py`/`main.py` behavior remain passive. The
approved pin opens only when an owner explicitly calls
`ConfiguredSensorRuntime.start()`. UART, protocol TX, I2C and radio approvals
remain closed.

The ledger binds the exact current source bytes. `BUILD_INFO.md` records the
pinned inputs, two byte-identical canonical-path builds, artifact hashes,
layout, earlier source-mounted target evidence and the remaining target gate.
The retained candidate passed its offline gates, an authorized app-only flash,
independent readback and frozen sensor-runtime target gate. The live REST/UI
temperature gate remains. Nothing here authorizes another board flash.
