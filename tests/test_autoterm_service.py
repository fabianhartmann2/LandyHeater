import inspect
import runpy
import unittest
from unittest import mock

import app.composition as composition_module
import board_config
import protocol.autoterm_service as autoterm_service_module
from app.heater_controller import PHASE_ERROR, HeaterController
from protocol.autoterm_protocol import (
    CMD_SETTINGS,
    CONTROL_MODE_CABIN_TEMPERATURE,
    CONTROL_MODE_POWER,
    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
    DEVICE_HEATER,
    build_frame,
    build_init_request,
    build_shutdown_request,
    build_start_for_mode,
    build_status_request,
    parse_frame,
)
from protocol.autoterm_service import (
    AutotermProtocolService,
    AutotermProtocolServiceError,
    AutotermProtocolTxDisabledError,
)
from protocol.uart_transport import UARTTransport


REAL_INIT = bytes.fromhex("AA 04 05 00 04 12 8A 00 3D D6 CB A6")
REAL_OFF_STATUS = bytes.fromhex(
    "AA 04 13 00 0F 00 01 00 1E 7F 00 80 01 2F "
    "00 00 00 00 00 00 00 00 00 60 6D A0"
)


class RecordingTransport:
    def __init__(self):
        self.sent = []
        self.poll_calls = []
        self.poll_batches = []
        self.send_result = "exact"
        self.send_error = None
        self.deinit_calls = 0
        self.deinit_error = None
        self.reset_rx_calls = 0
        self.reset_rx_error = None
        self.rx_faulted = False
        self.tx_enabled = True

    def poll(self, now_ms=None):
        self.poll_calls.append(now_ms)
        if self.poll_batches:
            return self.poll_batches.pop(0)
        return []

    def send_frame(self, raw_frame):
        raw_frame = bytes(raw_frame)
        self.sent.append(raw_frame)
        if self.send_error is not None:
            raise self.send_error
        if self.send_result == "exact":
            return len(raw_frame)
        return self.send_result

    def deinit(self):
        self.deinit_calls += 1
        if self.deinit_error is not None:
            raise self.deinit_error

    def reset_rx(self):
        self.reset_rx_calls += 1
        if self.reset_rx_error is not None:
            raise self.reset_rx_error
        self.rx_faulted = False

    def status(self):
        return {
            "rx_faulted": self.rx_faulted,
            "tx_enabled": self.tx_enabled,
            "last_error": None,
        }


class NoWriteUART:
    def __init__(self):
        self.writes = []
        self.deinit_calls = 0

    def any(self):
        return 0

    def read(self, count=None):
        return None

    def write(self, raw_frame):
        self.writes.append(bytes(raw_frame))
        return len(raw_frame)

    def deinit(self):
        self.deinit_calls += 1


class ScriptedUART(NoWriteUART):
    def __init__(self, reads):
        super().__init__()
        self.reads = list(reads)

    def any(self):
        return 1 if self.reads else 0

    def read(self, count=None):
        if not self.reads:
            return None
        return self.reads.pop(0)


def authorized_service(transport):
    return AutotermProtocolService(
        transport,
        _transmit_capability=(
            autoterm_service_module._SERVICE_TRANSMIT_CAPABILITY
        ),
    )


class TestAutotermProtocolServiceBoundaries(unittest.TestCase):
    def test_constructor_requires_poll_and_send_without_io(self):
        class MissingBoth:
            pass

        class MissingSend:
            def poll(self, now_ms=None):
                raise AssertionError("constructor must not poll")

            def deinit(self):
                raise AssertionError("constructor must not deinitialize")

        class MissingPoll:
            def send_frame(self, raw_frame):
                raise AssertionError("constructor must not send")

            def deinit(self):
                raise AssertionError("constructor must not deinitialize")

        class MissingDeinit:
            def poll(self, now_ms=None):
                raise AssertionError("constructor must not poll")

            def send_frame(self, raw_frame):
                raise AssertionError("constructor must not send")

        for transport in (
            None,
            MissingBoth(),
            MissingSend(),
            MissingPoll(),
            MissingDeinit(),
        ):
            with self.subTest(transport=transport):
                with self.assertRaises(ValueError):
                    AutotermProtocolService(transport)

        transport = RecordingTransport()
        service = AutotermProtocolService(transport)
        self.assertEqual(transport.poll_calls, [])
        self.assertEqual(transport.sent, [])
        self.assertFalse(hasattr(service, "send_frame"))
        self.assertFalse(hasattr(service, "transport"))
        self.assertFalse(hasattr(service, "uart"))
        self.assertFalse(hasattr(service, "tx_enabled"))
        self.assertFalse(service.closed)
        with self.assertRaises(AutotermProtocolTxDisabledError):
            service.request_initialization()
        self.assertEqual(transport.sent, [])

    def test_module_has_no_hardware_or_transport_factory_dependency(self):
        source = inspect.getsource(autoterm_service_module)
        for forbidden in (
            "import machine",
            "import board_config",
            "uart_transport",
            "_TX_AUTHORIZATION",
            "open_from_board_config",
        ):
            self.assertNotIn(forbidden, source)

    def test_invalid_transport_results_fail_closed_without_retry(self):
        class LiarInt(int):
            def __ne__(self, other):
                return False

        invalid_results = (
            None,
            False,
            True,
            0,
            -1,
            6,
            8,
            "7",
            LiarInt(0),
        )
        for result in invalid_results:
            with self.subTest(result=result):
                transport = RecordingTransport()
                transport.send_result = result
                service = authorized_service(transport)
                with self.assertRaises(AutotermProtocolServiceError):
                    service.request_initialization()
                self.assertEqual(transport.sent, [build_init_request()])

    def test_transport_exceptions_propagate_after_one_attempt(self):
        for error in (OSError("write failed"), KeyboardInterrupt()):
            with self.subTest(error=error.__class__.__name__):
                transport = RecordingTransport()
                transport.send_error = error
                service = authorized_service(transport)
                with self.assertRaises(error.__class__):
                    service.request_status()
                self.assertEqual(transport.sent, [build_status_request()])

    def test_poll_contract_is_bounded_and_explicit(self):
        transport = RecordingTransport()
        service = AutotermProtocolService(transport)
        for result in (None, b"", iter(()), 1):
            with self.subTest(result=result):
                transport.poll_batches = [result]
                with self.assertRaises(AutotermProtocolServiceError):
                    service.poll_inbound(50)

        transport.poll_batches = [[REAL_INIT] * 81]
        with self.assertRaisesRegex(
            AutotermProtocolServiceError, "too many frames"
        ):
            service.poll_inbound(51)

    def test_poll_parse_failure_is_fail_closed_and_next_poll_recovers(self):
        transport = RecordingTransport()
        service = AutotermProtocolService(transport)
        transport.poll_batches = [[REAL_INIT, b"bad"], [REAL_OFF_STATUS]]

        with self.assertRaises(ValueError):
            service.poll_inbound(60)
        self.assertEqual(transport.sent, [])
        self.assertEqual(
            service.poll_inbound(61),
            [parse_frame(REAL_OFF_STATUS)],
        )

    def test_parse_memory_error_propagates_without_poisoning_next_poll(self):
        transport = RecordingTransport()
        service = AutotermProtocolService(transport)
        transport.poll_batches = [[REAL_INIT], [REAL_OFF_STATUS]]

        with mock.patch.object(
            AutotermProtocolService,
            "parse_inbound_frame",
            side_effect=MemoryError("simulated heap pressure"),
        ):
            with self.assertRaises(MemoryError):
                service.poll_inbound(62)
        self.assertEqual(transport.sent, [])
        self.assertEqual(
            service.poll_inbound(63),
            [parse_frame(REAL_OFF_STATUS)],
        )

    def test_deinit_closes_immediately_and_cleanup_can_be_retried(self):
        transport = RecordingTransport()
        service = AutotermProtocolService(transport)
        transport.deinit_error = OSError("temporary cleanup failure")

        with self.assertRaises(OSError):
            service.deinit()
        self.assertTrue(service.closed)
        with self.assertRaises(AutotermProtocolServiceError):
            service.poll_inbound(0)
        with self.assertRaises(AutotermProtocolServiceError):
            service.request_status()
        self.assertEqual(transport.sent, [])

        transport.deinit_error = None
        service.deinit()
        service.deinit()
        self.assertEqual(transport.deinit_calls, 2)

    def test_transport_status_is_detached_and_rx_reset_is_explicit(self):
        transport = RecordingTransport()
        transport.rx_faulted = True
        service = AutotermProtocolService(transport)

        status = service.transport_status()
        self.assertTrue(status["rx_faulted"])
        status["rx_faulted"] = False
        self.assertTrue(transport.rx_faulted)

        self.assertTrue(service.reset_inbound())
        self.assertFalse(service.transport_status()["rx_faulted"])
        self.assertEqual(transport.reset_rx_calls, 1)


class TestAutotermProtocolServiceOutbound(unittest.TestCase):
    def setUp(self):
        self.transport = RecordingTransport()
        self.service = authorized_service(self.transport)

    def test_named_requests_send_exact_reference_frames_once(self):
        requests = (
            (self.service.request_initialization, (), build_init_request()),
            (self.service.request_status, (), build_status_request()),
            (self.service.request_shutdown, (), build_shutdown_request()),
            (
                self.service.request_start,
                (CONTROL_MODE_POWER, None, 5),
                build_start_for_mode(CONTROL_MODE_POWER, power_level=5),
            ),
            (
                self.service.request_start,
                (CONTROL_MODE_ROOF_TENT_TEMPERATURE, 20, None),
                build_start_for_mode(
                    CONTROL_MODE_ROOF_TENT_TEMPERATURE,
                    target_temperature=20,
                ),
            ),
            (
                self.service.request_start,
                (CONTROL_MODE_CABIN_TEMPERATURE, 20, None),
                build_start_for_mode(
                    CONTROL_MODE_CABIN_TEMPERATURE,
                    target_temperature=20,
                ),
            ),
        )
        for method, args, expected in requests:
            with self.subTest(method=method.__name__, args=args):
                before = len(self.transport.sent)
                self.assertIs(method(*args), True)
                self.assertEqual(len(self.transport.sent), before + 1)
                self.assertEqual(self.transport.sent[-1], expected)

    def test_start_boundaries_for_all_application_modes(self):
        cases = (
            (CONTROL_MODE_POWER, None, 1),
            (CONTROL_MODE_POWER, None, 9),
            (CONTROL_MODE_ROOF_TENT_TEMPERATURE, 5, None),
            (CONTROL_MODE_ROOF_TENT_TEMPERATURE, 30, None),
            (CONTROL_MODE_CABIN_TEMPERATURE, 5, None),
            (CONTROL_MODE_CABIN_TEMPERATURE, 30, None),
        )
        for mode, target, power in cases:
            with self.subTest(mode=mode, target=target, power=power):
                self.assertTrue(self.service.request_start(mode, target, power))
                self.assertEqual(
                    self.transport.sent[-1],
                    build_start_for_mode(
                        mode,
                        target_temperature=target,
                        power_level=power,
                    ),
                )

    def test_invalid_or_ambiguous_start_never_reaches_transport(self):
        cases = (
            ("unknown", None, 5),
            (CONTROL_MODE_POWER, None, None),
            (CONTROL_MODE_POWER, None, True),
            (CONTROL_MODE_POWER, None, 0),
            (CONTROL_MODE_POWER, None, 10),
            (CONTROL_MODE_POWER, 20, 5),
            (CONTROL_MODE_ROOF_TENT_TEMPERATURE, None, None),
            (CONTROL_MODE_ROOF_TENT_TEMPERATURE, True, None),
            (CONTROL_MODE_ROOF_TENT_TEMPERATURE, 4, None),
            (CONTROL_MODE_ROOF_TENT_TEMPERATURE, 31, None),
            (CONTROL_MODE_CABIN_TEMPERATURE, 20, 5),
        )
        for mode, target, power in cases:
            with self.subTest(mode=mode, target=target, power=power):
                before = len(self.transport.sent)
                with self.assertRaises(ValueError):
                    self.service.request_start(mode, target, power)
                self.assertEqual(len(self.transport.sent), before)


class TestAutotermProtocolServiceInbound(unittest.TestCase):
    def setUp(self):
        self.transport = RecordingTransport()
        self.service = AutotermProtocolService(self.transport)

    def test_real_captures_are_parsed_in_transport_order(self):
        self.transport.poll_batches = [[REAL_INIT, REAL_OFF_STATUS]]
        frames = self.service.poll_inbound(123)
        self.assertEqual(frames, [parse_frame(REAL_INIT), parse_frame(REAL_OFF_STATUS)])
        self.assertEqual(self.transport.poll_calls, [123])
        self.assertEqual(self.transport.sent, [])

    def test_bytes_like_input_is_copied_to_immutable_raw(self):
        for raw in (REAL_INIT, bytearray(REAL_INIT), memoryview(REAL_INIT)):
            with self.subTest(raw_type=raw.__class__.__name__):
                parsed = self.service.parse_inbound_frame(raw)
                self.assertIsInstance(parsed["raw"], bytes)
                self.assertEqual(parsed["raw"], REAL_INIT)

    def test_validator_ignores_all_supplied_metadata(self):
        forged = {
            "raw": REAL_OFF_STATUS,
            "device": 3,
            "command": 1,
            "payload": b"forged",
            "crc_valid": False,
            "crc_received": 0,
            "crc_calculated": 0,
            "status": {"heater_state": 4, "voltage": 99.9},
        }
        canonical = self.service.validate_inbound_frame(forged)
        self.assertEqual(canonical, parse_frame(REAL_OFF_STATUS))
        self.assertIsNot(canonical, forged)
        self.assertEqual(forged["status"]["heater_state"], 4)
        self.assertEqual(self.transport.sent, [])

    def test_forged_crc_claim_is_recalculated_from_raw(self):
        bad_raw = bytearray(REAL_OFF_STATUS)
        bad_raw[14] = 4
        forged = {
            "raw": bytes(bad_raw),
            "crc_valid": True,
            "crc_received": 0x6DA0,
            "crc_calculated": 0x6DA0,
        }
        canonical = self.service.validate_inbound_frame(forged)
        self.assertIs(canonical["crc_valid"], False)
        self.assertNotEqual(
            canonical["crc_received"], canonical["crc_calculated"]
        )

    def test_expected_malformed_inputs_return_none(self):
        malformed_frames = (
            None,
            b"raw",
            {},
            {"raw": None},
            {"raw": "AA"},
            {"raw": [0xAA]},
            {"raw": 7},
            {"raw": b"\xAA\x04"},
            {"raw": b"\x55\x04\x00\x00\x04\x00\x00"},
        )
        for frame in malformed_frames:
            with self.subTest(frame=frame):
                self.assertIsNone(self.service.validate_inbound_frame(frame))

    def test_unexpected_mapping_errors_are_not_hidden(self):
        class ExplodingDict(dict):
            def get(self, key, default=None):
                raise MemoryError("simulated allocation failure")

        with self.assertRaises(MemoryError):
            self.service.validate_inbound_frame(ExplodingDict())

    def test_unknown_command_and_bad_crc_are_preserved_by_poll(self):
        unknown = build_frame(
            CMD_SETTINGS,
            b"\x01\x02",
            device=DEVICE_HEATER,
            reserved=0x7E,
        )
        bad_crc = bytearray(REAL_INIT)
        bad_crc[-1] ^= 0x01
        self.transport.poll_batches = [(unknown, bytes(bad_crc))]
        frames = self.service.poll_inbound(10)
        self.assertEqual(frames[0]["command"], CMD_SETTINGS)
        self.assertEqual(frames[0]["reserved"], 0x7E)
        self.assertEqual(frames[0]["payload"], b"\x01\x02")
        self.assertTrue(frames[0]["crc_valid"])
        self.assertFalse(frames[1]["crc_valid"])


class TestAutotermProtocolServiceIntegration(unittest.TestCase):
    def test_controller_and_service_complete_real_sync_then_exact_start(self):
        transport = RecordingTransport()
        service = authorized_service(transport)
        controller = HeaterController(service)
        controller.request_start(CONTROL_MODE_POWER, power_level=5)

        self.assertEqual(controller.step(0), ["initialization"])
        transport.poll_batches = [[REAL_INIT]]
        for frame in service.poll_inbound(10):
            self.assertTrue(controller.handle_frame(frame, 10))

        self.assertEqual(controller.step(10), ["status"])
        transport.poll_batches = [[REAL_OFF_STATUS]]
        for frame in service.poll_inbound(20):
            self.assertTrue(controller.handle_frame(frame, 20))

        self.assertEqual(controller.step(220), ["start"])
        self.assertEqual(
            transport.sent,
            [
                build_init_request(),
                build_status_request(),
                build_start_for_mode(CONTROL_MODE_POWER, power_level=5),
            ],
        )

    def test_locked_uart_transport_blocks_service_and_controller_tx(self):
        uart = NoWriteUART()
        transport = UARTTransport(
            uart,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        service = AutotermProtocolService(transport)
        controller = HeaterController(service)

        self.assertEqual(controller.step(0), ["initialization_error"])
        self.assertEqual(controller.phase, PHASE_ERROR)
        self.assertEqual(uart.writes, [])
        self.assertEqual(transport.status()["tx_blocked"], 0)
        self.assertEqual(controller.step(999), [])
        self.assertEqual(uart.writes, [])
        self.assertEqual(controller.step(1000), ["initialization_error"])
        self.assertEqual(uart.writes, [])
        self.assertEqual(transport.status()["tx_blocked"], 0)

        canonical = service.validate_inbound_frame(
            {"raw": REAL_OFF_STATUS, "crc_valid": False}
        )
        self.assertEqual(canonical, parse_frame(REAL_OFF_STATUS))
        self.assertEqual(transport.status()["tx_blocked"], 0)

    def test_rx_fault_is_visible_and_recovers_only_after_explicit_reset(self):
        uart = ScriptedUART((None, None, REAL_INIT))
        transport = UARTTransport(
            uart,
            max_empty_ready_reads=2,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        service = AutotermProtocolService(transport)

        self.assertEqual(service.poll_inbound(0), [])
        self.assertEqual(service.poll_inbound(1), [])
        self.assertTrue(service.transport_status()["rx_faulted"])
        self.assertEqual(service.poll_inbound(2), [])
        self.assertEqual(len(uart.reads), 1)

        self.assertTrue(service.reset_inbound())
        self.assertFalse(service.transport_status()["rx_faulted"])
        self.assertEqual(service.poll_inbound(3), [parse_frame(REAL_INIT)])


class TestSafeProtocolComposition(unittest.TestCase):
    def test_importing_composition_does_not_load_board_or_hardware(self):
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name in (
                "board_config",
                "machine",
                "protocol.uart_transport",
            ):
                raise AssertionError("hardware dependency imported eagerly")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            namespace = runpy.run_path(
                "app/composition.py", run_name="safe_composition_import_test"
            )
        self.assertIn("open_tx_locked_protocol_service", namespace)

    def test_factory_has_no_public_injection_or_unlock_arguments(self):
        signature = inspect.signature(
            composition_module.open_tx_locked_protocol_service
        )
        self.assertEqual(tuple(signature.parameters), ())
        with self.assertRaises(TypeError):
            composition_module.open_tx_locked_protocol_service(
                tx_enabled=True
            )

    def test_true_board_flag_aborts_before_transport_factory(self):
        with mock.patch.object(
            board_config, "UART_PROTOCOL_TX_ENABLED", True
        ), mock.patch(
            "protocol.uart_transport.open_from_board_config"
        ) as open_transport:
            with self.assertRaises(RuntimeError):
                composition_module.open_tx_locked_protocol_service()
        open_transport.assert_not_called()

    def test_factory_returns_only_a_service_around_locked_transport(self):
        uart = NoWriteUART()
        transport = UARTTransport(
            uart,
            ticks_ms=lambda: 0,
            ticks_diff=lambda current, previous: current - previous,
        )
        with mock.patch.object(
            board_config, "UART_PROTOCOL_TX_ENABLED", False
        ), mock.patch(
            "protocol.uart_transport.open_from_board_config",
            return_value=transport,
        ) as open_transport:
            service = composition_module.open_tx_locked_protocol_service()

        open_transport.assert_called_once_with()
        self.assertFalse(hasattr(service, "transport"))
        self.assertFalse(hasattr(service, "send_frame"))
        self.assertFalse(hasattr(service, "tx_enabled"))
        with self.assertRaises(AutotermProtocolTxDisabledError):
            service.request_initialization()
        self.assertEqual(uart.writes, [])
        service.deinit()
        self.assertEqual(uart.deinit_calls, 1)

    def test_unlocked_transport_is_rejected_and_deinitialized(self):
        transport = RecordingTransport()
        transport.tx_enabled = True
        with mock.patch.object(
            board_config, "UART_PROTOCOL_TX_ENABLED", False
        ), mock.patch(
            "protocol.uart_transport.open_from_board_config",
            return_value=transport,
        ):
            with self.assertRaises(RuntimeError):
                composition_module.open_tx_locked_protocol_service()
        self.assertEqual(transport.sent, [])
        self.assertEqual(transport.deinit_calls, 1)

    def test_rejected_transport_cleanup_retries_once(self):
        class FailOnceCleanupTransport(RecordingTransport):
            def deinit(self):
                self.deinit_calls += 1
                if self.deinit_calls == 1:
                    raise OSError("transient deinit failure")

        transport = FailOnceCleanupTransport()
        transport.tx_enabled = True
        with mock.patch.object(
            board_config, "UART_PROTOCOL_TX_ENABLED", False
        ), mock.patch(
            "protocol.uart_transport.open_from_board_config",
            return_value=transport,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "TX-enabled transport"
            ):
                composition_module.open_tx_locked_protocol_service()
        self.assertEqual(transport.deinit_calls, 2)

    def test_persistent_cleanup_failure_is_reported(self):
        transport = RecordingTransport()
        transport.tx_enabled = True
        transport.deinit_error = OSError("deinit stayed broken")
        with mock.patch.object(
            board_config, "UART_PROTOCOL_TX_ENABLED", False
        ), mock.patch(
            "protocol.uart_transport.open_from_board_config",
            return_value=transport,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "transport cleanup also failed"
            ):
                composition_module.open_tx_locked_protocol_service()
        self.assertEqual(transport.deinit_calls, 2)

    def test_base_exception_during_service_construction_still_cleans_up(self):
        transport = RecordingTransport()
        transport.tx_enabled = False
        with mock.patch.object(
            board_config, "UART_PROTOCOL_TX_ENABLED", False
        ), mock.patch(
            "protocol.uart_transport.open_from_board_config",
            return_value=transport,
        ), mock.patch(
            "protocol.autoterm_service.AutotermProtocolService",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                composition_module.open_tx_locked_protocol_service()
        self.assertEqual(transport.deinit_calls, 1)

    def test_lying_locked_transport_still_cannot_write_through_service(self):
        transport = RecordingTransport()
        transport.tx_enabled = False
        with mock.patch.object(
            board_config, "UART_PROTOCOL_TX_ENABLED", False
        ), mock.patch(
            "protocol.uart_transport.open_from_board_config",
            return_value=transport,
        ):
            service = composition_module.open_tx_locked_protocol_service()

        with self.assertRaises(AutotermProtocolTxDisabledError):
            service.request_shutdown()
        self.assertEqual(transport.sent, [])
        service.deinit()


if __name__ == "__main__":
    unittest.main()
