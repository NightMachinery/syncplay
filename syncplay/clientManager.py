import time

from syncplay import ui
from syncplay.messages import getMessage
from syncplay.release import DISPLAY_VERSION
from syncplay.ui.ConfigurationGetter import ConfigurationGetter


class SyncplayClientManager(object):
    def run(self):
        config = ConfigurationGetter().getConfiguration()
        from twisted.internet import reactor
        # Keep the existing epoch for calls scheduled during GUI setup.
        clockOffset = reactor.seconds() - time.monotonic()
        reactor.seconds = lambda: clockOffset + time.monotonic()
        from syncplay.client import SyncplayClient  # Imported later, so the proper reactor is installed
        menuBar = config['menuBar'] if 'menuBar' in config else None
        interface = ui.getUi(graphical=not config["noGui"], passedBar=menuBar)
        syncplayClient = SyncplayClient(config["playerClass"], interface, config)
        if syncplayClient:
            interface.addClient(syncplayClient)
            syncplayClient.ui.showMessage(
                getMessage("syncplay-version-notification").format(DISPLAY_VERSION), noPlayer=True)
            syncplayClient.start(config['host'], config['port'])
        else:
            interface.showErrorMessage(getMessage("unable-to-start-client-error"), True)
