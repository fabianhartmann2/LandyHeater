import ast
import inspect
import unittest

import services.rest_security as security_module
from services.rest_security import (
    RestSecurityDenied,
    RestSecurityPolicy,
    RestSecurityUnavailable,
)


def deterministic_rng(size):
    return bytes(range(size))


def policy(ingress="ap", rng=deterministic_rng):
    return RestSecurityPolicy(
        rng,
        ("192.168.4.1", "heater.local"),
        ingress,
    )


class TestRestSecurityPolicy(unittest.TestCase):
    def test_construction_is_inert_and_start_is_explicit(self):
        calls = []

        def rng(size):
            calls.append(size)
            return b"x" * size

        value = policy(rng=rng)
        self.assertEqual(calls, [])
        self.assertFalse(value.mutation_api_available)
        self.assertTrue(value.start())
        self.assertEqual(calls, [32])
        self.assertFalse(value.start())

    def test_context_and_valid_same_origin_ap_mutation(self):
        value = policy()
        value.start()
        headers = {"host": "192.168.4.1"}
        context = value.security_context(headers)
        self.assertEqual(len(context["csrf_token"]), 64)
        self.assertNotIn(context["csrf_token"], repr(value.snapshot()))

        mutation_headers = {
            "host": "192.168.4.1:80",
            "origin": "http://192.168.4.1",
            "x-landy-csrf": context["csrf_token"],
        }
        self.assertTrue(value.authorize_mutation(mutation_headers))

    def test_station_listener_remains_read_only_with_correct_token(self):
        value = policy("sta")
        value.start()
        self.assertTrue(value.validate_read({"host": "heater.local"}))
        with self.assertRaises(RestSecurityUnavailable):
            value.security_context({"host": "heater.local"})
        with self.assertRaises(RestSecurityUnavailable):
            value.authorize_mutation({
                "host": "heater.local",
                "origin": "http://heater.local",
                "x-landy-csrf": "0" * 64,
            })

    def test_host_origin_and_token_are_all_mandatory_for_mutation(self):
        value = policy()
        value.start()
        token = value.security_context({"host": "heater.local"})[
            "csrf_token"
        ]
        base = {
            "host": "heater.local",
            "origin": "http://heater.local",
            "x-landy-csrf": token,
        }
        for key in ("host", "origin", "x-landy-csrf"):
            candidate = dict(base)
            del candidate[key]
            with self.assertRaises(RestSecurityDenied):
                value.authorize_mutation(candidate)
        for key, invalid in (
            ("host", "evil.invalid"),
            ("origin", "http://evil.invalid"),
            ("origin", "null"),
            ("x-landy-csrf", "f" * 64),
        ):
            candidate = dict(base)
            candidate[key] = invalid
            with self.assertRaises(RestSecurityDenied):
                value.authorize_mutation(candidate)

    def test_read_rejects_dns_rebinding_and_cross_origin(self):
        value = policy()
        value.start()
        with self.assertRaises(RestSecurityDenied):
            value.validate_read({"host": "attacker.example"})
        with self.assertRaises(RestSecurityDenied):
            value.validate_read({
                "host": "heater.local",
                "origin": "http://attacker.example",
            })

    def test_deinit_rotates_and_erases_token(self):
        generated = {"byte": 1}

        def rng(size):
            return bytes((generated["byte"],)) * size

        value = policy(rng=rng)
        value.start()
        first = value.security_context({"host": "heater.local"})[
            "csrf_token"
        ]
        raw = value._RestSecurityPolicy__token
        self.assertIsNone(value.deinit())
        self.assertEqual(bytes(raw), b"\x00" * 32)
        self.assertFalse(value.mutation_api_available)
        generated["byte"] = 2
        value.start()
        second = value.security_context({"host": "heater.local"})[
            "csrf_token"
        ]
        self.assertNotEqual(first, second)

    def test_rng_oom_and_contract_failure_disable_mutations_without_text(self):
        def oom(_):
            raise MemoryError("sensitive driver text")

        value = policy(rng=oom)
        with self.assertRaises(MemoryError) as caught:
            value.start()
        self.assertEqual(repr(caught.exception), "MemoryError()")
        self.assertFalse(value.mutation_api_available)
        self.assertNotIn("sensitive", repr(value.snapshot()))

        for result in (None, b"short", bytearray(32), "x" * 32):
            value = policy(rng=lambda _, result=result: result)
            with self.assertRaises(RestSecurityUnavailable):
                value.start()
            self.assertFalse(value.mutation_api_available)

    def test_rng_callback_reentrancy_clears_authority_and_faults(self):
        value = None
        entered = False

        def rng(size):
            nonlocal entered
            if not entered:
                entered = True
                value.deinit()
            return b"x" * size

        value = policy(rng=rng)
        with self.assertRaises(RestSecurityUnavailable):
            value.start()
        snapshot = value.snapshot()
        self.assertTrue(snapshot["faulted"])
        self.assertFalse(snapshot["started"])
        self.assertFalse(snapshot["mutation_api_available"])
        self.assertFalse(snapshot["operation_active"])

    def test_invalid_host_configuration_is_rejected(self):
        for host in ("", " heater.local", "heater.local:80", "héater"):
            with self.assertRaises(ValueError):
                RestSecurityPolicy(deterministic_rng, (host,), "ap")
        with self.assertRaises(ValueError):
            RestSecurityPolicy(deterministic_rng, ("heater.local",), "wan")

    def test_module_has_no_hardware_network_socket_or_os_import(self):
        source = inspect.getsource(security_module)
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for forbidden in ("machine", "network", "socket", "os", "hardware"):
            self.assertNotIn(forbidden, imports)


if __name__ == "__main__":
    unittest.main()
