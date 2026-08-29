# Phase-8 REST USB-only smoke on DFR0654

Date: 2026-08-11  
Board: DFRobot FireBeetle 2 ESP32-E V1.0 / DFR0654  
Firmware: MicroPython 1.28.0, `ESP32_GENERIC`  
Compiler: official MicroPython v1.28.0 `mpy-cross`, mpy v6.3,
`-march=xtensawin`

## Safety scope

The run used an isolated `/phase8_usb_rest_mpy_v1` import prefix containing
only the hardware-free REST closure. It did not upload or modify `boot.py`,
`main.py`, `board_config.py`, `hardware/` or `protocol/`, did not import
`machine` or `network`, and used only in-memory fake listener/client sockets.
No Wi-Fi interface, GPIO, UART, heater, sensor or RTC path was opened.

The smoke exercised the production strict JSON and HTTP boundaries, the REST
security and rate-limit services, `RestApplication`, the configuration/manual
application gateways and the cooperative `MicroPythonHTTPServer`. Each of four
iterations performed:

1. one AP-peer security-context request;
2. one valid CSRF-protected STOP through `ManualControlGateway` with verified
   Requested OFF truth;
3. one deliberately unavailable START which remained fail-closed;
4. JSON/HTTP exact-bound checks and the one-socket-action-per-step invariant.

## Board discoveries before the final pass

The first confirmed run failed closed during import because the REST router
pulled the complete configuration/scheduler/time validation graph into RAM.
The production code was changed so configuration schema validation loads only
at an actual configuration write, while lightweight shared configuration
error types remain resident. The measured REST-smoke import then retained
more than the required 32 KiB.

The next run exposed a MicroPython-v1.28 difference: built-in exception types
do not expose the CPython-style `ValueError.__init__`/`Exception.__init__`
methods used by four custom exceptions. Those constructors now store only
their bounded fields and provide a fixed `__str__`; 148 focused host tests
remained green before repeating the board run.

Neither failed run touched hardware or emitted the pass token.

## Final output

```text
PHASE 8 USB REST PASS: iterations=4 requests=12 completed=12 peer_count=1 step_actions=1 heap=141152/43984/42272/42288
PHASE8_USB_REST_SMOKE_PASS_V1
```

Interpretation:

- 4/4 bounded iterations passed;
- 12/12 HTTP responses completed;
- the validated AP peer occupied exactly one bounded limiter entry;
- every server step performed at most one accept/receive/send action;
- free heap after import, warm-up and the measured run stayed above 32 KiB;
- Requested State was OFF and both server and CSRF authority were cleaned up
  before the final pass token.

## Cleanup and reset

The exact isolated import directory was removed after the run. A hardware
reset followed. The independent postflight result was:

```text
PHASE8_POSTFLIGHT 106016 False False False False False
```

The fields are free heap, STA active, AP active, RAM Wi-Fi approval, Wi-Fi
lease and Wi-Fi poison latch. Thus both radios and all three Wi-Fi guards were
False after reset; the normal safe-boot path remained passive.

## Source hashes used for the final run

```text
d9615fdfea89653852d444c976df66a518fcdc159d75dc185978aa5fccba966c  adapters/micropython_http_server.py
ce1469fbe34df84e42bf57ae5c700b815e9f3deea99ea3224803ec394a0038f5  app/configuration_api_gateway.py
a92464f2d5a47bc4549efa35d152092795a544a2deef4e7f598cd0fcd7e0e051  app/manual_control_gateway.py
3cbda36df382fe857b7c808c5585f7d40daa048f88152f0215a0cf2166d4be8f  app/rest_application.py
d62de6ecb03df3fe4f4b7ad38efd3937bbb4884a0be0421277d219ed6839f62d  app/rest_composition.py
724c2aff9046932fc9b5c21fa2a47580e5d420404ea92f5451c3992dd60ea612  services/configuration_errors.py
e7f5c872b24786e323f695e86ce7fd5aebae1f3fb94552f12b1394728e221620  services/http_protocol.py
7d59c9c9e619413051adda4bcc6b9fe749a59b0ef0112e641e1b9e4af0f67c6c  services/rest_rate_limiter.py
f918cd75d848102c9e6c89da5d061e62b388e8a3a55b9a03e3b76b2b4854229b  services/rest_security.py
46aa0a0e0391035f248012c909c3e01a87525664654e42288685df7a9378b9de  services/strict_json.py
d7f115d73eb84ab4c6851e81cf21db2072e06de24c1f09a26a78322cc46d2da8  tools/phase8_rest_smoke.py
```
