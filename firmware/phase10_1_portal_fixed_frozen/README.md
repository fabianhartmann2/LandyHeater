# Portal-corrected Phase 10.1 frozen-module candidate

This directory defines the 44-file DFR0975-U N16R8 source closure after the
real-target portal response gate exposed two bounded wire-encoding omissions:
HTTP status 302 was absent from the encoder allowlist, and the application
duplicated the encoder-owned `Cache-Control` header. Separate AP- and
station-bound TCP listeners remain unchanged, both on user-visible port 80.

The ledger binds the current source bytes. `BUILD_INFO.md` records the exact
inputs, hashes, layout and remaining target gates. Nothing here authorizes or
performs a board flash. The earlier `phase10_1_fixed_frozen` candidate remains
retained as the exact historical image that exposed this issue.
