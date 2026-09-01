# DFR0975-U WLAN/DHCP gate — 2026-09-01

## Status

**PASS for the bounded Phase-7 phone AP association and DHCP gate on the
DFR0975-U N16R8.**

This was deliberately not an HTTP or Phase-8 product test. No listener or
socket was opened. Phase-8 single-listener acceptance remains open.

## Safety boundary

- USB power/data only;
- external 2.4-GHz antenna connected;
- heater, vehicle power, UART, RTC/I2C, 1-Wire, sensors and loopback wiring
  disconnected;
- exact custom machine identity required before radio activation;
- all persistent hardware and radio approvals remained fail-closed;
- the existing repository test credential was used unchanged and is not
  duplicated in this record;
- no flash erase, firmware write, VFS write, GPIO, UART, I2C, 1-Wire, HTTP,
  TCP or mDNS activity;
- one temporary in-memory AP approval, bounded join window and mandatory
  radio cleanup.

## Preflight

The 57 focused host tests for the Phase-7 network wrapper and phone AP runner
passed. The board reported the exact machine identity
`DFRobot DFR0975-U N16R8 with ESP32S3`, 8,319,952 bytes free GC heap and both
station and access-point interfaces inactive. No smoke-test file was present.

## Target result

The board exposed the WPA2 access point `Landy Heater` at `192.168.4.1` without
loading the HTTP stack. The phone associated, was observed as the sole client
and remained associated for the required 30-second stability interval. The
target then returned:

```text
PHASE7_PHONE_AP_CLIENT_SEEN_V1
clients=1
PHONE_CLIENT_CONFIRMED clients=1
radio_cleanup_confirmed=True
PHASE7_PHONE_AP_SMOKE_PASS_V1
```

While connected, the phone showed:

- IPv4 address: `192.168.4.2`;
- subnet mask: `255.255.255.0` (`/24`);
- router/DHCP gateway: `192.168.4.1`.

These values complete the DHCP evidence; association alone was not treated as
sufficient proof.

## Final state and boundary

The independent USB post-check reported station `False`, access point `False`,
8,320,320 bytes free GC heap and unchanged VFS geometry/free blocks:

```text
(4096, 4096, 3312, 3309, 3309, 0, 0, 0, 0, 255)
```

No repeat is required. The DFR0975-U functional WLAN/DHCP gate is complete.
The next network milestone is one separately authorized, bounded Phase-8
single-listener run on port 80 with exactly one real
`GET /api/v1/status` request, full JSON validation, memory/safety checks and
ordered cleanup.
