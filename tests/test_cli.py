import sys
import unittest
from unittest.mock import patch

from usage_limits_bar import cli


class TestLaunchMenubar(unittest.TestCase):
    @patch("usage_limits_bar.cli.subprocess.Popen")
    def test_detaches_the_menu_bar_process(self, popen):
        self.assertEqual(cli.launch_menubar(), 0)
        popen.assert_called_once_with(
            [sys.executable, "-m", "usage_limits_bar.cli", "--foreground"],
            stdin=cli.subprocess.DEVNULL,
            stdout=cli.subprocess.DEVNULL,
            stderr=cli.subprocess.DEVNULL,
            start_new_session=True,
        )


if __name__ == "__main__":
    unittest.main()
