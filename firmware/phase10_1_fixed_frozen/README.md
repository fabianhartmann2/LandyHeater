# Corrected Phase 10.1 frozen-module candidate

This directory defines the corrected 44-file DFR0975-U N16R8 source closure
for captive portal and read-only station discovery. It replaces the rejected
wildcard-listener candidate with separate AP- and station-bound TCP listeners,
both on the user-visible HTTP port 80.

The ledger binds the current source bytes. `BUILD_INFO.md` records the exact
inputs, hashes, layout and remaining target gates. Nothing here authorizes or
performs a board flash.
