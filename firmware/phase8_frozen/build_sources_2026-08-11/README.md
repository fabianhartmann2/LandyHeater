# Frozen build source snapshot (2026-08-11)

This directory preserves the exact 40 project source files bound by
`../FROZEN_SOURCES.sha256` and used by the retained 2026-08-11 firmware
artifacts.

The snapshot was reconstructed byte-for-byte from the retained project
history and then verified 40/40 against the build ledger. Thirty-eight files
are identical in both the retained Phase-8 project archive and the current
tree. The historical `adapters/micropython_http_server.py` exists only in that
archive. The archive predates the build-time Binary32 compatibility correction
in `app/temperature_manager.py`; the corrected file was therefore taken from
the current tree. Both exceptional files match the original build ledger
exactly. The archive by itself verifies only 39/40 and is not presented as the
complete build source closure.

Verify the snapshot from this directory with:

```sh
shasum -a 256 -c ../FROZEN_SOURCES.sha256
```

These files are provenance material only. They are not imported by the
application and must not be mistaken for the current product sources. The
current top-level 40-file closure is bound separately by
`../CURRENT_FROZEN_SOURCES.sha256`.

The top-level `../manifest.py` deliberately resolves the normal current
project tree. To reproduce the historical build inputs, first make an isolated
temporary copy of the project, overlay these 40 files onto the corresponding
top-level package paths in that copy, verify the ledger there, and run the
unchanged manifest from that isolated tree. Do not point the historical build
at the live current tree.
