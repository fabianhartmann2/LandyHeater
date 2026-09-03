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
- target status: not flashed.

The previous listener-corrected candidate remains retained as historical
evidence. A new hash-bound app-only flash approval is required before the one
combined target gate can be repeated.
