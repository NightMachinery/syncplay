import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from twisted.internet import defer, error, task
from twisted.internet.testing import StringTransport
from twisted.python.failure import Failure

from syncplay import constants
from syncplay.client import SyncplayClient
from syncplay.ui.ConfigurationGetter import ConfigurationGetter
from syncplay.ui.consoleUI import ConsoleUI


class TestTransport(StringTransport):
    def __init__(self, protocol):
        super().__init__()
        self.protocol = protocol
        self.aborts = 0

    def abortConnection(self):
        self.aborts += 1
        self.disconnecting = True

    def lose(self):
        self.protocol.connectionLost(Failure(error.ConnectionDone()))

    def receive(self, message):
        self.protocol.dataReceived(json.dumps(message).encode() + self.protocol.delimiter)

    def messages(self):
        return [json.loads(line) for line in self.value().splitlines()]


class ControlledEndpoint:
    def __init__(self):
        self.attempts = []
        self.pending = None
        self.factory = None

    def connect(self, factory):
        assert self.pending is None or self.pending.called
        self.factory = factory
        self.pending = defer.Deferred()
        self.attempts.append(self.pending)
        return self.pending

    def reach(self):
        proxy = self.factory.buildProtocol(None)
        transport = TestTransport(proxy)
        proxy.makeConnection(transport)
        self.pending.callback(proxy)
        return transport


class RecoveryTests(unittest.TestCase):
    def makeClient(self, **config):
        settings = ConfigurationGetter()._config
        settings.update(host="127.0.0.1", port=12345, name="Viewer", room="A",
                        debug=False, showOSD=False, autosaveJoinsToList=False)
        settings.update(config)
        self.clock = task.Clock()
        self.endpoint = ControlledEndpoint()
        self.ui = Mock(uiMode=constants.CONSOLE_UI_MODE)
        self.client = SyncplayClient(None, self.ui, settings)
        self.client._running = True
        self.client._prepareConnection(settings["host"], settings["port"], clock=self.clock,
                                       endpoint=self.endpoint, jitter=lambda low, high: high,
                                       seconds=self.clock.seconds)
        self.client.connection.start()
        self.connection = self.client.connection

    def hello(self, transport, user="Viewer", room="A", features=None):
        transport.receive({"TLS": {"startTLS": "false"}})
        transport.receive({"Hello": {"username": user, "room": {"name": room},
                                     "realversion": "1.7.6", "features": features or {}}})

    def state(self, transport, position=12, paused=True):
        transport.receive({"State": {"playstate": {"position": position, "paused": paused}}})

    def test_first_refusal_holds_and_logs_then_retry_succeeds(self):
        self.makeClient(room="Room {a}")
        self.endpoint.pending.errback(error.ConnectionRefusedError("private reason"))
        self.clock.advance(60)
        self.assertEqual(len(self.endpoint.attempts), 1)
        status, pending, log, attention = self.ui.connectionStatus.call_args.args
        for text in ("127.0.0.1:12345", "Room {a}", "TCP", "refused", "Reconnect",
                     "server address or password", "startup configuration"):
            self.assertIn(text, log)
        self.assertNotIn("private reason", log)
        self.assertTrue(attention)
        self.assertFalse(pending)
        self.ui.drop.assert_not_called()
        self.client.manualReconnect()
        self.client.manualReconnect()
        self.assertEqual(len(self.endpoint.attempts), 2)
        transport = self.endpoint.reach()
        self.hello(transport, room="Room {a}")
        self.state(transport)
        self.assertEqual(self.connection.phase, "room")

    def test_startup_playlist_is_sent_once_after_successful_admission(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "requested.txt"
            path.write_text("requested.mp4\n", encoding="utf-8")
            self.makeClient(loadPlaylistFromFile=str(path))
            self.endpoint.pending.errback(error.ConnectionRefusedError())
            self.clock.advance(0)
            self.client.manualReconnect()

            initial = self.endpoint.reach()
            self.hello(initial)
            empty = {"Set": {"playlistChange": {"files": [], "user": None}}}
            initial.receive(empty)
            self.state(initial)
            changes = [message["Set"]["playlistChange"] for message in initial.messages()
                       if "playlistChange" in message.get("Set", {})]
            self.assertEqual(changes, [{"files": ["requested.mp4"]}])

            initial.lose()
            self.clock.advance(1)
            recovered = self.endpoint.reach()
            self.hello(recovered)
            recovered.receive(empty)
            self.state(recovered)
            self.assertEqual(self.connection.phase, "room")
            self.assertFalse(any("playlistChange" in message.get("Set", {}) or
                                 "playlistIndex" in message.get("Set", {})
                                 for message in recovered.messages()))

    def test_dns_failure_and_combined_deadline(self):
        self.makeClient()
        self.endpoint.pending.errback(error.DNSLookupError())
        self.clock.advance(0)
        self.assertIn("host lookup failed", self.ui.connectionStatus.call_args.args[2])
        self.client.manualReconnect()
        self.clock.advance(15)
        self.assertTrue(self.endpoint.pending.called)
        self.assertEqual(self.connection.phase, "attention")
        self.assertEqual(len(self.endpoint.attempts), 2)

    def test_tls_and_hello_have_one_deadline(self):
        for plaintext in (False, True):
            with self.subTest(plaintext=plaintext):
                self.makeClient()
                transport = self.endpoint.reach()
                self.clock.advance(10)
                if plaintext:
                    transport.receive({"TLS": {"startTLS": "false"}})
                self.clock.advance(10)
                transport.lose()
                self.clock.advance(60)
                self.assertEqual(len(self.endpoint.attempts), 1)
                self.assertEqual(transport.aborts, 1)
                log = self.ui.connectionStatus.call_args.args[2]
                self.assertIn("Hello" if plaintext else "TLS", log)
                self.assertEqual(self.connection.phase, "attention")

    def test_ping_only_has_first_state_deadline_and_gets_ack(self):
        self.makeClient()
        transport = self.endpoint.reach()
        self.hello(transport)
        self.clock.advance(10)
        transport.receive({"State": {"ping": {"latencyCalculation": 1}}})
        self.assertNotIn("playstate", transport.messages()[-1]["State"])
        self.clock.advance(10)
        self.assertEqual(transport.aborts, 1)
        transport.lose()
        self.clock.advance(1)
        self.assertEqual(len(self.endpoint.attempts), 2)

    def test_retry_ceiling_and_reset_after_healthy_traffic(self):
        self.makeClient()
        transport = self.endpoint.reach()
        self.hello(transport)
        self.state(transport)
        transport.lose()
        for index in range(1002):
            delay = constants.RECONNECT_DELAYS[min(index, 5)]
            self.clock.advance(delay - 0.125)
            self.assertEqual(self.connection.phase, "waiting")
            self.clock.advance(0.125)
            self.assertEqual(len(self.endpoint.attempts), index + 2)
            if index % 2:
                self.endpoint.pending.errback(error.DNSLookupError())
            else:
                self.endpoint.reach().lose()
        self.clock.advance(30)
        transport = self.endpoint.reach()
        self.hello(transport)
        self.state(transport)
        for interval in (10, 10, 10):
            self.clock.advance(interval)
            self.state(transport)
        transport.lose()
        self.clock.advance(1)
        self.assertEqual(self.connection.phase, "dial")

    def test_timeout_retry_cancel_and_stale_callback(self):
        self.makeClient()
        first = self.endpoint.reach()
        original = self.client._protocol
        self.hello(first)
        self.state(first)
        self.clock.advance(12.5)
        self.client.manualReconnect()
        self.client.manualReconnect()
        self.assertEqual(len(self.endpoint.attempts), 1)
        self.client.cancelReconnect()
        first.lose()
        self.clock.advance(100)
        self.assertEqual(len(self.endpoint.attempts), 1)
        self.client.setRoom("B")
        self.client.sendRoom()
        self.client.manualReconnect()
        second = self.endpoint.reach()
        self.hello(second, user="Viewer_", room="B")
        self.state(second)
        original.connectionLost(Failure(error.ConnectionDone()))
        self.assertEqual(self.connection.phase, "room")
        self.assertEqual(self.client.getUsername(), "Viewer_")
        self.client.manualReconnect()
        self.client.manualReconnect()
        second.lose()
        third = self.endpoint.reach()
        third.receive({"TLS": {"startTLS": "false"}})
        request = next(message["Hello"] for message in third.messages() if "Hello" in message)
        self.assertEqual(request["room"], {"name": "B"})
        self.assertEqual(request["username"], "Viewer")

    def test_cancel_dns_and_room_change_during_hello(self):
        self.makeClient()
        self.client.cancelReconnect()
        self.clock.advance(100)
        self.assertEqual(self.connection.phase, "idle")
        self.assertEqual(len(self.endpoint.attempts), 1)
        self.client.manualReconnect()
        first = self.endpoint.reach()
        first.receive({"TLS": {"startTLS": "false"}})
        self.client.setRoom("B")
        self.client.sendRoom()
        first.receive({"Hello": {"username": "Old", "room": {"name": "A"}, "version": "1.7.6"}})
        self.assertEqual(self.client.getRoom(), "B")
        first.lose()
        second = self.endpoint.reach()
        self.hello(second, room="B")
        self.state(second)
        self.assertEqual(self.connection.phase, "room")
        self.connection.cancel(exiting=True)
        second.lose()
        self.clock.advance(100)
        self.assertEqual(self.connection.phase, "stopped")
        self.assertEqual(len(self.endpoint.attempts), 3)

    def test_bad_hello_and_password_are_held_even_after_admission(self):
        for message, reason in (({"Hello": {"room": "invalid"}}, "invalid or incompatible Hello"),
                                ({"Error": {"message": "Wrong password supplied"}}, "refused the password")):
            with self.subTest(reason=reason):
                self.makeClient()
                first = self.endpoint.reach()
                self.hello(first)
                self.state(first)
                first.lose()
                self.clock.advance(1)
                second = self.endpoint.reach()
                second.receive({"TLS": {"startTLS": "false"}})
                second.receive(message)
                second.lose()
                self.clock.advance(100)
                self.assertEqual(len(self.endpoint.attempts), 2)
                self.assertIn(reason, self.ui.connectionStatus.call_args.args[2])
                self.client.manualReconnect()
                recovered = self.endpoint.reach()
                self.hello(recovered)
                self.state(recovered)
                self.assertEqual(self.connection.phase, "room")

    def test_certificate_rejection_never_sends_hello(self):
        self.makeClient(password="synthetic")
        transport = self.endpoint.reach()
        transport.startTLS = Mock()
        transport.receive({"TLS": {"startTLS": "true"}})
        transport.protocol.connectionLost(Failure(Exception("mismatched_id=DNS_ID")))
        self.clock.advance(120)
        self.assertEqual(len(self.endpoint.attempts), 1)
        self.assertIn("certificate", self.ui.connectionStatus.call_args.args[2])
        self.assertEqual(transport.messages(), [{"TLS": {"startTLS": "send"}}])

    def test_pause_preference_and_recovery_suppression(self):
        for config in ({}, {"pauseOnLeave": False}, {"pauseOnLeave": True}):
            with self.subTest(config=config):
                self.makeClient(**config)
                self.client.userlist.currentUser.setFile("sample.mp4", 100, 1)
                player = self.client._player = Mock()
                self.client._userOffset = 2
                first = self.endpoint.reach()
                self.hello(first)
                self.state(first, 12, False)
                self.client.updatePlayerStatus(False, 14)
                self.assertTrue(self.client.canSendPlayback())
                player.reset_mock()
                first.lose()
                self.clock.advance(1)
                self.endpoint.pending.errback(error.ConnectionRefusedError())
                self.clock.advance(2)
                self.assertEqual(player.setPaused.call_count, int(config.get("pauseOnLeave", False)))
                second = self.endpoint.reach()
                second.receive({"TLS": {"startTLS": "false"}})
                second.receive({"Set": {"playlistChange": {"files": [], "user": None}}})
                second.receive({"Hello": {"username": "Viewer", "room": {"name": "A"},
                                          "version": "1.7.6"}})
                self.state(second, 42, True)
                player.setPosition.assert_called_with(44)
                player.setPaused.assert_called_with(True)
                self.client.updatePlayerStatus(False, 18)
                self.assertFalse(self.client.canSendPlayback())
                self.assertNotIn("playstate", second.messages()[-1]["State"])
                self.client.updatePlayerStatus(True, 44)
                self.assertTrue(self.client.canSendPlayback())
                rejoined = [args[0][2] for args in self.ui.connectionStatus.call_args_list
                            if args[0][2] and "Rejoined" in args[0][2]]
                self.assertEqual(rejoined, ["Rejoined room A."])

    def test_empty_snapshot_clears_playlist_without_writes(self):
        self.makeClient()
        first = self.endpoint.reach()
        self.hello(first)
        self.state(first)
        self.client.playlist._playlist = ["cached.mp4"]
        self.client.playlist._playlistIndex = 0
        first.lose()
        self.clock.advance(1)
        second = self.endpoint.reach()
        second.receive({"TLS": {"startTLS": "false"}})
        second.receive({"Set": {"playlistChange": {"files": [], "user": None}}})
        second.receive({"Set": {"playlistIndex": {"index": None, "user": None}}})
        second.receive({"Hello": {"username": "Viewer", "room": {"name": "A"},
                                  "version": "1.7.6"}})
        self.state(second)
        self.assertEqual(self.client.playlist._playlist, [])
        self.assertIsNone(self.client.playlist._playlistIndex)
        self.ui.setPlaylistIndexFilename.assert_called_with(None)
        for message in second.messages():
            self.assertNotIn("playlistChange", message.get("Set", {}))
            self.assertNotIn("playlistIndex", message.get("Set", {}))
            self.assertNotIn("playstate", message.get("State", {}))

    def test_old_player_status_cannot_change_rejoined_room(self):
        self.makeClient()
        self.client.userlist.currentUser.setFile("sample.mp4", 100, 1)
        player = self.client._player = Mock()
        first = self.endpoint.reach()
        self.hello(first)
        self.state(first, 15, False)
        self.client.askPlayer()
        stale = player.askForStatus.call_args.args[0]
        first.lose()
        self.clock.advance(1)
        second = self.endpoint.reach()
        self.hello(second)
        self.state(second, 42, True)
        self.client.askPlayer()
        confirm = player.askForStatus.call_args.args[0]
        confirm(True, 42)
        self.assertTrue(self.client.canSendPlayback())
        before = second.messages()
        stale(False, 15)
        self.assertEqual(second.messages(), before)
        self.assertTrue(self.client.getPlayerPaused())

    def test_keeps_ready_and_only_reauthenticates_matching_managed_room(self):
        for saved in (False, True):
            with self.subTest(saved=saved):
                room = "+B:ABCDEFABCDEF"
                self.makeClient(room=room, readyAtStart=True)
                self.client.userlist.currentUser.setReady(False)
                self.client.storeControlPassword(room if saved else "+A:ABCDEFABCDEF", "TESTPASS")
                transport = self.endpoint.reach()
                transport.receive({"TLS": {"startTLS": "false"}})
                transport.receive({"Set": {"ready": {"username": "Viewer", "isReady": None}}})
                transport.receive({"Hello": {"username": "Viewer", "room": {"name": room},
                                              "version": "1.7.6", "features": {
                                                  "readiness": True, "managedRooms": True}}})
                commands = [message["Set"] for message in transport.messages() if "Set" in message]
                self.assertEqual([x["ready"]["isReady"] for x in commands if "ready" in x], [False])
                self.assertEqual(any("controllerAuth" in x for x in commands), saved)
                self.assertFalse(self.client.userlist.currentUser.isController())

    def test_absent_playlist_message_does_not_clear_cache_or_publish_it(self):
        self.makeClient()
        first = self.endpoint.reach()
        self.hello(first)
        self.state(first)
        self.client.playlist._playlist = ["cached.mp4"]
        self.client.playlist._playlistIndex = 0
        first.lose()
        self.clock.advance(1)
        second = self.endpoint.reach()
        self.hello(second)
        self.state(second)
        self.assertEqual(self.client.playlist._playlist, ["cached.mp4"])
        self.assertEqual(self.client.playlist._playlistIndex, 0)
        self.assertFalse(any("playlistChange" in message.get("Set", {}) or
                             "playlistIndex" in message.get("Set", {})
                             for message in second.messages()))

    def test_acknowledges_remote_resume_without_echoing_old_pause(self):
        self.makeClient()
        self.client.userlist.currentUser.setFile("sample.mp4", 100, 1)
        self.client._player = Mock()
        transport = self.endpoint.reach()
        self.hello(transport)
        self.state(transport, 12, True)
        self.client.updatePlayerStatus(True, 12)
        transport.receive({"State": {"ignoringOnTheFly": {"server": 1},
                                     "playstate": {"position": 12, "paused": False, "setBy": "Peer"}}})
        reply = transport.messages()[-1]["State"]
        self.assertEqual(reply["ignoringOnTheFly"], {"server": 1})
        self.assertNotIn("playstate", reply)
        self.state(transport, 12, False)
        self.assertNotIn("playstate", transport.messages()[-1]["State"])


    def test_translated_status_can_be_logged_on_a_non_utf8_console(self):
        buffer = io.BytesIO()
        with io.TextIOWrapper(buffer, encoding="cp1252") as stdout:
            with redirect_stdout(stdout):
                ConsoleUI().connectionStatus("", True, "Connecting to room…", False)
            stdout.flush()
            self.assertIn("Connecting to room…".encode("cp1252"), buffer.getvalue())

    def test_unavailable_media_can_chat_and_does_not_retry(self):
        self.makeClient()
        self.client._player = Mock()
        self.client.fileSwitch.findFilepath = Mock(return_value=None)
        self.client.userlist.currentUser.setFile("local.mp4", 100, 1, "/media/local.mp4")
        transport = self.endpoint.reach()
        transport.receive({"TLS": {"startTLS": "false"}})
        transport.receive({"Set": {"playlistChange": {"files": ["missing.mp4"], "user": None}}})
        transport.receive({"Set": {"playlistIndex": {"index": 0, "user": None}}})
        transport.receive({"Hello": {"username": "Viewer", "room": {"name": "A"}, "version": "1.7.6"}})
        self.state(transport)
        self.assertEqual(self.connection.phase, "room")
        self.assertFalse(self.client.canSendPlayback())
        self.client.sendChat("hello")
        self.assertEqual(transport.messages()[-1], {"Chat": "hello"})
        for interval in (10, 10, 10):
            self.clock.advance(interval)
            self.state(transport)
        self.assertEqual(len(self.endpoint.attempts), 1)


    def test_room_change_does_not_carry_playlist_undo_across_rooms(self):
        self.makeClient()
        first = self.endpoint.reach()
        self.hello(first)
        self.state(first)
        self.client.playlist.changePlaylist(["a.mp4"], remote=True)
        self.client.playlist.changeToPlaylistIndex(0, remote=True)
        self.client.setRoom("B")
        self.client.sendRoom()
        self.assertEqual(self.client.playlist._playlist, [])
        self.assertIsNone(self.client.playlist._playlistIndex)
        first.receive({"Set": {"user": {"Viewer": {"room": {"name": "B"}}}}})
        first.receive({"Set": {"playlistChange": {"files": ["b.mp4"], "user": None}}})
        self.state(first)
        self.assertEqual(self.client.getRoom(), "B")
        self.assertEqual(self.client.playlist._playlist, ["b.mp4"])
        self.assertFalse(self.client.playlist._getPreviousPlaylist())


    def test_reapplies_room_state_if_player_discards_the_first_command(self):
        self.makeClient()
        self.client.userlist.currentUser.setFile("sample.mp4", 100, 1)
        player = self.client._player = Mock()
        transport = self.endpoint.reach()
        self.hello(transport)
        self.state(transport, 42, True)
        self.assertEqual(player.setPaused.call_count, 1)
        self.clock.advance(1.1)
        self.client.updatePlayerStatus(False, 12)
        self.assertEqual(player.setPaused.call_count, 2)
        self.assertFalse(self.client.canSendPlayback())
        self.assertNotIn("playstate", transport.messages()[-1]["State"])
        self.client.updatePlayerStatus(True, 42)
        self.assertTrue(self.client.canSendPlayback())



if __name__ == "__main__":
    unittest.main()
