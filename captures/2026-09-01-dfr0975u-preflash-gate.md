# DFR0975-U N16R8 preflash gate — 2026-09-01

## Status

**PASS for factory backup, fail-closed profile, static firmware artifacts and
the complete host regression. No flash is authorized.**

This record binds the received hardware identity, fail-closed product profile,
custom MicroPython target and retained artifacts before the first project
firmware write to the new board.

## Safety boundary

- USB data/power only;
- external 2.4-GHz antenna attached;
- heater, vehicle power, battery, UART, level interface, RTC, I2C, 1-Wire,
  sensors and loopback wiring disconnected;
- ROM identity and factory-backup operations read flash only;
- firmware creation and artifact inspection were offline;
- no REPL, filesystem, radio, GPIO, erase or flash-write operation;
- no Phase-7/8 target acceptance claim.

## Confidential factory recovery backup

The complete factory flash is stored outside the repository in an owner-only
directory; the random private path is intentionally not recorded in Git. The
directory and parent are mode `0700`; image and manifest are mode `0600`.

- range: `0x00000000` plus `0x01000000` bytes;
- image size: 16,777,216 bytes;
- image SHA-256:
  `1153302f315a21e868d9437950a985bcdf17c8471088e33d7f39487915b44886`;
- image MD5: `dc0953cd256f26394769fc882c6c53a8`;
- private manifest SHA-256:
  `fa8168f4878a1ac81f490002787eb3e0509ba057737fec4aeb369165dce2249f`.

esptool.py 4.7.0 used an ESP32-S3 RAM stub at 460800 baud for one successful
full read, followed by hard reset. A second independent read-only
`verify_flash` request made the board calculate the digest over the same full
16-MiB range; it returned `verify OK (digest matched)`, followed by another
hard reset. Earlier faster/incomplete attempts produced no accepted file.
There was no write or erase.

Offline checks found a valid ESP32-S3 bootloader and valid partition-table
MD5. The unchanged factory layout is:

| Partition | Offset | Size | Offline state |
| --- | ---: | ---: | --- |
| NVS | `0x9000` | `0x5000` | blank |
| OTA data | `0xE000` | `0x2000` | present as layout data |
| app0 | `0x10000` | `0x140000` | occupied factory app |
| app1 | `0x150000` | `0x140000` | blank |
| SPIFFS | `0x290000` | `0x170000` | blank |
| unassigned upper flash | `0x400000` | 12 MiB | blank |

No secret or filesystem contents were extracted or printed. The backup is a
recovery artifact, not permission to restore it.

## Bound hardware and profile

- board: DFRobot FireBeetle 2 ESP32-S3-U;
- SKU/revision: `DFR0975-U`, PCB `V1.0`;
- module marking: `ESP32-S3-WROOM-1U-N16R8`;
- ROM: ESP32-S3 revision 0.1, 40-MHz crystal;
- detected flash/PSRAM: 16 MiB Quad-SPI flash, 8 MiB embedded PSRAM;
- MicroPython target: `ESP32_GENERIC_S3`;
- custom build board: `DFR0975U_N16R8`, Octal-PSRAM configuration;
- expected runtime machine string:
  `DFRobot DFR0975-U N16R8 with ESP32S3`.

The active product routes are UART2 TX14/RX13, active-high TX gate GPIO12,
I2C1 SDA10/SCL11 and 1-Wire GPIO4. All pin approvals, the TX-gate approval,
protocol TX and Wi-Fi are `False`. The complete hardware guard also requires
the physical TX gate approval even while protocol TX remains disabled.

Historical DFR0654 validation remains available, but its RX-only, capture and
loopback tools reject the active S3 profile. They are not generalized until a
separate disconnected S3 UART/level-interface gate exists.

## Reproducible firmware evidence

Pinned inputs:

- MicroPython v1.28.0 commit
  `e0e9fbb17ed6fd06bb76e266ae554784c9c80804`;
- ESP-IDF v5.5.1 commit
  `fcae32885b0296b32044cb99ecbdc50d98dddb83`;
- project source commit
  `cb27b4f39954d44ed7553d13f63d6d5166129540`;
- Python 3.12.7, Espressif GCC 14.2.0, mpy-cross 1.28.0;
- `SOURCE_DATE_EPOCH=1788100339`, UTC/C locale, ccache disabled;
- exact retained ESP32-S3 managed-component lock.

The existing frozen-source ledger verified 40/40 project files. Two complete
clean builds ran sequentially at one fixed canonical absolute path because
ESP-IDF embeds an ELF-derived hash. All 15 compared outputs were byte-identical,
including bootloader, partition table, app, combined image, UF2, both
configurations, all generated flash-argument files, frozen C, ELF and map.

The repository retains the exact board overlay, dependency lock, resolved
configuration and required images under `firmware/dfr0975u_n16r8/`.

Main artifact results:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| bootloader | 19,232 B | `be074941dbcff048d22552208c318f53e9749142d77744ccfcad3744e5185985` |
| partition table | 3,072 B | `518d9ab5063af998cce24c461cfccc51ed7c6f4084c12e2fc93cd5bbb3ccf979` |
| application | 1,964,688 B | `917d8d28eaab54245fe0518606a0bc69a8f73f1fe66aab27ee6e1daa229b2291` |
| combined `firmware.bin` | 2,030,224 B | `4c0ed87982e8d643351034903acc0850acbceff6bcdceab622472eaf0c53b226` |

esptool 4.12 independently accepted both retained ESP32-S3 images, their
checksums and validation hashes. Offline partition parsing produced:

| Partition | Range | Size |
| --- | ---: | ---: |
| NVS | `0x009000–0x00F000` | 24 KiB |
| PHY | `0x00F000–0x010000` | 4 KiB |
| factory app | `0x010000–0x310000` | 3 MiB |
| LittleFS VFS | `0x310000–0x1000000` | 12.9375 MiB |

The app retains 1,181,040 bytes, about 38%, of slot reserve. There is no OTA
slot. The combined image contains bootloader, partition table and application
with `0xff` gaps, but no VFS bytes.

Retained `flash_args`/JSON paths match the packaged subdirectories. Generated
app-only and component-only argument files were intentionally omitted. A first
flash over unknown factory layout must not assume that its bootloader,
partition table, NVS or VFS is compatible.

## Separate memory proof

The final resolved configuration selects ESP32-S3, 16-MiB flash, Octal PSRAM
at 80 MHz, boot-time PSRAM initialization/memory test and fail-closed behavior
when PSRAM is missing. Allocations over 8 KiB prefer PSRAM and 32 KiB remain
reserved internally. Static linker headroom was 192,633 bytes DIRAM; this is
not runtime proof.

`tools/dfr0975u_memory_probe.py` is import-inert and requires an exact manual
token. After an approved flash, with radio/peripherals still locked, it must
separately check:

- exact MicroPython 1.28/S3 custom-machine and 16-MiB flash identity;
- at least 32 KiB free MicroPython GC heap;
- registered PSRAM between 7 MiB and the nominal 8 MiB;
- at least 32 KiB free and a 32-KiB largest block for internal 8-bit heap;
- the same limits for DMA-capable internal 8-bit heap.

The IDF capability masks are respectively `8BIT|SPIRAM`, `8BIT|INTERNAL` and
`8BIT|INTERNAL|DMA`. Internal/DMA headroom must later be sampled again under
the bounded real WLAN/product load.

## Independent review corrections

An independent read-only review found and the implementation corrected:

1. target runners now require the exact custom machine string rather than any
   machine name containing `esp32`;
2. runtime internal-memory gates now have 32-KiB limits and a separate DMA
   measurement;
3. packaged flash-metadata paths now resolve and misleading app-only first-
   flash arguments are absent;
4. complete hardware approval now includes the UART TX gate;
5. boolean gate level and unknown-profile fallbacks fail closed;
6. architecture, migration and phase documentation distinguish the active S3
   profile from deliberately historical DFR0654 diagnostics.

## Verification ledger

- profile/Phase-7/Phase-8/UART/memory focused suite: 99/99 passed;
- adapted legacy hardware-wrapper/DFR0654-loopback fixtures: 46/46 passed;
- retained firmware-package tests: 3/3 passed;
- frozen project closure: 40/40 hashes passed;
- artifact ledger: all retained files passed;
- final complete host regression: 1046/1046 passed;
- confidential factory backup and device digest: passed.

## Remaining boundary

No existing approval applies to this new board or image. The private factory
backup, static artifact gates and complete host regression have passed; the
final exact hash/operation report must now be presented for a fresh
authorization. The recommended first-project flash is a full-chip erase
followed by the complete compatible bootloader/partition/application image,
leaving a clean VFS; app-only flashing is not safe over the unverified factory
layout. That destructive scope must be named explicitly in the approval.

Only after that approved write may passive boot and the USB-only memory probe
run. Radio, storage, UART and Phase-8 acceptance remain separate later gates.
