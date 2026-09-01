# DFR0975-U migration plan

## Decision and status

The selected successor to the DFR0654 is the **DFRobot FireBeetle 2
ESP32-S3-U, SKU DFR0975-U, module variant N16R8**. The board arrived and passed
the non-writing USB identity gate on 2026-09-01. The active `board_config.py`
now describes this exact received board with every electrical/radio approval
closed. A path-bound reproducible MicroPython S3 artifact set is retained
under `firmware/dfr0975u_n16r8/`. After a fresh hash-bound authorization, the
complete flash was erased and the verified combined image was written. Passive
MicroPython identity and separate USB-only GC, PSRAM, internal and
internal-DMA memory gates passed. Manual ROM recovery and isolated real VFS
A/B storage also passed; automatic USB control-line recovery is not reliable.
Functional radio and Phase-8 target acceptance remain open.

Official references:

- [DFRobot DFR0975-U board documentation](https://wiki.dfrobot.com/dfr0975-u/)
- [MicroPython ESP32_GENERIC_S3 builds](https://micropython.org/download/ESP32_GENERIC_S3/)
- [ESP32-S3-WROOM-1/1U datasheet](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf)
- [ESP-IDF external RAM guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/external-ram.html)

The evaluated alternatives and selection criteria are recorded in
`ESP32_BOARD_OPTIONS.md`.

## Received hardware identity (2026-09-01)

The received board was inspected with only USB and its external 2.4-GHz
antenna connected. Heater, vehicle power, UART, I2C, 1-Wire, RTC, sensors and
loopback wiring remained disconnected.

- physical SKU: `DFR0975-U`;
- PCB revision: `V1.0`;
- module marking: `ESP32-S3-WROOM-1U-N16R8`;
- external antenna: connected before any future radio test;
- USB identity: Espressif USB Serial/JTAG, VID `0x303a`, PID `0x1001`;
- ROM identity: ESP32-S3 QFN56, silicon revision 0.1, 40-MHz crystal;
- detected embedded PSRAM: 8 MB, 3.3-V interface;
- detected SPI flash: 16 MB, Quad-SPI mode.

The single ROM query used esptool.py 4.7.0 with `--no-stub flash-id`, followed
by a hard reset. It did not erase or write flash and did not start Wi-Fi,
Bluetooth or application GPIOs. Silicon revision 0.1 is not the PCB revision.
The physical module marking is the evidence for the `1U` external-antenna
variant; USB VID/PID alone cannot distinguish the DFRobot board SKU.

Sanitized evidence and the remaining non-claims are recorded in
`captures/2026-09-01-dfr0975u-usb-identity.md`.

## Why this board

The latest DFR0654 Phase-8 composition left only 32,880 bytes before the
listener's proof-before-listen gate and fell below the required 32 KiB at the
following checkpoint. Flash capacity alone would not fix that runtime limit.
The DFR0975-U combines 16 MB flash with 8 MB Octal PSRAM and retains the compact
FireBeetle form factor. Its external antenna connector also permits RF-safe
placement outside a vehicle or metal enclosure.

PSRAM is expected to move Python/product residency away from scarce internal
RAM. It is not an automatic guarantee for Wi-Fi/lwIP: DMA-constrained and
internal-only allocations still require adequate internal memory. The new
target must therefore pass the same real heap and network gates rather than
being accepted from specifications alone.

## Non-transferable DFR0654 state

The following must **not** be copied or flashed onto the ESP32-S3:

- the classic-ESP32 `ESP32_GENERIC` application or combined image;
- its bootloader or partition table;
- full-flash backups or rollback images;
- classic-ESP32 MPY/native artifacts or flash offsets;
- the DFR0654 UART/pin assumptions without new board verification.

Historical DFR0654 artifacts remain useful only as evidence and rollback for
that original board.

## Migration-gate status

| Gate | Status on 2026-09-01 |
| --- | --- |
| Physical SKU, V1.0 revision, 1U-N16R8 module, antenna, flash and ROM PSRAM identity | complete, read-only evidence retained |
| Confidential factory recovery image | complete: 16 MiB read plus independent device digest; owner-only outside Git |
| Active DFR0975-U profile and S3 GPIO allow/deny rules | complete; every approval remains `False` |
| Legacy DFR0654 preservation | validation branch retained; DFR0654-only RX/capture/loopback tools deliberately reject the active S3 profile until a later S3 UART gate |
| Phase-7/8 platform guards | bound to the exact custom MicroPython machine identity and S3 profile |
| MicroPython 1.28 S3/Octal-PSRAM build | complete; two clean canonical builds matched for 15/15 outputs |
| 16-MiB bootloader, partition table, app, combined image and hashes | retained, statically verified, fully flashed after exact approval and write-verified |
| PSRAM/internal memory | complete for idle USB-only gate: 8 MiB PSRAM, 8,216,128 B GC free, 274,191 B internal free and 266,475 B internal-DMA free; loaded WLAN gate remains later |
| Passive boot and exact custom MicroPython identity | complete; MicroPython 1.28.0 and exact machine string confirmed without soft reset |
| USB recovery | manual `BOOT`/`RST` ROM entry and physical-reset return complete; automatic USB control-line entry/return is unreliable and not accepted |
| VFS and isolated Phase-6 A/B storage | complete: full 12.9375-MiB VFS, bounded real write/readback, generation/recovery checks and exact cleanup |
| Functional radio and Phase-8 HTTP target gates | open |

The profile migration intentionally does not generalize the old
`rx_only_transport`, UART loopback or UART capture path by changing constants.
Those paths contain DFR0654-specific pin-neutralization assumptions and stay
fail-closed until a disconnected S3-specific UART/level-interface test is
designed and separately approved.

## Fail-closed V1.0 pin plan

The V1.0 schematic and the received board identity support the following
planned profile. These assignments document the intended route; they are not
electrically approved merely because they appear in source. Every peripheral
approval and protocol-transmit flag remains `False` until its separate
USB-only hardware gate succeeds.

| Function | ESP32-S3 GPIO | Board label | Initial state |
| --- | ---: | --- | --- |
| Heater UART2 TX | 14 | D10 | disconnected and disabled |
| Heater UART2 RX | 13 | D11 | disconnected |
| Heater TX buffer enable | 12 | D12 | unapproved; external pull-down required |
| DS3231 I2C1 SDA | 10 | A4 | unapproved |
| DS3231 I2C1 SCL | 11 | A5 | unapproved |
| DS18B20 1-Wire bus | 4 | A0 | unapproved |

The eventual heater TX interface must be a protected, tri-state-capable level
stage. GPIO12 is an active-high enable and requires a physical pull-down so
reset, boot and absent firmware keep the heater-facing output high impedance.
Software protocol TX remains a separate lock.

GPIO1/2 are deliberately not used for the RTC on V1.0 because they already
carry the onboard AXP313A power-management I2C bus. GPIO0/3/45/46 are
strapping-related; GPIO19/20 are native USB; GPIO26-37 belong to module
flash/Octal-PSRAM routing; GPIO43/44 are UART0/recovery; GPIO21 and GPIO47 are
the onboard LED and key. Camera/GDI/JTAG-only routes are also excluded from
the initial product profile.

## Firmware and memory gates

The custom MicroPython build target is `DFR0975U_N16R8`, derived from
`ESP32_GENERIC_S3` with Octal PSRAM. Its header declares 16 MiB flash. The
layout contains a 3-MiB factory app at `0x10000` and an explicit 12.9375-MiB
LittleFS VFS at `0x310000`; there is no OTA slot. The 1,964,688-byte app leaves
1,181,040 bytes, about 38%, in its slot. Exact inputs, hashes and the
path-bound A/B proof are in `firmware/dfr0975u_n16r8/BUILD_INFO.md`.

The resolved configuration enables 8-MiB Octal PSRAM at 80 MHz, performs the
boot memory test, fails if PSRAM is absent, prefers PSRAM for allocations over
8 KiB and reserves 32 KiB internally. The separately measured USB-only runtime
gate required:

- at least 32 KiB free MicroPython GC heap;
- at least 7 MiB and no more than the nominal 8 MiB registered PSRAM;
- at least 32 KiB free and a 32-KiB largest block in internal 8-bit heap;
- the same 32-KiB free/largest limits for DMA-capable internal 8-bit heap.

It passed with 8,216,128 bytes GC free, the complete 8-MiB PSRAM region,
274,191 bytes internal free and 266,475 bytes internal-DMA free. Both largest
internal blocks were 196,608 bytes. Exact first-flash and runtime evidence is
recorded in `captures/2026-09-01-dfr0975u-first-flash-memory-gate.md`.

The internal and DMA values must later be sampled again under the bounded
Phase-7/8 WLAN load; an idle-boot pass does not prove Wi-Fi/lwIP headroom.

## External antenna

Use a 2.4-GHz, 50-ohm antenna with a U.FL/IPEX/MHF-I-compatible connection.
Espressif's module datasheet recommends no more than 2.33 dBi gain when relying
on the module's existing certification basis. The antenna or a bulkhead SMA
connection should be strain-relieved; U.FL is not a service connector.

For installation, keep the antenna outside metal shielding or behind plastic
or glass, use the shortest practical coaxial cable and keep it away from the
DC/DC converter, relay/heater wiring and other switching-current paths. The
antenna improves RF placement but is unrelated to the Phase-8 heap failure.

## Safe bring-up order

1. USB only; heater, vehicle UART, I2C and 1-Wire disconnected.
2. Establish ROM download/recovery and read board identity before flashing.
3. Back up the new board's factory contents if useful for recovery.
4. Present the exact artifact/backup report and obtain a fresh hash-bound
   approval that names the complete first-flash operation; app-only is not a
   valid first-board path.
5. Flash only the newly approved S3 image and complete layout.
6. Confirm passive `boot.py`/`main.py`, PSRAM, heap, flash/VFS and both radios
   initially inactive.
7. Revalidate UART lock/loopback and RX-only neutralization with no heater.
8. Revalidate AP association and automatic DHCP.
9. Run the single-listener Phase-8 full-product acceptance exactly once.

Phase 8 passes only with a real complete HTTP 200 JSON response from
`GET http://192.168.4.1/api/v1/status`, every mandatory >=32-KiB checkpoint,
unchanged storage/heater safety and complete ordered cleanup. Phase 9 remains
blocked until that proof exists.

## Vehicle boundary

The development board is not an automotive power interface. A later vehicle
installation still requires a protected 12-V-to-5-V/3.3-V supply, reverse-
polarity and transient protection, appropriate UART level protection or
isolation, grounding/EMI review and mechanical strain relief.
