import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "FGO-py"))
from fgo_agent.transport import AdbEmulator, PACKAGE, resumed_package


class TransportTests(unittest.TestCase):
    def test_foreground_parser_ignores_historical_activity(self):
        self.assertIsNone(resumed_package(f"Hist #0: ActivityRecord{{x {PACKAGE}/.Main}}"))
        self.assertEqual(resumed_package(f"mResumedActivity: ActivityRecord{{x u0 {PACKAGE}/.Main t1}}"), PACKAGE)

    def test_readonly_transport_refuses_taps(self):
        device = AdbEmulator("127.0.0.1:16384", "adb")
        device.native_size = (1920, 1080)
        with patch.object(device, "command") as command:
            with self.assertRaises(RuntimeError):
                device.tap(73, 573)
            command.assert_not_called()

    def test_foreground_change_prevents_input(self):
        device = AdbEmulator("127.0.0.1:16384", "adb", allow_input=True)
        device.native_size = (1920, 1080)
        with patch.object(device, "command", return_value=b"mResumedActivity: ActivityRecord{x com.android.launcher/.Main}") as command:
            with self.assertRaises(RuntimeError):
                device.tap(73, 573)
            self.assertEqual(command.call_count, 1)

    def test_normalized_coordinates_are_scaled(self):
        device = AdbEmulator("127.0.0.1:16384", "adb", allow_input=True)
        device.native_size = (1920, 1080)
        with patch.object(device, "check_foreground"), patch.object(device, "command") as command:
            device.tap(800, 570)
            command.assert_called_once_with("shell", "input", "tap", "1200", "855")

    def test_physical_phone_is_not_implicitly_selected(self):
        with self.assertRaises(ValueError):
            AdbEmulator("phone-serial", "adb")


if __name__ == "__main__":
    unittest.main()
