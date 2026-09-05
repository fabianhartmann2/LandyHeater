# DFR0975-U Phase 10.1 portal response correction (2026-09-03)

## Real-target result

The listener-corrected image reached station DHCP and mDNS at
`192.168.36.114`. Captive DNS answered 5 of 5 phone requests without an error,
the AP listener accepted one connection, and the station listener remained
healthy. The AP response failed before sending with
`response_contract_failed`; cleanup disabled both WLAN interfaces and retained
only the isolated retry configuration.

The immediately preceding AP bind failure was caused by reopening the same
`192.168.4.1:80` endpoint directly after Setup Assistant teardown. A hard
restart cleared that TCP lifetime and both explicit listeners then started.

## Root cause and correction

The captive application returned HTTP 302 with a `Location` header. The
bounded HTTP encoder did not allow status 302. In addition, the application
supplied `Cache-Control: no-store` even though the encoder owns and always
adds that header. Either condition is correctly rejected as an invalid
response contract.

The correction:

- adds `302 Found` to the response-status allowlist;
- removes the duplicate application-level `Cache-Control` header;
- encodes every tested captive response through the real wire encoder and
  asserts exactly one `Cache-Control: no-store` line.

## Verification and next gate

- focused regression: 113 tests passed;
- complete functional host suite: 1,135 tests passed before adding the new
  artifact-version gates;
- final repository suite including the new version gates: 1,143 tests passed;
- frozen closure: 44 exact files;
- canonical-path reproducibility: 15 of 15 outputs byte-identical;
- application size: 2,058,368 bytes;
- application SHA-256:
  `b3f16a7e4160cdd2c58cf78d25c6ebb3377a7d0438b5384054d679c19c03ad8f`;
- target status: authorized app-only flash and independent full readback
  passed on 2026-09-05; combined runtime acceptance passed.

The previous listener-corrected candidate remains retained as historical
evidence.

## Authorized target flash evidence (2026-09-05)

- approval bound to SHA-256
  `b3f16a7e4160cdd2c58cf78d25c6ebb3377a7d0438b5384054d679c19c03ad8f`;
- target reconfirmed as ESP32-S3 revision 0.1, 8 MiB embedded PSRAM and
  16 MiB flash;
- app-only write at `0x10000`, 2,058,368 bytes;
- erased application range: `0x10000` through `0x206fff`;
- no full-chip erase; bootloader, partition table and VFS were not written;
- esptool transfer verification: passed;
- independent readback: 2,058,368 bytes from `0x10000`;
- readback SHA-256:
  `b3f16a7e4160cdd2c58cf78d25c6ebb3377a7d0438b5384054d679c19c03ad8f`;
- byte comparison with the retained application artifact: identical.

The board remained in the ROM bootloader after verification and then received
a normal physical reset.

## Combined real-target result (2026-09-05)

The single bounded gate reused the isolated configuration left intentionally
by the preceding failed attempt. It did not read credentials to the console
and never touched production configuration. The observed result was:

```text
PHASE10_1_DISCOVERY_READY_V1
sta_ip=192.168.36.114
dns_answered=10
captive_redirects=3
station_root_reads=1
station_security_denied=1
station_mutation_denied=1
memory_samples=5
cleanup_confirmed=True
PHASE10_1_DISCOVERY_PASS_V1
```

The phone connected to `Landy Heater` using the unchanged test password and
automatically opened the Web UI through the captive portal. The host-side
station checks independently received HTTP 200 for `/`, HTTP 503 for
`/api/v1/security-context`, and HTTP 403 for the rejected
`/api/v1/heater/stop` mutation. Thus both discovery paths and the station-side
mutation boundary were exercised in the same target run.

All five memory samples met the 32-KiB floors for MicroPython GC, internal and
DMA-capable heap. Requested State stayed false and the null heater protocol
recorded zero calls. The post-gate USB check found both WLAN interfaces
inactive, 8,319,216 bytes of GC memory free, and only `board_config.py`,
passive `boot.py` and the retained tools in VFS. The isolated configuration
and ledger were removed. Phase 10.1 is accepted on the DFR0975-U target.
