# DFR0975-U Phase 10.1 listener correction (2026-09-03)

## Result

The first Phase-10.1 application was correctly written and read back, but its
single wildcard HTTP listener was rejected by real-target evidence. Captive
DNS, station DHCP and mDNS worked. HTTP failed before accepting a request
because the pinned MicroPython ESP32 socket object has no `getsockname()` API.

## Real-target evidence from the rejected candidate

- application SHA-256:
  `e378b4874d162f84b224396463b5384da9a55fcdd36a119ccee08b52d6f959e0`;
- exact app readback matched;
- ESP32-S3, 16 MiB flash and 8 MiB Octal PSRAM confirmed;
- station DHCP address: `192.168.36.114`;
- `heater.local` resolved to the station address;
- captive DNS: 58 received, 58 answered, 0 ignored, 0 socket errors;
- HTTP: 0 accepted, 0 completed, 14 socket errors;
- last HTTP error: `accepted_socket_rejected`.

The source audit of MicroPython v1.28.0 commit
`e0e9fbb17ed6fd06bb76e266ae554784c9c80804` showed that the ESP32 port's
socket locals table exposes bind/listen/accept/connect/send/recv but no
`getsockname`. Header-based ingress inference was deliberately rejected.

## Corrective design

The corrected design retains one user-visible HTTP port but uses one explicit
listener per active interface:

- `192.168.4.1:80`, fixed trusted ingress `ap`;
- current station DHCP address on port 80, fixed trusted ingress `sta`;
- `192.168.4.1:53/UDP` for captive DNS.

No wildcard bind or accepted-socket local-address lookup remains. Both HTTP
listeners are bounded and are stepped fairly with DNS. Station requests
remain read-only regardless of their headers.

## Cleanup and privacy

After the failed candidate was diagnosed, the isolated A/B test configuration
and temporary board tools were removed. Both WLAN interfaces were explicitly
disabled. No home-network credentials were retained in the repository or in
the board test filesystem.

## Corrected candidate

- host tests: 1,135 passed;
- reproducibility: 15 of 15 build outputs byte-identical across two clean
  builds;
- application size: 2,058,400 bytes;
- application SHA-256:
  `a760f73722ea4f6c5f9a85842498092b628a6e33a186b5c62179d79a1697cd18`;
- target status: not flashed; new hash-bound approval required.
