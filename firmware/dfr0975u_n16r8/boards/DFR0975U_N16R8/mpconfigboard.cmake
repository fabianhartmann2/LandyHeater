set(IDF_TARGET esp32s3)

# DFR0975-U V1.0 uses ESP32-S3-WROOM-1U-N16R8:
# 16 MiB quad-SPI flash and 8 MiB octal PSRAM.
set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    boards/sdkconfig.ble
    boards/sdkconfig.spiram_sx
    boards/sdkconfig.240mhz
    boards/sdkconfig.spiram_oct
    boards/DFR0975U_N16R8/sdkconfig.board
)

