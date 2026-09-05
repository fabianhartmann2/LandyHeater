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
It has passed offline artifact checks but has not been flashed. The earlier
source-mounted runtime result does not authorize or substitute for the
remaining hash-bound flash, readback and frozen-runtime gate.
