# DFR0975-U migration plan

## Decision and status

The selected successor to the DFR0654 is the **DFRobot FireBeetle 2
ESP32-S3-U, SKU DFR0975-U, module variant N16R8**. The board has been selected
but has not yet arrived or been validated. The current `board_config.py`,
firmware and safety evidence therefore continue to describe the DFR0654.

Official references:

- [DFRobot DFR0975-U board documentation](https://wiki.dfrobot.com/dfr0975-u/)
- [MicroPython ESP32_GENERIC_S3 builds](https://micropython.org/download/ESP32_GENERIC_S3/)
- [ESP32-S3-WROOM-1/1U datasheet](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf)
- [ESP-IDF external RAM guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/external-ram.html)

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

## Required implementation work

1. Verify the received SKU, hardware revision, `ESP32-S3-WROOM-1U-N16R8`
   module, flash size, PSRAM size/mode and external antenna path.
2. Add a separate DFR0975-U board profile; preserve DFR0654 support.
3. Replace DFR0654-only guards in board configuration, RX-only transport,
   protocol capture, loopback tools, target runners and tests with explicit
   profile-aware validation.
4. Select and validate new 3.3-V GPIOs for UART TX/RX, I2C SDA/SCL and 1-Wire.
   Avoid USB, boot-strapping, flash/PSRAM-reserved and board-reserved pins.
5. Build MicroPython 1.28 for `ESP32_GENERIC_S3` using the Octal-SPIRAM
   (`spiram-oct`) variant and the existing frozen project closure.
6. Generate and pin a new S3 bootloader, partition table, application,
   combined image, rollback image, hashes, sizes and flash commands.
7. Prove PSRAM detection/use and retain explicit internal/native memory
   headroom for Wi-Fi/lwIP.

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
4. Flash only the newly verified S3 image using its generated layout.
5. Confirm passive `boot.py`/`main.py`, PSRAM, heap, flash/VFS and both radios
   initially inactive.
6. Revalidate UART lock/loopback and RX-only neutralization with no heater.
7. Revalidate AP association and automatic DHCP.
8. Run the single-listener Phase-8 full-product acceptance exactly once.

Phase 8 passes only with a real complete HTTP 200 JSON response from
`GET http://192.168.4.1/api/v1/status`, every mandatory >=32-KiB checkpoint,
unchanged storage/heater safety and complete ordered cleanup. Phase 9 remains
blocked until that proof exists.

## Vehicle boundary

The development board is not an automotive power interface. A later vehicle
installation still requires a protected 12-V-to-5-V/3.3-V supply, reverse-
polarity and transient protection, appropriate UART level protection or
isolation, grounding/EMI review and mechanical strain relief.
