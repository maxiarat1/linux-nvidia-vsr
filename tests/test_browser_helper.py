"""Browser control protocol and extension build-identity regression checks."""

import io
import struct
import unittest
from scripts.extension_build import local_version
from scripts.native_host import handle, read_message, write_message


class BrowserHelper(unittest.TestCase):
    def test_worker_changes_also_change_chromium_version(self):
        first = local_version("0.8.0", "abc123450000")
        second = local_version("0.8.0", "def123450000")
        self.assertNotEqual(first, second)
        self.assertEqual(first, local_version("0.8.0", "abc123450000"))
        self.assertTrue(all(0 <= int(part) <= 65535 for part in first.split(".")))

    def test_native_protocol_roundtrip_and_bounds(self):
        data = io.BytesIO()
        write_message(data, {"action": "status"})
        data.seek(0)
        self.assertEqual(read_message(data), {"action": "status"})
        self.assertIsNone(read_message(io.BytesIO()))
        for payload in [
            b"\0",
            struct.pack("=I", 10000000),
            struct.pack("=I", 100) + b"{}",
        ]:
            with self.assertRaises(ValueError):
                read_message(io.BytesIO(payload))

    def test_only_fixed_control_actions_are_accepted(self):
        for message in [
            {"action": "exec", "command": "anything"},
            [],
            None,
            {"action": "stop"},
        ]:
            with self.assertRaises(ValueError):
                handle(message)


if __name__ == "__main__":
    unittest.main()
