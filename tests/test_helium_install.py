"""Configuration regression checks; no browser or GPU required."""

import unittest
from pathlib import Path
from scripts.install_helium import updated_flags


class HeliumFlags(unittest.TestCase):
    def test_preserves_existing_extensions_and_features(self):
        original = "# keep this\n--load-extension=/tmp/other\n--enable-features=ExistingFeature\n--disable-features=OtherFeature\n--custom-flag\n"
        output = updated_flags(original, Path("/tmp/vsr extension"))
        self.assertIn("--load-extension=/tmp/other,/tmp/vsr extension", output)
        self.assertIn(
            "--enable-features=ExistingFeature,AcceleratedVideoDecodeLinuxGL,VaapiOnNvidiaGPUs",
            output,
        )
        self.assertIn("--disable-features=OtherFeature,Vulkan", output)
        self.assertIn("# keep this\n", output)
        self.assertIn("--custom-flag\n", output)
        self.assertEqual(output, updated_flags(output, Path("/tmp/vsr extension")))

    def test_refuses_ambiguous_multiple_arguments_on_one_line(self):
        with self.assertRaises(ValueError):
            updated_flags("--load-extension=/tmp/a --other\n", Path("/tmp/vsr"))


if __name__ == "__main__":
    unittest.main()
