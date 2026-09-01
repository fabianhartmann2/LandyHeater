# Phase 9 frozen-module candidate

This directory defines the 42-file Phase-9 source closure for the DFR0975-U
N16R8 build. It extends the proven Phase-8 closure only with
`app/web_application.py` and generated `app/web_assets.py`; modifications to
existing modules are captured by `CURRENT_FROZEN_SOURCES.sha256`.

The retained binaries under `../dfr0975u_n16r8/` remain Phase-8 artifacts and
were not relabelled or overwritten. The separately retained Phase-9 candidate
is under `artifacts/`; its pinned inputs, A/B reproducibility proof, hashes,
layout and verification boundary are recorded in `BUILD_INFO.md`.

A Phase-9 rebuild shall use the pinned toolchain and board overlay documented
there, but pass this manifest:

```sh
make -C /private/tmp/landy-dfr0975u-s3-canonical/micropython/ports/esp32 \
  -j4 \
  BOARD=DFR0975U_N16R8 \
  FROZEN_MANIFEST=/private/tmp/landy-dfr0975u-s3-canonical/project/firmware/phase9_frozen/manifest.py
```

Before either reproducibility build, regenerate `app/web_assets.py` and verify
the source ledger. No command in this directory flashes or erases a board.
The retained combined image is not a flash authorization.
