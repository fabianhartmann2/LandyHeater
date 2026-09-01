# DFR0975-U USB recovery and VFS/storage gate — 2026-09-01

## Status

**PASS for manual ROM USB recovery, return to the exact project firmware,
LittleFS capacity, isolated Phase-6 A/B persistence and cleanup.**

Automatic entry/return through USB control-line reset is not reliable on this
received board. The documented recovery procedure therefore requires the
physical `BOOT` and `RST` buttons. Functional radio, S3 UART/level interface,
product peripherals and Phase-8 HTTP acceptance remain open.

## Safety boundary

- USB power/data only;
- external 2.4-GHz antenna connected;
- heater, vehicle power, UART, RTC/I2C, 1-Wire, sensors and loopback wiring
  disconnected;
- no flash erase, firmware write, GPIO, UART, I2C, 1-Wire or radio activation;
- storage writes restricted to the six exact paths
  `/phase6_usb_config_smoke_v1_{config,ledger}.{a,b,tmp}`;
- no production credential or persistent product configuration used.

## Initial state

The board returned the exact machine identity
`DFRobot DFR0975-U N16R8 with ESP32S3`. Both station and access-point
interfaces were inactive. None of the six reserved smoke paths existed.

`os.statvfs("/")` reported 4,096-byte blocks, 3,312 total blocks and 3,309
free/available blocks. This is the complete 13,565,952-byte (12.9375-MiB) VFS
partition with 13,553,664 bytes initially available; the 12,288-byte
difference is filesystem overhead.

## USB recovery result

Two automatic esptool 4.12.0 connection attempts from the running firmware,
using first the default control-line reset and then the native USB reset mode,
returned no ROM serial data. They performed no read, erase or write. A physical
`RST` press returned immediately to the unchanged MicroPython project
firmware.

The owner then entered download mode manually by holding `BOOT`, pressing and
releasing `RST`, and releasing `BOOT`. A read-only esptool connection with no
additional reset succeeded and confirmed:

- ESP32-S3 QFN56 revision 0.1;
- 40-MHz crystal and native USB-Serial/JTAG;
- 8-MiB embedded PSRAM;
- 16-MiB Quad-SPI flash at 3.3 V.

No stub, erase or write was used. The requested automatic USB-RTS hard reset
after the ROM query again did not restore an answering REPL; one physical
`RST` press did. The final firmware identity was exact. This proves a manual
USB recovery path, not unattended automatic recovery.

## VFS and Phase-6 storage result

The existing hardware-independent Phase-6 runner passed all 7 focused host
tests before use. It imports no `machine`, `board_config`, hardware or protocol
module and uses the frozen application/storage closure already present in the
verified firmware.

The first target invocation completed its cleanup but exceeded the host
helper's 10-second output timeout, so no pass claim was taken from it. The VFS
and all six reserved paths were verified clean before one necessary repeat
with a 60-second output window. That final minimum one-iteration run returned:

```text
PHASE 6 USB-ONLY CONFIG SMOKE PASS: 1/1
configuration_generation=2
ledger_generation=4
flash_config_writes=2
flash_ledger_writes=4
memory_before=8201232
memory_after_import=8201296
memory_after_warmup=8196928
memory_after=8197440
PHASE6_USB_CONFIG_SMOKE_PASS_V1
```

The runner proved a real VFS A/B write/readback, canonical record validation,
configuration and safety-ledger generations, durable timer consumption and
manual override, manager reconstruction, fail-closed damaged-newest-slot
behavior in its isolated memory stage, bounded heap recovery and final cleanup.

## Final state

After cleanup and a soft reboot, then again after the manual recovery return:

- all six reserved smoke paths were absent;
- VFS geometry and free block count exactly matched the initial state;
- the exact DFR0975-U MicroPython identity returned;
- station and access-point interfaces were both inactive.

No further board write or test is authorized by this record. The next gates
are separately bounded S3 UART/level-interface work and functional WLAN/DHCP,
followed later by the single-listener Phase-8 product acceptance.
