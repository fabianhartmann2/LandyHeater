# Safe API console and cURL examples preflash gate

Date: 2026-11-22. Target: DFR0975-U N16R8 Landy Heater. No device write was
performed for this candidate.

The local OpenAPI explorer now exposes a per-operation `Example & test`
control. Opening the documentation, expanding an operation or rendering a
cURL example performs no API request. The executable console is a separate
local versioned asset and is loaded only after the explicit control is used.
It has no external dependency and uses text nodes rather than executable HTML.

Every browser request is constrained to the current origin and an `/api/v1/`
path. Reads run only after the Execute button is pressed. Every mutation also
requires an acknowledgement checkbox; configured Start and Quick Start require
the exact additional word `START`. Persistent settings and timer mutations
have separate warnings, and Setup starts with an intentionally incomplete,
warned body. Mutations are never retried automatically. Bodyless Stop,
Capture-Stop and Timer-Delete requests send neither a body nor a JSON content
type.

Immediately before a confirmed mutation, the console obtains only the
protection values required by that operation: the AP or control CSRF context,
the current configuration ETag and, for heater revision-fenced requests, the
current heater request revision. Those values are not acquired while browsing
the documentation. cURL examples use `location.origin`, shell-safe quoting and
plain placeholders rather than live tokens. Their prerequisite commands show
where to read the CSRF token, ETag and heater revision. Copying has a textarea
fallback for HTTP-served browsers without secure Clipboard API access.

The Web bundle contains 15 assets. Its version is
`66178bd9a37ee7dd69af1e7b4a76d4da1a833254016bb2703a0f954bf29558bb`.
The 174,416 identity bytes compress deterministically to 49,613 gzip bytes;
the minified `/openapi.json` is 30,552 identity bytes and 4,928 gzip bytes.
The API console source is 15,499 bytes, below the 16-KiB frozen asset limit.

The focused OpenAPI, Web, static-cache, HTTP, Phase-13 frozen-source and
artifact matrix passed 97/97 tests. The complete repository suite ran 1,360
tests: 1,358 passed. The only failures are the two already documented immutable
historical Phase-11 captive-DNS and early Phase-13 sensor-board source ledgers.
JavaScript syntax checks, frozen source and artifact ledgers, ESP32-S3 image
checks and `git diff --check` passed.

Two clean builds at the pinned canonical path produced all 15 compared outputs
byte-identically. The retained application candidate is:

```text
offset: 0x10000
size:   2271296 bytes
sha256: 0711bbe572c9f1e00fee1af5c055961eac134e2649f8b03a1ef83e0670a5cd07
```

The combined image is 2,336,832 bytes with SHA-256
`274dc1a3c7349c7a87b80df7222851d77f4d2637898010dbe44a76aafc1fa8ff`.
The 3-MiB factory application partition retains 874,432 bytes. The current
installed application remains the previously authorized and read-back-verified
`824b63bc532413cee22bbdc4a33153c726bcc63a0c1b8e9e90ead758c083dee8`
image. This record does not authorize a flash; an exact new hash authorization
is required before any device write.
