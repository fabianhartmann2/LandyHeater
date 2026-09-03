import unittest

from adapters.micropython_captive_dns import (
    DNS_PORT,
    MAX_DNS_PACKET_BYTES,
    CaptiveDNSError,
    MicroPythonCaptiveDNS,
    _response_for,
)


AP_IP = "192.168.4.1"
PEER = ("192.168.4.2", 53000)


def query(name="connectivitycheck.gstatic.com", query_type=1):
    packet = bytearray(b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00")
    for label in name.split("."):
        encoded = label.encode("ascii")
        packet.append(len(encoded))
        packet.extend(encoded)
    packet.extend(b"\x00")
    packet.extend(bytes((query_type >> 8, query_type & 255, 0, 1)))
    return bytes(packet)


class FakeUDP:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.blocking = []
        self.bound = []
        self.received_sizes = []
        self.sent = []
        self.close_calls = 0

    def setblocking(self, value):
        self.blocking.append(value)

    def bind(self, address):
        self.bound.append(address)

    def recvfrom(self, size):
        self.received_sizes.append(size)
        if not self.events:
            raise OSError(11)
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def sendto(self, payload, peer):
        self.sent.append((bytes(payload), peer))
        return len(payload)

    def close(self):
        self.close_calls += 1


class TestCaptiveDNS(unittest.TestCase):
    def test_construction_is_inert_and_start_binds_only_ap_udp_53(self):
        port = FakeUDP()
        calls = []
        server = MicroPythonCaptiveDNS(AP_IP, lambda: calls.append(1) or port)
        self.assertEqual(calls, [])
        self.assertTrue(server.start())
        self.assertEqual(calls, [1])
        self.assertEqual(port.blocking, [False])
        self.assertEqual(port.bound, [(AP_IP, DNS_PORT)])
        self.assertFalse(server.start())

    def test_a_query_returns_ap_address_without_retaining_name(self):
        port = FakeUDP([(query(), PEER)])
        server = MicroPythonCaptiveDNS(AP_IP, lambda: port)
        server.start()
        self.assertTrue(server.step())
        self.assertEqual(len(port.sent), 1)
        response, peer = port.sent[0]
        self.assertEqual(peer, PEER)
        self.assertEqual(response[:2], b"\x12\x34")
        self.assertEqual(response[2:4], b"\x81\x80")
        self.assertEqual(response[6:8], b"\x00\x01")
        self.assertEqual(response[-4:], b"\xc0\xa8\x04\x01")
        self.assertNotIn("gstatic", repr(server.snapshot()))

    def test_aaaa_gets_bounded_noerror_response_without_answer(self):
        response = _response_for(query(query_type=28), b"\xc0\xa8\x04\x01")
        self.assertEqual(response[6:8], b"\x00\x00")
        self.assertLessEqual(len(response), MAX_DNS_PACKET_BYTES)

    def test_malformed_oversize_and_compressed_question_are_ignored(self):
        compressed = query()
        compressed = compressed[:12] + b"\xc0\x0c\x00\x01\x00\x01"
        events = [(b"short", PEER), (b"x" * 513, PEER), (compressed, PEER)]
        port = FakeUDP(events)
        server = MicroPythonCaptiveDNS(AP_IP, lambda: port)
        server.start()
        self.assertFalse(server.step())
        self.assertFalse(server.step())
        self.assertFalse(server.step())
        self.assertEqual(port.sent, [])
        self.assertEqual(server.snapshot()["ignored"], 3)

    def test_would_block_is_inert_and_deinit_closes(self):
        port = FakeUDP()
        server = MicroPythonCaptiveDNS(AP_IP, lambda: port)
        server.start()
        self.assertFalse(server.step())
        self.assertEqual(port.received_sizes, [MAX_DNS_PACKET_BYTES + 1])
        self.assertIsNone(server.deinit())
        self.assertEqual(port.close_calls, 1)
        self.assertFalse(server.step())
        with self.assertRaises(CaptiveDNSError):
            server.start()

    def test_rejects_wildcard_multicast_and_noncanonical_addresses(self):
        for value in ("0.0.0.0", "224.0.0.1", "255.255.255.255", "192.168.04.1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MicroPythonCaptiveDNS(value)


if __name__ == "__main__":
    unittest.main()
