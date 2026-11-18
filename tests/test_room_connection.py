import unittest
from unittest.mock import Mock

from twisted.internet import defer, error, task
from twisted.internet.protocol import ClientFactory

from syncplay.connection import RoomConnection


class DialEndpoint:
    def __init__(self):
        self.dials = []

    def connect(self, factory):
        attempt = defer.Deferred()
        self.dials.append((attempt, factory))
        return attempt


class RoomConnectionTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = DialEndpoint()
        self.clock = task.Clock()
        self.client = Mock()
        self.jitter = Mock(side_effect=lambda low, high: low)
        self.connection = RoomConnection(self.client, self.endpoint, ClientFactory(),
                                         self.clock, jitter=self.jitter, seconds=self.clock.seconds)
        self.connection.start()

    def test_cancelled_dial_factory_cannot_build_a_protocol(self):
        first, factory = self.endpoint.dials[-1]
        self.connection.cancel()
        self.connection.retryNow()
        self.assertTrue(first.called)
        self.assertIsNone(factory.buildProtocol(None))
        self.assertEqual(len(self.endpoint.dials), 2)

    def test_wake_with_overdue_retry_dials_once(self):
        self.connection.everAdmitted = True
        self.endpoint.dials[-1][0].errback(error.ConnectionRefusedError())
        self.clock.advance(120)
        self.assertEqual(len(self.endpoint.dials), 2)
        self.jitter.assert_called_once_with(0.5, 1)
        self.connection.cancel()
        self.clock.advance(300)
        self.assertEqual(len(self.endpoint.dials), 2)
        self.assertEqual(self.clock.getDelayedCalls(), [])

    def test_startup_deadline_cancels_the_endpoint(self):
        attempt, _ = self.endpoint.dials[-1]
        self.clock.advance(14.999)
        self.assertFalse(attempt.called)
        self.clock.advance(.001)
        self.assertTrue(attempt.called)
        self.assertFalse(self.connection.service.running)
        self.client.connectionStatus.assert_called_with("attention", failedPhase="dial",
                                                         reason="timeout")
        self.clock.advance(1000)
        self.assertEqual(len(self.endpoint.dials), 1)
