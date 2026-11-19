import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from twisted.internet import task

from syncplay import constants, version
from syncplay.client import SyncplayClient
from syncplay.playback import SHARED_PLAYBACK_SPEED
from syncplay.players.mpv import MpvPlayer
from syncplay.server import Room, SyncFactory
from syncplay.ui.ConfigurationGetter import ConfigurationGetter
from syncplay.utils import RoomPasswordProvider
from test_desktop_recovery import ControlledEndpoint, TestTransport


class ServerSpeedTests(unittest.TestCase):
    def setUp(self):
        self.clock = task.Clock()
        self.now = 100.0
        self.enterContext(patch("syncplay.server.reactor", self.clock))
        self.enterContext(patch("syncplay.server.time", SimpleNamespace(
            monotonic=lambda: self.now, time=lambda: 1_000_000 + self.now)))
        with redirect_stdout(io.StringIO()):
            self.factory = SyncFactory(salt="synthetic")

    def viewer(self, name, speedSupport=None, room="test"):
        protocol = self.factory.buildProtocol(None)
        transport = TestTransport(protocol)
        protocol.makeConnection(transport)
        features = {} if speedSupport is None else {SHARED_PLAYBACK_SPEED: speedSupport}
        transport.receive({"Hello": {"username": name, "room": {"name": room},
                                     "realversion": version, "features": features}})
        self.clock.advance(.1)
        hello = next(m["Hello"] for m in transport.messages() if "Hello" in m)
        self.assertIs(hello["features"][SHARED_PLAYBACK_SPEED], True)
        self.assertEqual(hello["realversion"], "1.7.7")
        return transport

    def request(self, viewer, speed, revision=None):
        if revision is None:
            revision = viewer.protocol._watcher.getRoom().getSharedSpeed()[2]
        start = len(viewer.messages())
        viewer.receive({"Set": {"playbackSpeed": {"speed": speed, "speedRevision": revision}}})
        results = [m["Set"]["playbackSpeed"] for m in viewer.messages()[start:]
                   if "playbackSpeed" in m.get("Set", {})]
        return results[-1]["accepted"] if results else None

    def test_rates_reanchor_and_legacy_viewer_disables_until_departure(self):
        first = self.viewer("First", True)
        watcher = first.protocol._watcher
        room = watcher.getRoom()
        room.setPosition(20, watcher)
        room.setPaused(Room.STATE_PLAYING, watcher)
        self.now += 2
        self.assertTrue(self.request(first, 1.5))
        oldRevision = room.getSharedSpeed()[2]
        self.assertEqual(room.getPosition(), 22)
        self.now += 2
        modern = self.viewer("Second", True)
        self.assertEqual(room.getPosition(), 25)
        self.assertEqual(room.getSharedSpeed(), (1.5, True, oldRevision))
        legacy = self.viewer("Legacy")
        self.assertEqual(room.getPosition(), 25)
        self.assertEqual(room.getSharedSpeed()[:2], (1, False))
        for message in legacy.messages():
            self.assertNotIn("speed", message.get("State", {}).get("playstate", {}))
        self.now += 2
        legacy.lose()
        self.assertEqual(room.getPosition(), 27)
        self.assertEqual(room.getSharedSpeed()[:2], (1, True))
        self.assertFalse(self.request(first, .75, oldRevision))
        self.assertTrue(self.request(modern, .75))
        self.now += 2
        self.assertEqual(room.getPosition(), 28.5)

    def test_speed_validation_and_managed_room_authority(self):
        password = "AB-123-456"
        name = RoomPasswordProvider.getControlledRoomName("test", password, "synthetic")
        controller = self.viewer("Controller", True, name)
        viewer = self.viewer("Viewer", True, name)
        self.assertFalse(self.request(controller, 1.5))
        controller.receive({"Set": {"controllerAuth": {"password": password}}})
        self.assertFalse(self.request(viewer, 1.5))
        for invalid in (True, "1.5", None, float("nan"), float("inf"), 0, 4.01, .24, 10 ** 80):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.request(controller, invalid))
        self.assertTrue(self.request(controller, 1.5))
        room = controller.protocol._watcher.getRoom()
        room.setPermanent(True)
        controller.lose()
        viewer.lose()
        self.assertEqual(room.getSharedSpeed()[:2], (1, False))
        replacement = self.viewer("Replacement", True, name)
        self.assertIs(replacement.protocol._watcher.getRoom(), room)
        self.assertEqual(room.getSharedSpeed()[:2], (1, True))

    def test_watcher_uses_reported_local_rate_and_ignores_stale_samples(self):
        transport = self.viewer("First", True)
        watcher = transport.protocol._watcher
        room = watcher.getRoom()
        room.setPaused(Room.STATE_PLAYING, watcher)
        self.assertTrue(self.request(transport, 1.5))
        revision = room.getSharedSpeed()[2]
        watcher.updateState(10, False, False, .2, revision, 1.425)
        self.assertAlmostEqual(watcher.getPosition(), 10.285)
        self.now += 1
        self.assertAlmostEqual(watcher.getPosition(), 11.710)
        watcher.updateState(500, True, True, 0, revision - 1, 1)
        self.assertTrue(room.isPlaying())
        self.assertAlmostEqual(watcher.getPosition(), 11.710)

    def test_runtime_capability_change_anchors_and_disables_speed(self):
        first = self.viewer("First", True)
        second = self.viewer("Second", "true")
        room = first.protocol._watcher.getRoom()
        self.assertFalse(self.request(first, 1.5))
        second.receive({"Set": {"features": {SHARED_PLAYBACK_SPEED: True}}})
        self.assertTrue(self.request(first, 1.5))
        second.receive({"Set": {"features": {SHARED_PLAYBACK_SPEED: False}}})
        self.assertEqual(room.getSharedSpeed()[:2], (1, False))


class ClientSpeedTests(unittest.TestCase):
    def connect(self, serverSupportsSpeed=True, playerSupportsSpeed=True):
        config = ConfigurationGetter()._config
        config.update(host="127.0.0.1", port=12345, name="Viewer", room="test",
                      debug=False, showOSD=False, autosaveJoinsToList=False)
        endpoint = ControlledEndpoint()
        clock = task.Clock()
        self.client = SyncplayClient(None, Mock(uiMode=constants.CONSOLE_UI_MODE), config)
        self.client._running = True
        self.player = self.client._player = Mock(sharedSpeedReady=playerSupportsSpeed)
        self.client.userlist.currentUser.setFile("sample.mp4", 100, 1)
        self.client._prepareConnection(config["host"], config["port"], clock=clock,
                                       endpoint=endpoint, seconds=clock.seconds)
        self.client.connection.start()
        self.transport = endpoint.reach()
        self.transport.receive({"TLS": {"startTLS": "false"}})
        self.transport.receive({"Hello": {"username": "Viewer", "room": {"name": "test"},
                                          "realversion": version,
                                          "features": {SHARED_PLAYBACK_SPEED: serverSupportsSpeed}}})
        hello = next(m["Hello"] for m in self.transport.messages() if "Hello" in m)
        self.assertEqual(hello["features"][SHARED_PLAYBACK_SPEED], playerSupportsSpeed)

    def snapshot(self, speed, revision, enabled=True, paused=True):
        self.transport.receive({"State": {"playstate": {"position": 12, "paused": paused,
                                                        "speed": speed, "speedEnabled": enabled,
                                                        "speedRevision": revision}}})

    def requests(self):
        return [m["Set"]["playbackSpeed"] for m in self.transport.messages()
                if "playbackSpeed" in m.get("Set", {})]

    def test_baseline_correction_and_reset_are_never_intent(self):
        self.connect()
        self.snapshot(1.5, 7)
        self.player.setSpeed.assert_called_once_with(1.5)
        self.client.updatePlayerStatus(True, 12)
        self.assertFalse(self.client.canSendPlayback())
        self.client.playerSpeedObserved(self.player, 1.5, native=False)
        self.client.updatePlayerStatus(True, 12)
        self.assertTrue(self.client.canSendPlayback())
        self.client._slowDownToCoverTimeDifference(self.client._config["slowdownThreshold"] + 1, "Peer")
        slowed = 1.5 * constants.SLOWDOWN_RATE
        self.player.setSpeed.assert_called_with(slowed)
        self.client.playerSpeedObserved(self.player, slowed, native=False)
        self.client._slowDownToCoverTimeDifference(0, "Peer")
        self.player.setSpeed.assert_called_with(1.5)
        self.client.playerSpeedObserved(self.player, 1.5, native=False)
        self.assertEqual(self.requests(), [])

    def test_native_changes_coalesce_and_deferred_pause_uses_new_revision(self):
        self.connect()
        self.snapshot(1, 7, paused=False)
        self.client.playerSpeedObserved(self.player, 1, native=False)
        self.client.updatePlayerStatus(False, 12)
        self.client.playerSpeedObserved(self.player, 1.5, native=True)
        self.client.playerSpeedObserved(self.player, .75, native=True)
        self.snapshot(1.5, 8, paused=False)
        self.transport.receive({"Set": {"playbackSpeed": {"speedRevision": 7, "accepted": True}}})
        self.assertEqual(self.requests(), [{"speed": 1.5, "speedRevision": 7},
                                           {"speed": .75, "speedRevision": 8}])
        self.snapshot(.75, 9, paused=False)
        self.transport.receive({"Set": {"playbackSpeed": {"speedRevision": 8, "accepted": True}}})
        self.client._sendPlaybackChange(12, True, False)
        self.assertIsNotNone(self.client._deferredPlayback)
        self.client.playerSpeedObserved(self.player, .75, native=False)
        changes = [m["State"]["playstate"] for m in self.transport.messages()
                   if m.get("State", {}).get("playstate", {}).get("paused") is True]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["speedRevision"], 9)
        self.assertEqual(changes[0]["localSpeed"], .75)

    def test_stock_server_ignores_optional_rate_and_leaves_native_rate_local(self):
        self.connect(serverSupportsSpeed=False)
        self.snapshot(1.5, 7)
        self.client.updatePlayerStatus(True, 12)
        self.client.playerSpeedObserved(self.player, .75, native=True)
        self.assertEqual(self.client._roomSpeed(), 1)
        self.assertEqual(self.client._localSpeed, .75)
        self.player.setSpeed.assert_not_called()
        self.assertEqual(self.requests(), [])

    def test_client_latency_and_position_clocks_use_respective_rates(self):
        self.connect()
        with patch("syncplay.client.time", SimpleNamespace(monotonic=lambda: 101.5)):
            self.client.updateGlobalState(10, False, False, "Peer", .3, (1.5, True, 1))
            self.assertAlmostEqual(self.client.getGlobalPosition(), 10.45)
            self.client.playerSpeedObserved(self.player, .75, native=False)
            self.client._playerPaused = False
            self.client._playerPosition = 10
            self.client._lastPlayerUpdate = 101
            self.assertAlmostEqual(self.client.getPlayerPosition(), 10.375)

    def test_mpv_does_not_publish_a_native_event_from_before_its_latest_write(self):
        mpv = MpvPlayer.__new__(MpvPlayer)
        mpv._set_defaults()
        mpv._client = Mock()
        mpv._listener = Mock()
        mpv._reportSpeed("initial", 0, 1)
        mpv.setSpeed(1.5)
        mpv._reportSpeed("native", 0, .75)
        mpv._reportSpeed("applied", 1, 1.5)
        mpv._reportSpeed("native", 1, 1)
        self.assertEqual([call.args[1:] for call in mpv._client.playerSpeedObserved.call_args_list],
                         [(1, False), (1.5, False), (1, True)])
