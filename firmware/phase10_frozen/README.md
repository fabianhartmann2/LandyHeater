# Phase 10 frozen-module candidate

This directory defines the 42-file Phase-10 source closure for the DFR0975-U
N16R8 build. It retains the proven Phase-9 composition and adds the Setup
Assistant API and generated Phase-10 web assets. Phase-9 metadata and binaries
remain historical and unchanged.

`app/web_assets.py` and `CURRENT_FROZEN_SOURCES.sha256` were regenerated before
two clean, byte-identical reproducibility builds. `BUILD_INFO.md` records the
pinned toolchain, verification and exact retained hashes. The previously
retained credential-validation image was flashed only after a separate exact
hash-bound authorization and completely read back. The current retained image
corrects bodyless browser mutations, including timer deletion. It was likewise
flashed only after a new exact authorization, completely read back and passed
the resumed real-phone delete gate. Nothing in this directory itself flashes
or erases a board, and a retained image is never authorization.
