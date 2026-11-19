"""Brand and version information for the desktop distribution."""

from syncplay import version

APP_NAME = "SyncPlayU"
APP_ICON = "syncplayu.png"
NIGHT_VERSION = 3
DISPLAY_VERSION = "{} (Night {})".format(version, NIGHT_VERSION)
DISPLAY_TITLE = "{} {}".format(APP_NAME, DISPLAY_VERSION)
RELEASE_TAG = "v{}-night.{}".format(version, NIGHT_VERSION)
VERSION_INFO = {"upstream_version": version, "night_version": NIGHT_VERSION}
