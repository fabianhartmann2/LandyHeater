# Local OpenAPI and Settings explorer preflash gate

Date: 2026-11-22. Target: DFR0975-U N16R8 Landy Heater. No device write was
performed.

The REST implementation was reconciled into a local OpenAPI 3.0.3 document.
It describes 18 paths and 27 operations, including the canonical hex timer
resource and its three deprecated segment aliases. AP-only persistent
mutations and AP-and-station heater controls use distinct CSRF schemes; Origin,
generation ETags, query limits, rate limits and the STOP recovery exception are
explicit.

Settings now creates the documentation card dynamically. After the user opens
it, versioned local CSS and JavaScript are loaded and `/openapi.json` is fetched
with a plain same-origin request. The mobile dialog supports search, tag groups,
parameters, request bodies, responses and schemas. It has no CDN, iframe,
external URL, mutation client, token lookup or executable “Try it out” path.

The minified OpenAPI asset is 30,423 bytes identity and 4,881 bytes gzip. The
complete 14-asset bundle is 154,949 bytes identity and 43,691 bytes gzip, with
bundle version
`2e8af3591c875589432a7dbc9c33ecfeacb1cbc772948386fee889b7ff480c47`.

Contract, Web, cache, HTTP, frozen-source and artifact tests passed 96/96. The complete
suite ran 1,359 tests: 1,357 passed and only the two deliberately immutable
historical Phase-11 captive-DNS and early Phase-13 board-profile ledgers remain
different. Two clean builds at the pinned canonical path produced all 15
compared outputs byte-identically.

Retained application candidate:

```text
offset: 0x10000
size:   2245552 bytes
sha256: 824b63bc532413cee22bbdc4a33153c726bcc63a0c1b8e9e90ead758c083dee8
```

The combined image is 2,311,088 bytes with SHA-256
`1fe1bd81fb95ec9972830be93afd6cb970a5707949b0d54fd70243ac8eabef43`.
The 3-MiB app partition retains 900,176 bytes. This record does not authorize a
flash; the currently installed application remains `a4d5a2f1...` until an exact
new hash authorization is supplied.
