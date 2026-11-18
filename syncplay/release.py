"""Version information for the Night desktop distribution."""

from syncplay import version

NIGHT_VERSION = 1
DISPLAY_VERSION = "{} (Night {})".format(version, NIGHT_VERSION)
RELEASE_TAG = "v{}-night.{}".format(version, NIGHT_VERSION)
VERSION_INFO = {"upstream_version": version, "night_version": NIGHT_VERSION}
