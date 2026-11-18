"""The desktop client's connection lifecycle, driven by one Twisted service."""

import random
import time

from twisted.application.internet import ClientService
from twisted.internet import defer, error
from twisted.internet.protocol import ClientFactory

from syncplay import constants


class _DialFactory(ClientFactory):
    """Keep a cancelled endpoint from producing a protocol for a newer dial."""

    def __init__(self, factory, connection, generation):
        self.factory = factory
        self.connection = connection
        self.generation = generation

    def buildProtocol(self, address):
        if self.connection.generation == self.generation and self.connection.phase == "dial":
            return self.factory.buildProtocol(address)


class RoomConnection:
    """Own the dial, transport deadlines and retries for one endpoint and login."""

    def __init__(self, client, endpoint, factory, clock, jitter=random.uniform,
                 seconds=time.monotonic):
        self.client = client
        self.endpoint = endpoint
        self.clock = clock
        self.seconds = seconds
        self.jitter = jitter
        self.service = ClientService(self, factory, clock=clock, retryPolicy=self.retryDelay)
        self.phase = "idle"
        self.generation = 0
        self.protocol = None
        self.everAdmitted = False
        self._outage = False
        self._failureCount = 0
        self._healthySince = None
        self._lastSummary = None
        self._deadline = None
        self._statusDetails = {}
        self._holdCall = None
        self._stopping = False
        self._startAfterStop = False

    def _status(self, phase, **details):
        self.phase = phase
        self._statusDetails = details
        self.client.connectionStatus(phase, **details)

    def refreshStatus(self):
        if self.phase != "failed":
            details = dict(self._statusDetails, announce=False)
            self.client.connectionStatus(self.phase, **details)

    def roomChanged(self):
        if self.phase in ("dial", "tls", "hello") and not self._stopping:
            self._startAfterStop = True
            self._stopService()

    def start(self):
        self.service.startService()

    def connect(self, factory):
        """Endpoint interface consumed by ClientService for every dial."""
        self.generation += 1
        generation = self.generation
        self._status("dial", announce=not self._outage)
        attempt = defer.maybeDeferred(self.endpoint.connect, _DialFactory(factory, self, generation))
        deadline = self.clock.callLater(constants.CONNECT_TIMEOUT, self._dialTimedOut,
                                        attempt, generation)
        attempt.addBoth(self._dialFinished, deadline)
        attempt.addErrback(self._dialFailed, generation)
        return attempt

    def _dialTimedOut(self, attempt, generation):
        if self.generation == generation and self.phase == "dial":
            self._failed("dial", "timeout")
        attempt.cancel()

    @staticmethod
    def _dialFinished(result, deadline):
        if deadline.active():
            deadline.cancel()
        return result

    def _dialFailed(self, failure, generation):
        if self.generation == generation and self.phase == "dial":
            if failure.check(error.DNSLookupError):
                phase, reason = "dns", "dns"
            elif failure.check(error.ConnectionRefusedError):
                phase, reason = "tcp", "refused"
            elif failure.check(error.TimeoutError, error.TCPTimedOutError):
                phase, reason = "dial", "timeout"
            else:
                phase, reason = "tcp", "unreachable"
            self._failed(phase, reason)
        return failure

    def transportConnected(self, protocol):
        if protocol.generation != self.generation or self.phase != "dial":
            return False
        self.protocol = protocol
        self._status("tls", announce=not self._outage)
        self._arm(constants.ADMISSION_TIMEOUT, self._admissionTimedOut, protocol)
        return True

    def isCurrent(self, protocol):
        return self.protocol is protocol and protocol.generation == self.generation

    def awaitingHello(self, protocol):
        if self.isCurrent(protocol):
            self._status("hello", announce=False)

    def admitted(self, protocol):
        if not self.isCurrent(protocol) or self.phase != "hello":
            return False
        self.everAdmitted = True
        self.awaitingRoomState(protocol)
        return True

    def awaitingRoomState(self, protocol):
        self._healthySince = None
        self._status("sync", announce=not self._outage)
        self._arm(constants.ADMISSION_TIMEOUT, self._stateTimedOut, protocol)

    def stateReceived(self, protocol, usable):
        if not self.isCurrent(protocol):
            return
        now = self.seconds()
        if self._healthySince is None:
            if not usable:
                return
            self._healthySince = now
        if now - self._healthySince >= constants.RECONNECT_HEALTHY_TIME:
            self._failureCount = 0
        self._arm(constants.PROTOCOL_TIMEOUT, self._stateTimedOut, protocol)

    def synchronized(self):
        rejoined = self._outage
        self._outage = False
        self._lastSummary = None
        self._status("room", rejoined=rejoined)

    def _arm(self, delay, callback, protocol):
        self._cancelDeadline()
        self._deadline = self.clock.callLater(delay, callback, protocol)

    def _cancelDeadline(self):
        if self._deadline is not None and self._deadline.active():
            self._deadline.cancel()
        self._deadline = None

    def _admissionTimedOut(self, protocol):
        if self.isCurrent(protocol):
            self._failed(self.phase, "timeout", abort=True)

    def _stateTimedOut(self, protocol):
        if self.isCurrent(protocol):
            self._failed("state", "timeout", abort=True)

    def lost(self, protocol, reason, fatal=False):
        if self.isCurrent(protocol):
            phase = "state" if protocol.logged else self.phase
            self._failed(phase, reason, fatal=fatal)

    def reject(self, protocol, reason):
        if self.isCurrent(protocol):
            self._failed(self.phase, reason, fatal=True, abort=True)

    def _detach(self):
        self._cancelDeadline()
        self._healthySince = None
        self.generation += 1
        protocol, self.protocol = self.protocol, None
        if protocol is not None:
            self.client.protocolDisconnected(protocol)
        return protocol

    def _beginOutage(self):
        if self.everAdmitted and not self._outage:
            self._outage = True
            self.client.onDisconnect()

    def _failed(self, phase, reason, fatal=False, abort=False):
        protocol = self._detach()
        self._beginOutage()
        if fatal or not self.everAdmitted:
            self._status("attention", failedPhase=phase, reason=reason)
            # Let ClientService finish its connection-lost transition before stopping it.
            self._holdCall = self.clock.callLater(0, self._stopService)
        else:
            self.phase = "failed"
            self._failure = phase, reason
        if abort and protocol is not None:
            protocol.abort()

    def retryDelay(self, _serviceFailures):
        # ClientService schedules a retry even when a connection fails permanently.
        # The deferred stop above cancels that call before it can dial.
        if self.phase != "failed":
            return constants.RECONNECT_DELAYS[0]
        ceilings = constants.RECONNECT_DELAYS
        ceiling = ceilings[min(self._failureCount, len(ceilings) - 1)]
        self._failureCount += 1
        delay = self.jitter(ceiling / 2, ceiling)
        now = self.seconds()
        announce = self._lastSummary is None or now - self._lastSummary >= ceilings[-1]
        if announce:
            self._lastSummary = now
        phase, reason = self._failure
        self._status("waiting", failedPhase=phase, reason=reason, delay=delay, announce=announce)
        return delay

    def retryNow(self):
        if self.phase in ("stopped", "dial", "tls", "hello", "sync") or self._stopping:
            return
        if self._holdCall is not None and self._holdCall.active():
            self._holdCall.cancel()
        self._startAfterStop = True
        self._beginOutage()
        self._status("dial", announce=False)
        self._stopService()

    def cancel(self, exiting=False):
        self._startAfterStop = False
        if self._holdCall is not None and self._holdCall.active():
            self._holdCall.cancel()
        self._status("stopped" if exiting else "idle")
        self._stopService()

    def _stopService(self):
        if self._stopping:
            return
        self._stopping = True
        protocol = self._detach()
        stopped = self.service.stopService()
        if protocol is not None:
            protocol.abort()
        # Restart after both endpoint cancellation and transport shutdown settle.
        stopped.addCallback(self._serviceStopped)

    def _serviceStopped(self, ignored):
        self._stopping = False
        if self._startAfterStop:
            self._startAfterStop = False
            self.start()
