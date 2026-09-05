# DFR0975-U Phase-13 sensor and RTC bring-up — 2026-09-05

## Scope and safety boundary

This bring-up used the physically confirmed DFR0975-U V1.0 N16R8 target,
powered only over USB. Heater, vehicle 12 V and UART remained disconnected;
both Wi-Fi interfaces remained inactive. The existing Phase-11 application
image was not reflashed. Hardware approvals were changed only in RAM for each
bounded probe and restored afterwards.

## DS3231M RTC result

The photographed Raspberry-Pi-oriented module contains a `DS3231M`. It was
wired at 3.3 V as follows:

| Module label | DFR0975-U route |
| --- | --- |
| `+` | `3V3` |
| `D` | A4 / GPIO10 / SDA |
| `C` | A5 / GPIO11 / SCL |
| `NC` | disconnected |
| `-` | `GND` |

The production DS3231 adapter successfully read address `0x68`. Its initial
status was control `0x1c`, status `0x88`: the oscillator was enabled but the
oscillator-stop flag made the old time untrusted. After explicit user
authorization, the staged/committed write path wrote and read back
`2026-09-05T12:47:49Z`; control remained `0x1c` and status became `0x08`.

After USB had been removed for more than 30 seconds and restored, a read again
failed closed with `ds3231_oscillator_stop_flag`; the confirmed status was
again `0x88`. The soldered backup cell had been stored for about five years
and is presumed depleted. Therefore:

- I2C1 on SDA10/SCL11, address `0x68`, DS3231M register reads, staged writes,
  readback and GPIO cleanup are electrically demonstrated;
- battery-backed retention is **not** accepted;
- the RTC is not a trusted offline time source and must be replaced or fitted
  with a verified correct backup cell before the final RTC gate;
- `I2C_PINS_APPROVED` remains `False` in the delivered board profile.

## DS18B20 result

The installed three-wire bus uses A0/GPIO4 and an external pull-up made from
two individually identified 10-kOhm resistors in parallel, approximately
5 kOhm, from DATA to 3V3.

An initial sensor was wired incorrectly, became hot, was disconnected and was
permanently retired. No result from that damaged part is accepted. With a new
sensor and the corrected, user-verified wire assignment, the production
MicroPython wrapper discovered three valid family-`0x28` ROMs and completed
one shared conversion plus three reads without scan, conversion, CRC, value or
bus errors:

| ROM ID | First valid sample |
| --- | ---: |
| `28159f270d000090` | 28.2500 °C |
| `286ed3bd0b000013` | 34.0625 °C |
| `28875f270d00006d` | 33.1875 °C |

Two one-cycle differential warming checks established the physical roles:

| Application role | ROM ID | Evidence |
| --- | --- | --- |
| `roof_tent` | `286ed3bd0b000013` | rose from 34.0625 °C to 35.1250 °C while both others fell |
| `cabin` | `28875f270d00006d` | rose from 32.8750 °C to 33.3750 °C while both others fell |
| `outside` | `28159f270d000090` | remaining unique ROM after both differential checks |

Every probe returned GPIO4 to `Pin.IN` without a pull, closed its temporary
approval and confirmed both radios inactive. The route, pull-up, discovery,
conversion, three reads and role mapping are accepted as the first Phase-13
DS18B20 electrical gate. Continuous runtime integration and failure injection
remain separate work; `ONEWIRE_PIN_APPROVED` therefore stays `False` in the
currently delivered firmware until that deliberate composition change is
built and hash-authorized.

## Persistent role assignment

The prior phone smokes had correctly removed their isolated test records, so
the real product configuration and scheduler ledger were both at first-boot
generation zero. The normal production `ConfigManager` provisioned both A/B
domains and committed only the three sensor assignments. Independent reload
confirmed:

- configuration generation `2` and scheduler-ledger generation `2`;
- all three role assignments exactly as listed above;
- `setup_complete=False`, zero timers and zero known networks;
- no radio activation and no disclosed or replaced credential.

The next setup run may add network and other user settings while preserving
these role assignments. Persisting identifiers does not itself open GPIO4 or
start sensor polling.

## Continuous product sensor-runtime gate

The first product-runtime attempts exposed an intermittent physical contact:
the DFR0975-U had been pressed onto a breadboard with an unsoldered header.
Depending on movement, the same guarded production scan saw either all three
sensors or none. This was not accepted as a software or electrical pass. The
header was then soldered, USB was restored and the owner confirmed that every
sensor remained cool.

Without reflashing the accepted Phase-11 image, the current source candidate
was mounted over USB and the normal production A/B configuration was loaded
read-only. `ConfiguredSensorRuntime` opened only the approved GPIO4 1-Wire
adapter and used the same generation-bound `TemperatureManager` instance that
feeds REST status and the Web UI. The bounded final run reported:

- three completed sampling cycles and nine valid readings;
- `roof_tent` (`286ed3bd0b000013`): `31.0000 °C`;
- `cabin` (`28875f270d00006d`): `29.2500 °C`;
- `outside` (`28159f270d000090`): `28.1875 °C`;
- zero scan, conversion, read, value, manager or bus-contract errors;
- unchanged production-storage signatures;
- both Wi-Fi interfaces inactive throughout;
- confirmed GPIO4 release after runtime cleanup.

The exact target token was `PHASE13_SENSOR_RUNTIME_PASS_V1`. This accepts the
continuous, configuration-bound USB sensor runtime and its handoff to the
shared temperature model. It does not claim a new frozen-image flash, a live
browser/API target gate, RTC retention, UART or heater integration.

## Frozen application candidate

The accepted source closure was then frozen with `board_config.py` and
`app/sensor_composition.py` included explicitly. Two clean builds at the same
canonical path matched byte-for-byte across all 15 retained and diagnostic
outputs. Esptool validated the ESP32-S3 application and unchanged bootloader;
the unchanged partition table retains the 3-MiB application slot and VFS at
`0x310000`.

The app-only candidate is 2,098,656 bytes, leaves 1,047,072 bytes in its slot,
and has SHA-256
`8bf1fd20446bdedb04afe40daefd65378c671430679ee2416566136454aa6e13`.
It passed offline artifact checks before the separately authorized target
write.

## App-only flash, readback and frozen runtime

The owner authorized the exact digest above for an app-only write at
`0x10000` without full-chip erase. Esptool wrote 2,098,656 bytes, verified the
write, and an independent read of the same range was byte-identical with the
same SHA-256. Bootloader, partition table and VFS were not written.

After manual reset, the passive target reported MicroPython 1.28.0, the exact
DFR0975-U N16R8 identity, 8,312,048 free heap bytes and both radios inactive.
The check also identified the older 14,758-byte `/board_config.py` retained in
VFS, SHA-256
`714725d51a9602d65bbcf74376c626059a9ec2d1ee9f101df828cb6fc6ea3356`.
It has the prior closed 1-Wire flag and shadows `.frozen` in the default path
order. No VFS mutation was authorized or performed.

The bounded runner placed `.frozen` first only in RAM and confirmed the new
frozen `board_config.py` and `app/sensor_composition.py`, with 1-Wire approved
and UART, protocol TX, I2C and radios closed. It then reported:

```text
cycles=3
valid_readings=9
roof_tent_c=29.6250
cabin_c=29.0625
outside_c=32.6250
storage_unchanged=True
radios_inactive=True
gpio4_released=True
PHASE13_SENSOR_RUNTIME_PASS_V1
```

This accepts the exact frozen sensor runtime. Any later automatic startup must
explicitly own frozen-module precedence or migrate the obsolete VFS board
profile under separate authority.

## Live REST/API and Web-UI sensor gate

A bounded phone runner then copied the trusted production configuration into
isolated A/B test stores, preserved the three assignments, started the real
`ConfiguredSensorRuntime`, REST runtime, Phase-9 Web application, one AP-bound
port-80 listener and captive DNS. It never dispatched a mutating request and
never accessed the heater protocol.

The first connection attempt reached the captive portal, but the test wrapper
still treated every browser-cancelled HTTP response as a fatal transport
result and consequently shut down its own AP. This was a test-harness defect,
not a password or DHCP failure. The wrapper was aligned with the already
accepted Phase-10 transport rule: only a fully accounted
`client_send_failed` caused by a browser cancelling a response is non-fatal;
latched server faults, parser errors, re-entry and unaccounted transport errors
still fail closed. Read-only `HEAD`/`OPTIONS` discovery is answered locally
without product dispatch, while write methods remain blocked before the
application.

With the unchanged SSID and password, the repeated run opened the captive
portal and loaded the real Heater UI. The UI displayed all three temperatures,
which the owner confirmed visually. The same run reported three valid status
responses:

```text
roof_tent_c=28.0000
cabin_c=27.5000
outside_c=32.0625
status_reads=3
production_storage_unchanged=True
isolated_files_removed=True
radio_sensor_http_cleanup=True
PHASE13_SENSOR_WEB_PHONE_PASS_V1
```

This closes the Phase-13 live DS18B20-to-temperature-manager-to-REST-to-Web-UI
gate. RTC battery retention, UART and heater integration remain separate and
open. No additional flash or production-storage mutation was performed.

## Recorded follow-up: live setup sensor assignment

The accepted Home UI refreshes role-based temperatures, but the Setup
Assistant's sensor-assignment step currently shows only ROM IDs from the one
`GET /api/v1/setup` performed when the dialog opens. It does not render the
`value_c`/health fields already present for assigned devices and does not
refresh setup sensor data. Unassigned device temperatures are also not yet
projected through that endpoint, although the bounded DS18B20 adapter already
retains a copied per-ROM `devices` snapshot for this purpose.

This is now an explicit Phase-13 follow-up requirement. The completed behavior
must:

- display ROM ID, current temperature and health together for every discovered
  device, including devices not assigned to a role;
- refresh at a bounded interval while the sensor-assignment step is visible,
  without reloading the page and without losing unsaved dropdown selections;
- obtain observations from the already-running sensor owner and never trigger
  scans, conversions or writes from an HTTP GET;
- show missing, stale and failed states explicitly and never invent a numeric
  replacement value;
- retain unique-role validation and defer every persistent change to the one
  final atomic setup commit;
- stop polling when the dialog/tab is hidden and preserve the existing
  fail-closed heater/protocol boundary.

Target acceptance requires all three physical ROMs and their live values to be
visible, two or more automatic refreshes, differential warming that identifies
at least one selected sensor, preserved unsaved selections, unchanged
production storage before the final commit, and complete sensor/HTTP/radio
cleanup. This requirement is documented only; it is not implemented or
accepted by the gate above.
