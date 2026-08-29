# Phase 8 – full REST target progress (2026-08-24/25)

## Status

The complete frozen `RestApplication` + configuration storage +
`ConfigManager` + NetworkManager + production HTTP adapter target acceptance
is still **open**. No production runtime or heater control was enabled, and
Phase 9 remains unreleased.

This record preserves the latest sanitized state before the board was
disconnected. It contains no WLAN password, CSRF token, private backup path or
raw request/header data.

## Firmware and source state

The HTTP adapter's write deadline was corrected so that the timeout starts
after the application handler and response encoder have finished, rather than
from the stale timestamp sampled before dispatch:

```text
adapters/micropython_http_server.py
9e2dab55cfef972b60795f1914d6a97b5b1e28b4392d0b7e52b12d52862554cd
```

An updated 1,971,296-byte factory application containing that source was
flashed app-only at `0x10000`, without erase and without writing the
bootloader or partition table. The complete application readback matched:

```text
81f46473f41ed3fbd28e6686adaf36fa4c0ef0fa9995c5d7e20c057f86ffd080
```

The updated binary is not retained in this repository. The binaries under
`firmware/phase8_frozen/artifacts/` remain the explicitly historical
2026-08-11 build. Its exact source closure is retained separately under
`firmware/phase8_frozen/build_sources_2026-08-11/`; the current closure is
bound by `CURRENT_FROZEN_SOURCES.sha256`.

The updated firmware's offline build gate passed 1,027/1,027 host tests at the
time it was built. Later diagnostic-only runner changes increased the current
repository suite without changing that retained binary.

## Six-module diagnostic runner

The latest source and official MicroPython v1.28.0 MPY 6.3/xtensawin artifact
identities were:

| Module | Source SHA-256 | MPY SHA-256 | MPY size |
| --- | --- | --- | ---: |
| coordinator | `3ecba0342674c1ec5995324f60c9d4d74f52fa50047a3016e4015523cc2a2b23` | `0a91abffef9b8b7f3bee23f6233b99cd0f0d7a7169a82ba66a2db17160562faa` | 9,622 B |
| stage 1 | `bdd2c180bb0d44291adfce3392a7ad0ef45f72edc801e2fa971f27c2eb3d74bf` | `24e4604c4d7518a535993d8cb6b5a638a31f66f9ea7d3019d27b57efe04758b7` | 8,247 B |
| stage-2 seam | `48dac579781f2a026ef28a3da3b3b4b26638e289f602205a4f1bb3df80a38445` | `76f6618522c2a87314222704101b580fbcbb64a18d35973ce944591361b98386` | 8,662 B |
| stage-2 prepare | `a12991c8e6708af165e03f263c5347371608e6aa8324cdff15ff27307a8284b3` | `0de8e0e21bab1b4148f17f38cfe5f55751267eedd37c430bdd0a34a2cc5453ef` | 8,707 B |
| stage-2 proof/serve | `b19729ff07f11d50c339006fff1ea4d671ddb670259b55fe74294c5750b203e8` | `c875210c17632dbb6b1a8747d8a430ff4ad349ecac7458d0ab64cf400ac23a7c` | 21,826 B |
| failure diagnostics | `a956ba9bb5eeb0decfbccbf3b8b0a9df463656fe15ceb01febd35d10e40cd2fe` | `bcee491ef6765d78682fff73ab31d8c18863bc1be4a6ab2573e799df24da5edb` | 4,028 B |

The final host state passed 30/30 focused full-REST runner tests, 52/52 HTTP
adapter tests and 1,034/1,034 complete host tests. The full-runner test source
was bound by:

```text
2268b15a77c090e0bc06ed55771901762744c79b08f5f9d289ba802a3ab14f95
```

## Board evidence and failure boundary

The stage-1 link probe succeeded with an automatic phone address on the AP
subnet and returned the exact read-only JSON result from
`http://192.168.4.1:8080/api/v1/phase8-link-check`:

```json
{"api_version":1,"ip_check":{"ap_peer_validated":true,"full_product_loaded":false,"result":"ok"},"phase":8}
```

This proves the same live AP could carry IPv4/TCP before the heavy product
composition. In one subsequent full-product run the production port-80
listener bound successfully, with 60,960 bytes free before HTTP start and
35,136 bytes after bind. The first final listener connection did not reach the
REST handler: server evidence was `accepted=0`, `socket_errors=1`, with the
response observer still clean. That localized the failure to the raw accept
path but did not preserve the numeric socket error.

The next runner revision added bounded, secret-free accept error
classification. During its board attempt the stage-1 `:8080` page loaded, but
the phone then left the WLAN before the final route; therefore it produced no
new accepted-error value and no full-product pass.

At every completed attempt, HTTP, REST security and radio cleanup remained
ordered and fail-closed; the normal boot path remained passive. These facts do
not satisfy the final acceptance requirement. A future run must still produce
the exact final post-cleanup pass token while every mandatory heap, storage,
security, heater-OFF, protocol-tripwire, route-wire and cleanup gate holds.

## Next safe action

No board action is pending while the board is disconnected. On continuation,
start from a passive USB-only baseline, use the exact pinned runner artifacts
or rebuild them from the recorded sources, verify their hashes, and perform
one bounded diagnostic run. Do not connect the heater, UART, I2C or 1-Wire and
do not relax the 32-KiB acceptance gates.
