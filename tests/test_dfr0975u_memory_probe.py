import unittest
import types

from tools import dfr0975u_memory_probe as probe


class FakeConfig:
    BOARD_SKU = "DFR0975-U"
    BOARD_HARDWARE_REVISION = "1.0"
    BOARD_MODULE = "ESP32-S3-WROOM-1U-N16R8"
    MICROPYTHON_TARGET = "ESP32_GENERIC_S3"
    MICROPYTHON_BUILD_BOARD = "DFR0975U_N16R8"
    MICROPYTHON_VARIANT = "SPIRAM_OCT"
    MICROPYTHON_VERSION = "1.28.0"
    UART_PINS_APPROVED = False
    UART_PROTOCOL_TX_ENABLED = False
    UART_TX_GATE_APPROVED = False
    ONEWIRE_PIN_APPROVED = False
    I2C_PINS_APPROVED = False
    WIFI_RADIO_APPROVED = False


class FakeGC:
    collected = 0

    @classmethod
    def collect(cls):
        cls.collected += 1

    @staticmethod
    def mem_free():
        return 7_000_000

    @staticmethod
    def mem_alloc():
        return 500_000


class FakeESP32:
    calls = []

    @staticmethod
    def flash_size():
        return 16 * 1024 * 1024

    @classmethod
    def idf_heap_info(cls, capabilities):
        cls.calls.append(capabilities)
        if capabilities == ((1 << 2) | (1 << 11)):
            return [(300_000, 180_000, 96_000, 120_000)]
        if capabilities == ((1 << 2) | (1 << 3) | (1 << 11)):
            return [(220_000, 130_000, 72_000, 90_000)]
        if capabilities == ((1 << 2) | (1 << 10)):
            return [(8 * 1024 * 1024, 500_000, 400_000, 350_000)]
        raise AssertionError("unexpected capabilities")


class FakeOS:
    @staticmethod
    def uname():
        return types.SimpleNamespace(machine=probe._EXPECTED_MACHINE)


class FakeSys:
    implementation = types.SimpleNamespace(
        name="micropython", version=(1, 28, 0)
    )
    platform = "esp32"


class TestDFR0975UMemoryProbe(unittest.TestCase):
    def setUp(self):
        FakeGC.collected = 0
        FakeESP32.calls = []

    def test_reports_psram_gc_and_internal_heap_separately(self):
        result = probe.run(
            probe.MEMORY_PROBE_CONFIRMATION,
            config_module=FakeConfig,
            os_module=FakeOS,
            sys_module=FakeSys,
            esp32_module=FakeESP32,
            gc_module=FakeGC,
        )
        self.assertEqual(result["flash_bytes"], 16 * 1024 * 1024)
        self.assertEqual(result["gc_free"], 7_000_000)
        self.assertEqual(result["internal"]["free"], 180_000)
        self.assertEqual(result["internal"]["largest"], 96_000)
        self.assertEqual(result["internal_dma"]["free"], 130_000)
        self.assertEqual(result["internal_dma"]["largest"], 72_000)
        self.assertEqual(result["psram"]["total"], 8 * 1024 * 1024)
        self.assertEqual(FakeGC.collected, 1)
        self.assertEqual(
            FakeESP32.calls,
            [
                (1 << 2) | (1 << 11),
                (1 << 2) | (1 << 3) | (1 << 11),
                (1 << 2) | (1 << 10),
            ],
        )

    def test_wrong_confirmation_or_open_hardware_lock_fails_before_sampling(self):
        with self.assertRaises(RuntimeError):
            probe.run(
                "wrong",
                config_module=FakeConfig,
                os_module=FakeOS,
                sys_module=FakeSys,
                esp32_module=FakeESP32,
                gc_module=FakeGC,
            )

        class UnsafeConfig(FakeConfig):
            WIFI_RADIO_APPROVED = True

        with self.assertRaises(RuntimeError):
            probe.run(
                probe.MEMORY_PROBE_CONFIRMATION,
                config_module=UnsafeConfig,
                os_module=FakeOS,
                sys_module=FakeSys,
                esp32_module=FakeESP32,
                gc_module=FakeGC,
            )
        self.assertEqual(FakeESP32.calls, [])
        self.assertEqual(FakeGC.collected, 0)

    def test_missing_psram_and_wrong_flash_fail_closed(self):
        class WrongFlash(FakeESP32):
            @staticmethod
            def flash_size():
                return 4 * 1024 * 1024

        with self.assertRaisesRegex(RuntimeError, "physical flash"):
            probe.run(
                probe.MEMORY_PROBE_CONFIRMATION,
                config_module=FakeConfig,
                os_module=FakeOS,
                sys_module=FakeSys,
                esp32_module=WrongFlash,
                gc_module=FakeGC,
            )

        class MissingPSRAM(FakeESP32):
            @classmethod
            def idf_heap_info(cls, capabilities):
                if capabilities == ((1 << 2) | (1 << 11)):
                    return [(300_000, 180_000, 96_000, 120_000)]
                if capabilities == ((1 << 2) | (1 << 3) | (1 << 11)):
                    return [(220_000, 130_000, 72_000, 90_000)]
                return []

        with self.assertRaisesRegex(RuntimeError, "psram heap has no regions"):
            probe.run(
                probe.MEMORY_PROBE_CONFIRMATION,
                config_module=FakeConfig,
                os_module=FakeOS,
                sys_module=FakeSys,
                esp32_module=MissingPSRAM,
                gc_module=FakeGC,
            )

    def test_platform_identity_and_32_kib_native_gates_fail_closed(self):
        class WrongOS(FakeOS):
            @staticmethod
            def uname():
                return types.SimpleNamespace(machine="Generic ESP32 module")

        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            probe.run(
                probe.MEMORY_PROBE_CONFIRMATION,
                config_module=FakeConfig,
                os_module=WrongOS,
                sys_module=FakeSys,
                esp32_module=FakeESP32,
                gc_module=FakeGC,
            )

        class LowDMA(FakeESP32):
            @classmethod
            def idf_heap_info(cls, capabilities):
                if capabilities == ((1 << 2) | (1 << 11)):
                    return [(300_000, 180_000, 96_000, 120_000)]
                if capabilities == ((1 << 2) | (1 << 3) | (1 << 11)):
                    return [(220_000, 32_767, 32_767, 20_000)]
                return [(8 * 1024 * 1024, 500_000, 400_000, 350_000)]

        with self.assertRaisesRegex(RuntimeError, "internal DMA heap free"):
            probe.run(
                probe.MEMORY_PROBE_CONFIRMATION,
                config_module=FakeConfig,
                os_module=FakeOS,
                sys_module=FakeSys,
                esp32_module=LowDMA,
                gc_module=FakeGC,
            )


if __name__ == "__main__":
    unittest.main()
