import errno
import inspect
import json
import os
import random
import tempfile
import unittest
from unittest import mock

import adapters.config_file_store as store_module
from adapters.config_file_store import (
    AtomicJSONConfigStore,
    ConfigStoreConflictError,
    ConfigStoreDurabilityError,
    ConfigStoreError,
    ConfigStoreFormatError,
)


_DEFAULT = object()


class RenameThenRaise:
    def __init__(self, error):
        self.error = error


class MemoryStream:
    def __init__(self, filesystem, path, mode):
        self._filesystem = filesystem
        self._path = path
        self._mode = mode
        self._offset = 0
        if mode == "wb":
            filesystem.files[path] = b""
        elif mode != "rb":
            raise AssertionError("unexpected mode {}".format(mode))

    def read(self, size=-1):
        self._filesystem.calls.append(("read", self._path, size))
        data = self._filesystem.files[self._path][self._offset:]
        if size >= 0:
            data = data[:size]
        action = self._filesystem.take_action("read", _DEFAULT)
        if action is _DEFAULT:
            result = bytes(data)
        elif callable(action):
            result = action(bytes(data))
        else:
            result = action
        if type(result) is bytes:
            self._offset += len(result)
        return result

    def write(self, data):
        data = bytes(data)
        self._filesystem.calls.append(("write", self._path, len(data)))
        action = self._filesystem.take_action("write", _DEFAULT)
        written = len(data) if action is _DEFAULT else action
        if type(written) is int and 0 < written <= len(data):
            self._filesystem.files[self._path] += data[:written]
        return written

    def seek(self, offset):
        if self._mode != "rb" or type(offset) is not int or offset < 0:
            raise OSError("invalid seek")
        self._offset = offset
        return offset

    def flush(self):
        self._filesystem.calls.append(("flush", self._path))
        action = self._filesystem.take_action("flush", _DEFAULT)
        return None if action is _DEFAULT else action

    def close(self):
        self._filesystem.calls.append(("close", self._path, self._mode))
        action = self._filesystem.take_action("close", _DEFAULT)
        return None if action is _DEFAULT else action


class MemoryFileSystem:
    def __init__(self):
        self.files = {}
        self.calls = []
        self.plans = {}

    def plan(self, operation, *actions):
        self.plans[operation] = list(actions)

    def take_action(self, operation, default):
        actions = self.plans.get(operation)
        action = actions.pop(0) if actions else default
        if isinstance(action, BaseException):
            raise action
        return action

    def count(self, operation):
        return sum(1 for call in self.calls if call[0] == operation)

    def open(self, path, mode):
        self.calls.append(("open", path, mode))
        action = self.take_action("open", _DEFAULT)
        if action is not _DEFAULT:
            return action
        if mode == "rb" and path not in self.files:
            raise OSError(errno.ENOENT, "missing")
        return MemoryStream(self, path, mode)

    def stat(self, path):
        self.calls.append(("stat", path))
        action = self.take_action("stat", _DEFAULT)
        if action is not _DEFAULT:
            return action
        if path not in self.files:
            raise OSError(errno.ENOENT, "missing")
        return (0, 0, 0, 0, 0, 0, len(self.files[path]), 0, 0, 0)

    def remove(self, path):
        self.calls.append(("remove", path))
        action = self.take_action("remove", _DEFAULT)
        if path not in self.files:
            raise OSError(errno.ENOENT, "missing")
        del self.files[path]
        return None if action is _DEFAULT else action

    def rename(self, source, target):
        self.calls.append(("rename", source, target))
        action = self.take_action("rename", _DEFAULT)
        if isinstance(action, RenameThenRaise):
            self.files[target] = self.files.pop(source)
            raise action.error
        if source not in self.files:
            raise OSError(errno.ENOENT, "missing")
        self.files[target] = self.files.pop(source)
        return None if action is _DEFAULT else action

    def sync(self):
        self.calls.append(("sync",))
        action = self.take_action("sync", _DEFAULT)
        return None if action is _DEFAULT else action


def raw_record(
    payload_bytes,
    generation=1,
    checksum=None,
    footer_generation=None,
    footer_length=None,
    footer_checksum=None,
    trailing=b"",
):
    if checksum is None:
        checksum = store_module._crc32(payload_bytes)
    if footer_generation is None:
        footer_generation = generation
    if footer_length is None:
        footer_length = len(payload_bytes)
    if footer_checksum is None:
        footer_checksum = checksum
    header = "{}|{}|{}|{:08x}\n".format(
        store_module._MAGIC,
        generation,
        len(payload_bytes),
        checksum,
    ).encode("ascii")
    footer = "\n{}|{}|{}|{:08x}\n".format(
        store_module._FOOTER_MAGIC,
        footer_generation,
        footer_length,
        footer_checksum,
    ).encode("ascii")
    return header + payload_bytes + footer + trailing


def segmented_record(data):
    record = store_module._SegmentedRecord(len(data))
    offset = 0
    for chunk in record.iter_chunks():
        chunk[:] = data[offset:offset + len(chunk)]
        offset += len(chunk)
    return record


class TestConfigFileStoreImportAndConstruction(unittest.TestCase):
    def test_module_execution_and_construction_perform_no_io(self):
        source = inspect.getsource(store_module)
        source_file = inspect.getsourcefile(store_module)
        code = compile(source, source_file, "exec")

        def forbidden(*_args, **_kwargs):
            raise AssertionError("configuration storage performed eager I/O")

        with mock.patch("builtins.open", side_effect=forbidden), mock.patch.object(
            os, "stat", side_effect=forbidden
        ), mock.patch.object(os, "remove", side_effect=forbidden), mock.patch.object(
            os, "rename", side_effect=forbidden
        ), mock.patch.object(os, "sync", side_effect=forbidden, create=True):
            namespace = {
                "__file__": source_file,
                "__name__": "config_file_store_import_test",
            }
            exec(code, namespace)
            namespace["AtomicJSONConfigStore"]("/config")

        filesystem = MemoryFileSystem()
        store = AtomicJSONConfigStore("/config", filesystem=filesystem)
        self.assertEqual(filesystem.calls, [])
        self.assertEqual(store.base_path, "/config")

    def test_constructor_rejects_invalid_ports_without_calling_them(self):
        for filesystem in (object(), None):
            if filesystem is None:
                continue
            with self.subTest(filesystem=filesystem):
                with self.assertRaises(ValueError):
                    AtomicJSONConfigStore("/config", filesystem=filesystem)

        for path in (None, "", "/", "x\x00y", "x" * 193):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    AtomicJSONConfigStore(path, filesystem=MemoryFileSystem())

    def test_default_filesystem_fails_closed_when_os_sync_is_absent(self):
        with mock.patch.object(store_module._os, "sync", None):
            with self.assertRaises(ConfigStoreDurabilityError):
                store_module._DefaultFileSystem()
            with self.assertRaises(ConfigStoreDurabilityError):
                AtomicJSONConfigStore("/config")


class TestConfigRecordFormat(unittest.TestCase):
    def test_crc32_known_vector_and_canonical_roundtrip(self):
        self.assertEqual(store_module._crc32(b"123456789"), 0xCBF43926)
        payload = {"alpha": 1, "items": [True, None, "z"]}
        encoded = store_module._encode_record(payload, 7, 1024)
        self.assertIn(b'{"alpha":1,"items":[true,null,"z"]}', encoded)
        decoded = store_module._decode_record(encoded, 1024)
        self.assertEqual(decoded["generation"], 7)
        self.assertEqual(decoded["payload"], payload)
        self.assertEqual(decoded["fingerprint"], store_module._crc32(
            b'{"alpha":1,"items":[true,null,"z"]}'
        ))

    def test_canonical_json_sorts_keys_and_escapes_without_runtime_dict_order(self):
        payload = {
            "z": "line\nquote\"slash\\\u0001",
            "a": {"two": 2, "one": 1},
        }
        encoded = store_module._canonical_json_bytes(payload)
        self.assertEqual(
            encoded,
            b'{"a":{"one":1,"two":2},"z":"line\\nquote\\"slash\\\\\\u0001"}',
        )
        record_bytes = store_module._encode_record(payload, 1, 1024)
        self.assertEqual(
            store_module._decode_record(record_bytes, 1024)["payload"],
            payload,
        )

        for invalid in (1.0, float("nan"), (1, 2), {1: "bad-key"}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ConfigStoreFormatError):
                    store_module._canonical_json_bytes(invalid)

    def test_preallocated_encoder_matches_stdlib_reference_and_exact_size(self):
        boundary_text = "".join(chr(value) for value in (
            0x00, 0x08, 0x09, 0x0A, 0x0C, 0x0D, 0x1F,
            0x22, 0x5C, 0x7F, 0x80, 0x7FF, 0x800,
            0xD7FF, 0xE000, 0xFFFF, 0x10000, 0x10FFFF,
        ))
        payload = {
            "z": [None, False, True, -2147483648, boundary_text],
            "a": {"quote\"": "slash\\", "empty": ""},
        }
        reference = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        measured = store_module._canonical_json_size(payload)
        buffer = store_module._canonical_json_buffer(payload)
        encoded = store_module._canonical_json_bytes(payload)
        self.assertEqual(measured, len(reference))
        self.assertEqual(len(buffer), measured)
        self.assertEqual(bytes(buffer), reference)
        self.assertEqual(encoded, reference)

    def test_canonical_encoder_reference_fuzz(self):
        generator = random.Random(0x1A2B3C4D)
        alphabet = (
            "", "a", "Z", "0", " ", '"', "\\", "\b", "\f",
            "\n", "\r", "\t", "\x00", "\x01", "\x1f",
            "\x7f", "é", "中", "😀", "\u07ff", "\u0800",
        )

        def random_text(maximum):
            return "".join(
                generator.choice(alphabet)
                for _ in range(generator.randrange(maximum + 1))
            )

        def random_value(depth=0):
            scalar_choice = generator.randrange(5)
            if depth >= 4 or scalar_choice < 3:
                if scalar_choice == 0:
                    return None
                if scalar_choice == 1:
                    return bool(generator.randrange(2))
                if scalar_choice == 2:
                    return generator.randrange(-0x7FFFFFFF, 0x7FFFFFFF)
                return random_text(8)
            if scalar_choice == 3:
                return [
                    random_value(depth + 1)
                    for _ in range(generator.randrange(5))
                ]
            result = {}
            while len(result) < generator.randrange(5):
                result[random_text(6)] = random_value(depth + 1)
            return result

        for index in range(500):
            payload = random_value()
            with self.subTest(index=index):
                reference = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.assertEqual(
                    store_module._canonical_json_bytes(payload), reference
                )
                self.assertEqual(
                    store_module._canonical_json_size(payload), len(reference)
                )
                if index < 100:
                    record = store_module._encode_record(
                        payload, index + 1, 4096
                    )
                    direct = store_module._decode_record(record, 4096)
                    streamed = store_module._decode_segmented_record(
                        segmented_record(record), 4096
                    )
                    self.assertEqual(streamed, direct)

    def test_canonical_encoder_rejects_surrogates_and_excess_depth(self):
        with self.assertRaisesRegex(ConfigStoreFormatError, "UTF-8"):
            store_module._canonical_json_bytes("\ud800")

        value = 0
        for _ in range(store_module._MAX_JSON_DEPTH):
            value = [value]
        self.assertIsInstance(store_module._canonical_json_bytes(value), bytes)
        value = [value]
        with self.assertRaisesRegex(ConfigStoreFormatError, "deeply nested"):
            store_module._canonical_json_bytes(value)

    def test_noncanonical_json_and_duplicate_keys_are_rejected_with_valid_crc(self):
        variants = (
            b'{"alpha":1, "beta":2}',
            b'{ "alpha":1,"beta":2}',
            b'{"alpha":1,"alpha":2}',
        )
        for payload_bytes in variants:
            with self.subTest(payload_bytes=payload_bytes):
                with self.assertRaises(ConfigStoreFormatError):
                    store_module._decode_record(
                        raw_record(payload_bytes), 1024
                    )
                with self.assertRaises(ConfigStoreFormatError):
                    store_module._decode_segmented_record(
                        segmented_record(raw_record(payload_bytes)), 1024
                    )

    def test_crc_footer_length_and_trailing_corruption_are_rejected(self):
        payload_bytes = b'{"alpha":1}'
        valid = raw_record(payload_bytes)
        variants = (
            valid.replace(b'"alpha":1', b'"alpha":2'),
            raw_record(payload_bytes, footer_generation=2),
            raw_record(payload_bytes, footer_length=len(payload_bytes) + 1),
            raw_record(
                payload_bytes,
                footer_checksum=store_module._crc32(payload_bytes) ^ 1,
            ),
            raw_record(payload_bytes, trailing=b"x"),
            valid[:-1],
        )
        for record in variants:
            with self.subTest(record=record):
                with self.assertRaises(ConfigStoreFormatError):
                    store_module._decode_record(record, 1024)

    def test_record_size_is_bounded_on_encode_decode_and_file_load(self):
        with self.assertRaises(ConfigStoreFormatError):
            store_module._encode_record({"value": "x" * 300}, 1, 256)
        with self.assertRaises(ConfigStoreFormatError):
            store_module._decode_record(b"x" * 257, 256)

        filesystem = MemoryFileSystem()
        filesystem.files["/config.a"] = b"x" * 257
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=256, filesystem=filesystem
        )
        self.assertEqual(store.load_records(), ())
        status = store.status()
        self.assertEqual(status["invalid_slots"], 1)
        self.assertEqual(status["slot_files_present"], 1)

    def test_oversize_and_encoding_oom_happen_before_filesystem_io(self):
        filesystem = MemoryFileSystem()
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=256, filesystem=filesystem
        )
        with self.assertRaisesRegex(ConfigStoreFormatError, "size limit"):
            store.commit({"value": "x" * 10000}, 1, 0)
        self.assertEqual(filesystem.calls, [])

        with mock.patch.object(
            store_module,
            "_write_canonical_json",
            side_effect=MemoryError("canonical buffer oom"),
        ):
            with self.assertRaises(MemoryError):
                store.commit({"value": "small"}, 1, 0)
        self.assertEqual(filesystem.calls, [])

    def test_large_preallocated_record_roundtrips_without_format_change(self):
        payload = {
            "schema_version": 2,
            "blob": ("Zürich-😀-line\nquote\"slash\\" * 230),
            "items": list(range(64)),
        }
        record = store_module._encode_record(payload, 2147483647, 8192)
        decoded = store_module._decode_record(record, 8192)
        self.assertEqual(decoded["payload"], payload)
        self.assertEqual(decoded["generation"], 2147483647)
        header_end = record.index(b"\n")
        payload_start = header_end + 1
        payload_end = payload_start + len(
            store_module._canonical_json_bytes(payload)
        )
        self.assertEqual(
            record[payload_start:payload_end],
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )

    def test_decode_compares_without_a_payload_sized_canonical_buffer(self):
        payload = {
            "schema_version": 2,
            "blob": ("Zürich-😀-line\nquote\"slash\\" * 230),
            "items": list(range(64)),
        }
        record = store_module._encode_record(payload, 1, 8192)
        payload_length = len(store_module._canonical_json_bytes(payload))
        original_size = store_module._canonical_json_size
        with mock.patch.object(
            store_module,
            "_canonical_json_bytes",
            side_effect=AssertionError("decoder made an immutable copy"),
        ), mock.patch.object(
            store_module,
            "_canonical_json_buffer",
            side_effect=AssertionError("decoder made a canonical buffer"),
        ), mock.patch.object(
            store_module,
            "_canonical_json_size",
            wraps=original_size,
        ) as canonical_size:
            decoded = store_module._decode_record(record, 8192)

        self.assertEqual(decoded["payload"], payload)
        self.assertGreaterEqual(canonical_size.call_count, 1)
        self.assertEqual(
            canonical_size.call_args_list[0].kwargs,
            {"maximum": payload_length},
        )

    def test_large_config_commits_and_fresh_store_reloads(self):
        payload = {
            "schema_version": 2,
            "blob": ("Zürich-😀-line\nquote\"slash\\" * 230),
            "items": list(range(64)),
        }
        filesystem = MemoryFileSystem()
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=8192, filesystem=filesystem
        )
        self.assertTrue(store.commit(payload, 1, 0))

        fresh_store = AtomicJSONConfigStore(
            "/config", max_record_bytes=8192, filesystem=filesystem
        )
        records = fresh_store.load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["generation"], 1)
        self.assertEqual(records[0]["payload"], payload)

    def test_commit_uses_mutable_record_without_immutable_encoder_copy(self):
        payload = {"schema_version": 2, "value": "bounded"}
        expected = store_module._encode_record(payload, 1, 1024)
        self.assertEqual(
            store_module._encode_record_buffer(
                payload, 1, 1024
            ).to_bytes(),
            expected,
        )
        filesystem = MemoryFileSystem()
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=1024, filesystem=filesystem
        )
        with mock.patch.object(
            store_module,
            "_encode_record",
            side_effect=MemoryError("immutable copy unavailable"),
        ):
            self.assertTrue(store.commit(payload, 1, 0))
        self.assertEqual(filesystem.files["/config.a"], expected)

    def test_strict_decoder_still_rejects_mutable_external_record(self):
        record_buffer = store_module._encode_record_buffer(
            {"value": "safe"}, 1, 1024
        )
        with self.assertRaisesRegex(ConfigStoreFormatError, "immutable bytes"):
            store_module._decode_record(record_buffer, 1024)

    def test_bootstrap_second_generation_uses_chunked_first_slot_receipt(self):
        payload = {
            "schema_version": 2,
            "blob": ("Zürich-😀-line\nquote\"slash\\" * 230),
            "items": list(range(64)),
        }
        filesystem = MemoryFileSystem()
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=8192, filesystem=filesystem
        )
        self.assertTrue(store.commit(payload, 1, 0))
        with mock.patch.object(
            store,
            "_read_bytes",
            side_effect=MemoryError("contiguous record unavailable"),
        ):
            self.assertTrue(store.commit(payload, 2, 1))
        self.assertEqual(
            store_module._decode_record(filesystem.files["/config.a"], 8192)[
                "payload"
            ],
            payload,
        )
        self.assertEqual(
            store_module._decode_record(filesystem.files["/config.b"], 8192)[
                "payload"
            ],
            payload,
        )
        fresh_store = AtomicJSONConfigStore(
            "/config", max_record_bytes=8192, filesystem=filesystem
        )
        with mock.patch.object(
            fresh_store,
            "_read_bytes",
            side_effect=MemoryError("contiguous record unavailable"),
        ):
            records = fresh_store.load_records()
        self.assertEqual([item["generation"] for item in records], [1, 2])
        self.assertIs(records[0]["payload"], records[1]["payload"])
        self.assertIs(
            records[0]["canonical_payload"],
            records[1]["canonical_payload"],
        )

    def test_nonbytes_file_read_is_invalid_not_trusted(self):
        filesystem = MemoryFileSystem()
        filesystem.files["/config.a"] = store_module._encode_record(
            {"value": 1}, 1, 1024
        )
        filesystem.plan("read", "not-bytes")
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=1024, filesystem=filesystem
        )
        self.assertEqual(store.load_records(), ())
        self.assertEqual(store.status()["invalid_slots"], 1)

    def test_file_read_is_segmented_and_detects_change(self):
        filesystem = MemoryFileSystem()
        encoded = store_module._encode_record({"value": 1}, 1, 1024)
        filesystem.files["/config.a"] = encoded
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=1024, filesystem=filesystem
        )
        self.assertEqual(store.load_records()[0]["payload"], {"value": 1})
        read_calls = [call for call in filesystem.calls if call[0] == "read"]
        self.assertEqual(read_calls[-2][2], len(encoded))
        self.assertEqual(read_calls[-1][2], 1)
        self.assertLessEqual(
            max(call[2] for call in read_calls),
            store_module._RECORD_CHUNK_BYTES,
        )

        filesystem.plan("read", lambda value: value[:-1])
        self.assertEqual(store.load_records(), ())
        self.assertEqual(store.status()["invalid_slots"], 1)


class TestConfigFileStoreGenerations(unittest.TestCase):
    def setUp(self):
        self.filesystem = MemoryFileSystem()
        self.store = AtomicJSONConfigStore(
            "/config", max_record_bytes=4096, filesystem=self.filesystem
        )

    def test_first_commit_uses_slot_a_and_roundtrips(self):
        payload = {"schema_version": 1, "value": "first"}
        self.assertTrue(self.store.commit(payload, 1, 0))
        self.assertIn("/config.a", self.filesystem.files)
        self.assertNotIn("/config.b", self.filesystem.files)
        self.assertNotIn("/config.tmp", self.filesystem.files)
        records = self.store.load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["slot"], "a")
        self.assertEqual(records[0]["generation"], 1)
        self.assertEqual(records[0]["payload"], payload)
        status = self.store.status()
        self.assertEqual(status["last_target_slot"], "a")
        self.assertEqual(status["writes"], 1)
        self.assertFalse(status["durability_unknown"])

    def test_generations_alternate_and_keep_the_previous_generation(self):
        payloads = (
            {"value": "one"},
            {"value": "two"},
            {"value": "three"},
            {"value": "four"},
        )
        for index, payload in enumerate(payloads, 1):
            self.assertTrue(self.store.commit(payload, index, index - 1))
        records = {record["slot"]: record for record in self.store.load_records()}
        self.assertEqual(records["a"]["generation"], 3)
        self.assertEqual(records["a"]["payload"], payloads[2])
        self.assertEqual(records["b"]["generation"], 4)
        self.assertEqual(records["b"]["payload"], payloads[3])
        targets = [call[2] for call in self.filesystem.calls if call[0] == "rename"]
        self.assertEqual(targets, [
            "/config.a", "/config.b", "/config.a", "/config.b"
        ])

    def test_valid_temp_is_diagnostic_only_and_never_loaded(self):
        self.filesystem.files["/config.a"] = store_module._encode_record(
            {"value": "slot"}, 1, 4096
        )
        self.filesystem.files["/config.tmp"] = store_module._encode_record(
            {"value": "temp"}, 99, 4096
        )
        records = self.store.load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["generation"], 1)
        self.assertEqual(records[0]["payload"], {"value": "slot"})
        self.assertTrue(self.store.status()["temp_present"])

        del self.filesystem.files["/config.a"]
        self.assertEqual(self.store.load_records(), ())
        self.assertTrue(self.store.status()["temp_present"])

    def test_stale_expected_generation_and_generation_leap_never_publish(self):
        self.store.commit({"value": 1}, 1, 0)
        self.filesystem.calls = []
        with self.assertRaises(ConfigStoreConflictError):
            self.store.commit({"value": 2}, 1, 0)
        self.assertEqual(self.filesystem.count("write"), 0)
        self.assertEqual(self.filesystem.count("rename"), 0)
        self.assertEqual(self.filesystem.count("sync"), 0)

        self.filesystem.calls = []
        with self.assertRaises(ConfigStoreConflictError):
            self.store.commit({"value": 3}, 3, 1)
        self.assertEqual(self.filesystem.calls, [])

    def test_equal_generation_split_brain_is_rejected_without_publish(self):
        self.filesystem.files["/config.a"] = store_module._encode_record(
            {"value": "a"}, 1, 4096
        )
        self.filesystem.files["/config.b"] = store_module._encode_record(
            {"value": "b"}, 1, 4096
        )
        with self.assertRaises(ConfigStoreConflictError):
            self.store.commit({"value": "new"}, 2, 1)
        self.assertEqual(self.filesystem.count("write"), 0)
        self.assertEqual(self.filesystem.count("rename"), 0)

    def test_identical_mirror_generation_can_advance_safely(self):
        record = store_module._encode_record({"value": "same"}, 1, 4096)
        self.filesystem.files["/config.a"] = record
        self.filesystem.files["/config.b"] = record
        self.assertTrue(self.store.commit({"value": "new"}, 2, 1))
        records = {item["slot"]: item for item in self.store.load_records()}
        self.assertEqual(records["a"]["generation"], 1)
        self.assertEqual(records["b"]["generation"], 2)

    def test_identical_payload_is_still_an_explicit_new_store_generation(self):
        payload = {"value": "same"}
        self.assertTrue(self.store.commit(payload, 1, 0))
        self.filesystem.calls = []
        self.assertTrue(self.store.commit(payload, 2, 1))
        self.assertGreater(self.filesystem.count("write"), 0)
        self.assertEqual(self.filesystem.count("rename"), 1)
        records = sorted(
            self.store.load_records(), key=lambda item: item["generation"]
        )
        self.assertEqual([item["generation"] for item in records], [1, 2])
        self.assertEqual([item["payload"] for item in records], [payload, payload])

    def test_explicit_reseal_replaces_split_brain_with_two_safe_generations(self):
        self.filesystem.files["/config.a"] = store_module._encode_record(
            {"value": "old-a"}, 7, 4096
        )
        self.filesystem.files["/config.b"] = store_module._encode_record(
            {"value": "old-b"}, 7, 4096
        )
        recovered = {"value": "explicit-recovery"}
        self.assertEqual(self.store.reseal(recovered), 9)
        records = sorted(
            self.store.load_records(), key=lambda item: item["generation"]
        )
        self.assertEqual([item["generation"] for item in records], [8, 9])
        self.assertEqual(
            [item["payload"] for item in records], [recovered, recovered]
        )
        self.assertEqual(self.store.status()["writes"], 2)

    def test_reseal_failure_keeps_a_durable_safe_candidate(self):
        self.filesystem.files["/config.a"] = store_module._encode_record(
            {"value": "old-a"}, 1, 4096
        )
        self.filesystem.files["/config.b"] = store_module._encode_record(
            {"value": "old-b"}, 1, 4096
        )
        self.filesystem.plan(
            "sync", None, None, OSError(errno.EIO, "second stage failed")
        )
        recovered = {"value": "safe"}
        with self.assertRaises(OSError):
            self.store.reseal(recovered)
        self.assertEqual(self.store.status()["last_error"], "commit_failed")
        records = sorted(
            self.store.load_records(), key=lambda item: item["generation"]
        )
        self.assertEqual([item["generation"] for item in records], [1, 2])
        self.assertEqual(records[-1]["payload"], recovered)

    def test_reseal_propagates_transient_slot_io_before_any_publish(self):
        old_bytes = store_module._encode_record(
            {"value": "old-enabled-state"}, 2, 4096
        )
        self.filesystem.files["/config.b"] = old_bytes
        self.filesystem.plan("open", OSError(errno.EIO, "transient read"))

        with self.assertRaises(OSError):
            self.store.reseal({"value": "safe-recovery"})

        self.assertEqual(self.filesystem.files["/config.b"], old_bytes)
        self.assertNotIn("/config.a", self.filesystem.files)
        self.assertNotIn("/config.tmp", self.filesystem.files)
        self.assertEqual(self.filesystem.count("write"), 0)
        self.assertEqual(self.filesystem.count("rename"), 0)

    def test_reseal_replaces_a_corrupt_slot_before_publishing_its_peer(self):
        self.filesystem.files["/config.b"] = b"corrupt-old-state"

        self.assertEqual(
            self.store.reseal({"value": "safe-recovery"}), 2
        )
        records = sorted(
            self.store.load_records(), key=lambda item: item["generation"]
        )
        self.assertEqual([item["generation"] for item in records], [1, 2])
        self.assertEqual(
            [item["payload"] for item in records],
            [{"value": "safe-recovery"}, {"value": "safe-recovery"}],
        )
        rename_targets = [
            call[2] for call in self.filesystem.calls if call[0] == "rename"
        ]
        self.assertEqual(rename_targets, ["/config.b", "/config.a"])
        self.assertNotIn("/config.tmp", self.filesystem.files)

    def test_reseal_preencodes_both_actual_generations_before_publish(self):
        filesystem = MemoryFileSystem()
        store = AtomicJSONConfigStore(
            "/config", max_record_bytes=256, filesystem=filesystem
        )
        old_bytes = store_module._encode_record({"value": "old"}, 8, 256)
        filesystem.files["/config.b"] = old_bytes
        candidate = {"x": "a" * 175}

        with self.assertRaises(ConfigStoreFormatError):
            store.reseal(candidate)

        self.assertEqual(filesystem.files["/config.b"], old_bytes)
        self.assertNotIn("/config.a", filesystem.files)
        self.assertNotIn("/config.tmp", filesystem.files)
        self.assertEqual(filesystem.count("write"), 0)
        self.assertEqual(filesystem.count("rename"), 0)

    def test_reseal_second_record_allocation_failure_precedes_publish(self):
        old_bytes = store_module._encode_record(
            {"value": "old"}, 8, 4096
        )
        self.filesystem.files["/config.b"] = old_bytes
        original_encode = store_module._encode_record_buffer
        calls = []

        def fail_second_encode(payload, generation, maximum):
            calls.append(generation)
            if len(calls) == 2:
                raise MemoryError("second record allocation failed")
            return original_encode(payload, generation, maximum)

        with mock.patch.object(
            store_module,
            "_encode_record_buffer",
            side_effect=fail_second_encode,
        ):
            with self.assertRaises(MemoryError):
                self.store.reseal({"value": "safe"})

        self.assertEqual(calls, [9, 10])
        self.assertEqual(self.filesystem.files["/config.b"], old_bytes)
        self.assertNotIn("/config.a", self.filesystem.files)
        self.assertNotIn("/config.tmp", self.filesystem.files)
        self.assertEqual(self.filesystem.count("write"), 0)
        self.assertEqual(self.filesystem.count("rename"), 0)

    def test_two_unreadable_slots_are_synced_away_before_low_generation(self):
        self.filesystem.files["/config.a"] = store_module._encode_record(
            {"value": "old-safe"}, 2, 4096
        )
        self.filesystem.files["/config.b"] = store_module._encode_record(
            {"value": "old-enabled"}, 2, 4096
        )
        self.filesystem.plan(
            "read",
            lambda value: value[:-1],
            lambda value: value[:-1],
        )
        # Two deletion syncs, then the complete first publish.  Fail before
        # the second record is published.
        self.filesystem.plan(
            "sync",
            None,
            None,
            None,
            None,
            OSError(errno.EIO, "second recovery publish failed"),
        )

        with self.assertRaises(OSError):
            self.store.reseal({"value": "safe-recovery"})

        self.assertNotIn("/config.b", self.filesystem.files)
        records = self.store.load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["slot"], "a")
        self.assertEqual(records[0]["generation"], 1)
        self.assertEqual(records[0]["payload"], {"value": "safe-recovery"})

    def test_reseal_signature_rejects_a_changed_recovery_view(self):
        self.filesystem.files["/config.a"] = store_module._encode_record(
            {"value": "observed"}, 7, 4096
        )
        _, signature = self.store.inspect_recovery()
        self.filesystem.files["/config.a"] = store_module._encode_record(
            {"value": "changed"}, 7, 4096
        )

        with self.assertRaises(ConfigStoreConflictError):
            self.store.reseal({"value": "safe"}, signature)

        self.assertEqual(self.filesystem.count("write"), 0)
        self.assertEqual(self.filesystem.count("rename"), 0)


class TestConfigFileStoreIOContracts(unittest.TestCase):
    def make_store(self, filesystem=None):
        if filesystem is None:
            filesystem = MemoryFileSystem()
        return filesystem, AtomicJSONConfigStore(
            "/config", max_record_bytes=4096, filesystem=filesystem
        )

    def test_positive_partial_writes_are_completed_exactly(self):
        filesystem, store = self.make_store()
        filesystem.plan("write", 1, 2, 3, 5)
        payload = {"value": "partial-write"}
        self.assertTrue(store.commit(payload, 1, 0))
        self.assertGreater(filesystem.count("write"), 4)
        self.assertEqual(store.load_records()[0]["payload"], payload)

    def test_invalid_write_counts_fail_before_publish(self):
        for result in (None, True, 0, -1, 1000000):
            with self.subTest(result=result):
                filesystem, store = self.make_store()
                filesystem.plan("write", result)
                with self.assertRaises(ConfigStoreError):
                    store.commit({"value": 1}, 1, 0)
                self.assertEqual(filesystem.count("rename"), 0)
                status = store.status()
                self.assertEqual(status["last_error"], "commit_failed")
                self.assertFalse(status["durability_unknown"])

    def test_missing_temp_remove_is_a_true_noop(self):
        filesystem, store = self.make_store()
        self.assertTrue(store.commit({"value": 1}, 1, 0))
        self.assertEqual(filesystem.count("remove"), 0)

    def test_existing_stale_temp_is_removed_before_staging(self):
        filesystem, store = self.make_store()
        filesystem.files["/config.tmp"] = b"stale"
        self.assertTrue(store.commit({"value": 1}, 1, 0))
        self.assertEqual(filesystem.count("remove"), 1)
        self.assertNotIn("/config.tmp", filesystem.files)

    def test_non_none_filesystem_noop_results_are_rejected(self):
        for operation in ("flush", "close", "sync", "remove", "rename"):
            for result in (False, 0, True, object()):
                with self.subTest(operation=operation, result=result):
                    filesystem, store = self.make_store()
                    if operation == "remove":
                        filesystem.files["/config.tmp"] = b"stale"
                    filesystem.plan(operation, result)
                    with self.assertRaises(ConfigStoreError):
                        store.commit({"value": 1}, 1, 0)
                    publish_attempted = operation == "rename"
                    status = store.status()
                    self.assertEqual(
                        status["last_error"],
                        (
                            "durability_unknown"
                            if publish_attempted
                            else "commit_failed"
                        ),
                    )
                    self.assertIs(
                        status["durability_unknown"], publish_attempted
                    )

    def test_partial_write_then_error_preserves_active_slot(self):
        filesystem, store = self.make_store()
        store.commit({"value": "active"}, 1, 0)
        active = filesystem.files["/config.a"]
        filesystem.plan("write", 7, OSError(errno.EIO, "write failed"))
        with self.assertRaises(OSError):
            store.commit({"value": "new"}, 2, 1)
        self.assertEqual(filesystem.files["/config.a"], active)
        self.assertNotIn("/config.b", filesystem.files)
        self.assertIn("/config.tmp", filesystem.files)
        self.assertEqual(store.status()["last_error"], "commit_failed")


class TestConfigFileStoreFaultBoundaries(unittest.TestCase):
    def setUp(self):
        self.filesystem = MemoryFileSystem()
        self.store = AtomicJSONConfigStore(
            "/config", max_record_bytes=4096, filesystem=self.filesystem
        )
        self.store.commit({"value": "active"}, 1, 0)
        self.active_a = self.filesystem.files["/config.a"]
        self.filesystem.calls = []

    def test_fault_before_publish_is_commit_failed_and_keeps_active(self):
        self.filesystem.plan("sync", OSError(errno.EIO, "pre-publish sync"))
        with self.assertRaises(OSError):
            self.store.commit({"value": "new"}, 2, 1)
        self.assertEqual(self.filesystem.files["/config.a"], self.active_a)
        self.assertNotIn("/config.b", self.filesystem.files)
        self.assertEqual(self.filesystem.count("rename"), 0)
        status = self.store.status()
        self.assertEqual(status["last_error"], "commit_failed")
        self.assertFalse(status["durability_unknown"])
        self.assertEqual(status["writes"], 1)

    def test_corrupt_staged_readback_never_reaches_rename(self):
        self.filesystem.plan(
            "read",
            _DEFAULT,
            _DEFAULT,
            lambda value: value[:-1],
        )
        with self.assertRaises(ConfigStoreFormatError):
            self.store.commit({"value": "new"}, 2, 1)
        self.assertEqual(self.filesystem.files["/config.a"], self.active_a)
        self.assertNotIn("/config.b", self.filesystem.files)
        self.assertEqual(self.filesystem.count("rename"), 0)
        self.assertEqual(self.store.status()["last_error"], "commit_failed")

    def test_rename_failure_is_durability_unknown_even_without_side_effect(self):
        self.filesystem.plan("rename", OSError(errno.EIO, "rename failed"))
        with self.assertRaises(OSError):
            self.store.commit({"value": "new"}, 2, 1)
        self.assertEqual(self.filesystem.files["/config.a"], self.active_a)
        self.assertNotIn("/config.b", self.filesystem.files)
        status = self.store.status()
        self.assertEqual(status["last_error"], "durability_unknown")
        self.assertTrue(status["durability_unknown"])
        self.assertEqual(status["writes"], 1)

    def test_rename_side_effect_then_exception_is_durability_unknown(self):
        self.filesystem.plan(
            "rename", RenameThenRaise(OSError(errno.EIO, "late rename error"))
        )
        with self.assertRaises(OSError):
            self.store.commit({"value": "new"}, 2, 1)
        self.assertEqual(self.filesystem.files["/config.a"], self.active_a)
        published = store_module._decode_record(
            self.filesystem.files["/config.b"], 4096
        )
        self.assertEqual(published["generation"], 2)
        self.assertEqual(published["payload"], {"value": "new"})
        self.assertTrue(self.store.status()["durability_unknown"])

    def test_fault_after_publish_preserves_old_active_and_reports_unknown(self):
        self.filesystem.plan("sync", None, OSError(errno.EIO, "final sync"))
        with self.assertRaises(OSError):
            self.store.commit({"value": "new"}, 2, 1)
        self.assertEqual(self.filesystem.files["/config.a"], self.active_a)
        published = store_module._decode_record(
            self.filesystem.files["/config.b"], 4096
        )
        self.assertEqual(published["generation"], 2)
        status = self.store.status()
        self.assertEqual(status["last_error"], "durability_unknown")
        self.assertTrue(status["durability_unknown"])
        self.assertEqual(status["writes"], 1)

        recovered = AtomicJSONConfigStore(
            "/config", max_record_bytes=4096, filesystem=self.filesystem
        )
        records = {item["generation"] for item in recovered.load_records()}
        self.assertEqual(records, {1, 2})

    def test_failed_third_generation_never_modifies_newest_active_slot(self):
        self.store.commit({"value": "second"}, 2, 1)
        active_b = self.filesystem.files["/config.b"]
        self.filesystem.plan("sync", None, OSError(errno.EIO, "final sync"))
        with self.assertRaises(OSError):
            self.store.commit({"value": "third"}, 3, 2)
        self.assertEqual(self.filesystem.files["/config.b"], active_b)
        replaced_old = store_module._decode_record(
            self.filesystem.files["/config.a"], 4096
        )
        self.assertEqual(replaced_old["generation"], 3)
        rename_targets = [
            call[2] for call in self.filesystem.calls if call[0] == "rename"
        ]
        self.assertEqual(rename_targets[-1], "/config.a")
        self.assertTrue(self.store.status()["durability_unknown"])

    def test_two_slot_fault_matrix_never_modifies_the_active_slot(self):
        other_payload = store_module._encode_record(
            {"value": "verified-but-wrong"}, 3, 4096
        )

        def stale_remove(filesystem):
            filesystem.files["/config.tmp"] = b"stale"
            filesystem.plan("remove", OSError(errno.EIO, "remove"))

        cases = (
            ("stale_remove", False, stale_remove),
            (
                "temp_open",
                False,
                lambda fs: fs.plan(
                    "open",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "open"),
                ),
            ),
            (
                "temp_write",
                False,
                lambda fs: fs.plan("write", OSError(errno.EIO, "write")),
            ),
            (
                "temp_flush",
                False,
                lambda fs: fs.plan("flush", OSError(errno.EIO, "flush")),
            ),
            (
                "temp_close",
                False,
                lambda fs: fs.plan(
                    "close",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "close"),
                ),
            ),
            (
                "first_sync",
                False,
                lambda fs: fs.plan("sync", OSError(errno.EIO, "sync")),
            ),
            (
                "staged_open",
                False,
                lambda fs: fs.plan(
                    "open",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "staged open"),
                ),
            ),
            (
                "staged_read",
                False,
                lambda fs: fs.plan(
                    "read",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "read"),
                ),
            ),
            (
                "staged_close",
                False,
                lambda fs: fs.plan(
                    "close",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "staged close"),
                ),
            ),
            (
                "staged_mismatch",
                False,
                lambda fs: fs.plan(
                    "read",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    other_payload,
                ),
            ),
            (
                "rename_before_side_effect",
                True,
                lambda fs: fs.plan("rename", OSError(errno.EIO, "rename")),
            ),
            (
                "rename_after_side_effect",
                True,
                lambda fs: fs.plan(
                    "rename",
                    RenameThenRaise(OSError(errno.EIO, "late rename")),
                ),
            ),
            (
                "final_sync",
                True,
                lambda fs: fs.plan(
                    "sync", None, OSError(errno.EIO, "final sync")
                ),
            ),
            (
                "published_open",
                True,
                lambda fs: fs.plan(
                    "open",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "published open"),
                ),
            ),
            (
                "published_read",
                True,
                lambda fs: fs.plan(
                    "read",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "published read"),
                ),
            ),
            (
                "published_close",
                True,
                lambda fs: fs.plan(
                    "close",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    OSError(errno.EIO, "published close"),
                ),
            ),
            (
                "published_mismatch",
                True,
                lambda fs: fs.plan(
                    "read",
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    _DEFAULT,
                    other_payload,
                ),
            ),
        )
        for name, publish_attempted, prepare in cases:
            with self.subTest(name=name):
                filesystem = MemoryFileSystem()
                store = AtomicJSONConfigStore(
                    "/config", max_record_bytes=4096, filesystem=filesystem
                )
                store.commit({"value": "first"}, 1, 0)
                store.commit({"value": "active"}, 2, 1)
                active_b = filesystem.files["/config.b"]
                slots_before = {
                    key: value
                    for key, value in filesystem.files.items()
                    if key in ("/config.a", "/config.b")
                }
                filesystem.calls = []
                prepare(filesystem)
                expected_error = (
                    ConfigStoreFormatError
                    if name.endswith("mismatch")
                    else OSError
                )
                with self.assertRaises(expected_error):
                    store.commit({"value": "third"}, 3, 2)
                self.assertEqual(filesystem.files["/config.b"], active_b)
                removed_paths = [
                    call[1] for call in filesystem.calls if call[0] == "remove"
                ]
                self.assertFalse(
                    any(path in ("/config.a", "/config.b") for path in removed_paths)
                )
                if not publish_attempted:
                    self.assertEqual(
                        {
                            key: value
                            for key, value in filesystem.files.items()
                            if key in ("/config.a", "/config.b")
                        },
                        slots_before,
                    )
                status = store.status()
                self.assertEqual(
                    status["last_error"],
                    (
                        "durability_unknown"
                        if publish_attempted
                        else "commit_failed"
                    ),
                )
                self.assertIs(
                    status["durability_unknown"], publish_attempted
                )

    def test_baseexceptions_use_the_same_publish_boundary(self):
        for publish_attempted in (False, True):
            with self.subTest(publish_attempted=publish_attempted):
                filesystem = MemoryFileSystem()
                store = AtomicJSONConfigStore(
                    "/config", max_record_bytes=4096, filesystem=filesystem
                )
                store.commit({"value": "active"}, 1, 0)
                active = filesystem.files["/config.a"]
                if publish_attempted:
                    filesystem.plan("sync", None, KeyboardInterrupt())
                else:
                    filesystem.plan("flush", KeyboardInterrupt())
                with self.assertRaises(KeyboardInterrupt):
                    store.commit({"value": "new"}, 2, 1)
                self.assertEqual(filesystem.files["/config.a"], active)
                self.assertIs(
                    store.status()["durability_unknown"], publish_attempted
                )


class TestConfigFileStoreRealFilesystem(unittest.TestCase):
    def test_real_temporary_directory_roundtrip_across_store_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            base_path = os.path.join(directory, "landy-config")
            store = AtomicJSONConfigStore(base_path, max_record_bytes=4096)
            payloads = (
                {"schema_version": 1, "value": "one"},
                {"schema_version": 1, "value": "two"},
                {"schema_version": 1, "value": "three"},
            )
            for generation, payload in enumerate(payloads, 1):
                self.assertTrue(
                    store.commit(payload, generation, generation - 1)
                )

            self.assertFalse(os.path.exists(base_path + ".tmp"))
            reopened = AtomicJSONConfigStore(
                base_path, max_record_bytes=4096
            )
            records = sorted(
                reopened.load_records(), key=lambda item: item["generation"]
            )
            self.assertEqual(
                [item["generation"] for item in records], [2, 3]
            )
            self.assertEqual(records[0]["payload"], payloads[1])
            self.assertEqual(records[1]["payload"], payloads[2])
            self.assertEqual({item["slot"] for item in records}, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
