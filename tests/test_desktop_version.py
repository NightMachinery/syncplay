import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from syncplay import version
from syncplay.release import NIGHT_VERSION, RELEASE_TAG
from syncplay.ui.ConfigurationGetter import ConfigurationGetter


class DesktopVersionTests(unittest.TestCase):
    def versionOutput(self, option):
        with redirect_stdout(io.StringIO()) as output:
            with patch.object(ConfigurationGetter, "_getConfigurationFilePath", return_value="syncplay.ini"):
                with patch.object(ConfigurationGetter, "_parseConfigFile"):
                    with patch("sys.argv", ["syncplayClient.py", option]):
                        with self.assertRaises(SystemExit) as exited:
                            ConfigurationGetter().getConfiguration()
        self.assertIsNone(exited.exception.code)
        return output.getvalue()

    def test_cli_shows_both_versions(self):
        self.assertEqual(self.versionOutput("--version").strip(),
                         "SyncPlayU {} (Night {})".format(version, NIGHT_VERSION))

    def test_json_keeps_versions_separate_from_the_release_tag(self):
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(json.loads(self.versionOutput("--version-json")),
                         {"upstream_version": version, "night_version": NIGHT_VERSION})
        self.assertEqual(RELEASE_TAG, "v{}-night.{}".format(version, NIGHT_VERSION))
