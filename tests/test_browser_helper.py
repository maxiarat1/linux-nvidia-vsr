"""Browser control protocol and extension build-identity regression checks."""

import io
import struct
import unittest
from scripts.extension_build import local_version
from scripts.native_host import handle, read_message, write_message


class BrowserHelper(unittest.TestCase):
    def test_worker_changes_also_change_chromium_version(self):
        previous = {"version": "0.8.50000.65000", "digest": "old"}
        first = local_version("0.9.0", "ffff12340000", previous)
        previous = {"version": first, "digest": "ffff12340000"}
        second = local_version("0.9.0", "000012340000", previous)
        parts = lambda value: tuple(map(int, value.split(".")))
        self.assertGreater(parts(first), parts("0.8.50000.65000"))
        self.assertGreater(parts(second), parts(first))
        self.assertEqual(first, local_version("0.9.0", "ffff12340000", previous))
        self.assertTrue(all(0 <= int(part) <= 65535 for part in first.split(".")))

    def test_builds_never_downgrade_on_branch_switch_or_counter_rollover(self):
        previous = {"version": "0.9.1.65535", "digest": "previous"}
        self.assertEqual(local_version("0.8.0", "next", previous), "0.9.2.0")

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
