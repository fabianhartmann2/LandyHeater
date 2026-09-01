"""USB-only DFR0975-U PSRAM and internal-heap identity probe.

Importing this module has no hardware side effects. ``run()`` performs no
radio, GPIO, filesystem or flash operation. It separates IDF internal 8-bit
heap from SPI-RAM and reports the MicroPython GC heap independently.
"""


MEMORY_PROBE_CONFIRMATION = "DFR0975U_USB_ONLY_MEMORY_PROBE"
MEMORY_PROBE_PASS = "DFR0975U_MEMORY_PROBE_PASS_V1"

_MALLOC_CAP_8BIT = 1 << 2
_MALLOC_CAP_DMA = 1 << 3
_MALLOC_CAP_SPIRAM = 1 << 10
_MALLOC_CAP_INTERNAL = 1 << 11
_EXPECTED_FLASH_BYTES = 16 * 1024 * 1024
_MINIMUM_PSRAM_REGION_BYTES = 7 * 1024 * 1024
_MAXIMUM_PSRAM_REGION_BYTES = 8 * 1024 * 1024
_MINIMUM_GC_FREE_BYTES = 32 * 1024
_MINIMUM_INTERNAL_FREE_BYTES = 32 * 1024
_MINIMUM_INTERNAL_LARGEST_BYTES = 32 * 1024

_EXPECTED_MACHINE = "DFRobot DFR0975-U N16R8 with ESP32S3"

_EXPECTED_IDENTITY = (
    "DFR0975-U",
    "1.0",
    "ESP32-S3-WROOM-1U-N16R8",
    "ESP32_GENERIC_S3",
    "DFR0975U_N16R8",
    "SPIRAM_OCT",
    "1.28.0",
)


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _aggregate_regions(regions, label):
    _require(type(regions) in (list, tuple), "{} heap list missing".format(label))
    _require(len(regions) > 0, "{} heap has no regions".format(label))
    total = 0
    free = 0
    largest = 0
    minimum = 0
    for region in regions:
        _require(
            type(region) in (list, tuple) and len(region) == 4,
            "{} heap region shape invalid".format(label),
        )
        values = []
        for value in region:
            _require(
                type(value) is int and value >= 0,
                "{} heap counter invalid".format(label),
            )
            values.append(value)
        region_total, region_free, region_largest, region_minimum = values
        _require(region_free <= region_total, "{} free exceeds total".format(label))
        _require(
            region_largest <= region_free,
            "{} largest block exceeds free".format(label),
        )
        _require(
            region_minimum <= region_total,
            "{} minimum exceeds total".format(label),
        )
        total += region_total
        free += region_free
        minimum += region_minimum
        if region_largest > largest:
            largest = region_largest
    return {
        "regions": len(regions),
        "total": total,
        "free": free,
        "largest": largest,
        "minimum": minimum,
    }


def run(
    confirmation,
    config_module=None,
    os_module=None,
    sys_module=None,
    esp_module=None,
    esp32_module=None,
    gc_module=None,
):
    """Return bounded memory facts without activating any peripheral."""

    _require(
        confirmation == MEMORY_PROBE_CONFIRMATION,
        "DFR0975-U memory probe is not armed",
    )
    if config_module is None:
        import board_config as config_module

    identity = (
        config_module.BOARD_SKU,
        config_module.BOARD_HARDWARE_REVISION,
        config_module.BOARD_MODULE,
        config_module.MICROPYTHON_TARGET,
        config_module.MICROPYTHON_BUILD_BOARD,
        config_module.MICROPYTHON_VARIANT,
        config_module.MICROPYTHON_VERSION,
    )
    _require(identity == _EXPECTED_IDENTITY, "board firmware identity differs")

    if sys_module is None:
        import sys as sys_module
    implementation = getattr(sys_module, "implementation", None)
    _require(
        getattr(implementation, "name", None) == "micropython",
        "MicroPython is required",
    )
    version = getattr(implementation, "version", ())
    try:
        version_triplet = (version[0], version[1], version[2])
    except (IndexError, TypeError):
        version_triplet = None
    _require(version_triplet == (1, 28, 0), "MicroPython 1.28.0 is required")
    _require(getattr(sys_module, "platform", None) == "esp32", "ESP32 required")

    if os_module is None:
        import os as os_module
    uname = getattr(os_module, "uname", None)
    _require(callable(uname), "os.uname API missing")
    _require(
        getattr(uname(), "machine", None) == _EXPECTED_MACHINE,
        "DFR0975-U firmware machine identity differs",
    )

    for name in (
        "UART_PINS_APPROVED",
        "UART_PROTOCOL_TX_ENABLED",
        "UART_TX_GATE_APPROVED",
        "ONEWIRE_PIN_APPROVED",
        "I2C_PINS_APPROVED",
        "WIFI_RADIO_APPROVED",
    ):
        _require(
            getattr(config_module, name, None) is False,
            "{} must remain false".format(name),
        )

    if esp_module is None:
        import esp as esp_module
    if esp32_module is None:
        import esp32 as esp32_module
    if gc_module is None:
        import gc as gc_module

    _require(callable(getattr(esp_module, "flash_size", None)), "esp API missing")
    _require(
        callable(getattr(esp32_module, "idf_heap_info", None)),
        "esp32 API missing",
    )
    for name in ("collect", "mem_free", "mem_alloc"):
        _require(callable(getattr(gc_module, name, None)), "gc API missing")

    gc_module.collect()
    gc_free = gc_module.mem_free()
    gc_alloc = gc_module.mem_alloc()
    _require(
        type(gc_free) is int and gc_free >= _MINIMUM_GC_FREE_BYTES,
        "GC free heap is below 32 KiB",
    )
    _require(type(gc_alloc) is int and gc_alloc >= 0, "GC allocated heap invalid")

    flash_bytes = esp_module.flash_size()
    _require(flash_bytes == _EXPECTED_FLASH_BYTES, "physical flash is not 16 MB")

    internal = _aggregate_regions(
        esp32_module.idf_heap_info(_MALLOC_CAP_8BIT | _MALLOC_CAP_INTERNAL),
        "internal",
    )
    internal_dma = _aggregate_regions(
        esp32_module.idf_heap_info(
            _MALLOC_CAP_8BIT | _MALLOC_CAP_INTERNAL | _MALLOC_CAP_DMA
        ),
        "internal DMA",
    )
    psram = _aggregate_regions(
        esp32_module.idf_heap_info(_MALLOC_CAP_8BIT | _MALLOC_CAP_SPIRAM),
        "psram",
    )
    _require(
        psram["total"] >= _MINIMUM_PSRAM_REGION_BYTES,
        "registered PSRAM is smaller than the N16R8 profile",
    )
    _require(
        psram["total"] <= _MAXIMUM_PSRAM_REGION_BYTES,
        "registered PSRAM exceeds the N16R8 profile",
    )
    for label, heap in (("internal", internal), ("internal DMA", internal_dma)):
        _require(
            heap["free"] >= _MINIMUM_INTERNAL_FREE_BYTES,
            "{} heap free memory is below 32 KiB".format(label),
        )
        _require(
            heap["largest"] >= _MINIMUM_INTERNAL_LARGEST_BYTES,
            "{} largest block is below 32 KiB".format(label),
        )

    result = {
        "flash_bytes": flash_bytes,
        "gc_free": gc_free,
        "gc_alloc": gc_alloc,
        "internal": internal,
        "internal_dma": internal_dma,
        "psram": psram,
    }
    print(
        MEMORY_PROBE_PASS,
        flash_bytes,
        gc_free,
        internal["free"],
        internal["largest"],
        internal_dma["free"],
        internal_dma["largest"],
        psram["total"],
    )
    return result


__all__ = (
    "MEMORY_PROBE_CONFIRMATION",
    "MEMORY_PROBE_PASS",
    "run",
)
