# coding:utf8
import json
import math
import re
import time
from datetime import datetime
from functools import wraps

from twisted import version as twistedVersion
from twisted.internet.interfaces import IHandshakeListener
from twisted.protocols.basic import LineReceiver
from twisted.python.versions import Version
from zope.interface.declarations import implementer

import syncplay
from syncplay import utils
from syncplay.constants import PING_MOVING_AVERAGE_WEIGHT, CONTROLLED_ROOMS_MIN_VERSION, USER_READY_MIN_VERSION, SHARED_PLAYLIST_MIN_VERSION, CHAT_MIN_VERSION, UNKNOWN_UI_MODE
from syncplay.messages import getMessage
from syncplay.playback import SHARED_PLAYBACK_SPEED, validRoomSpeed, validSpeedRevision
from syncplay.utils import meetsMinVersion


class JSONCommandProtocol(LineReceiver):
    def handleMessages(self, messages):
        for message in messages.items():
            command = message[0]
            if command == "Hello":
                self.handleHello(message[1])
            elif command == "Set":
                self.handleSet(message[1])
            elif command == "List":
                self.handleList(message[1])
            elif command == "State":
                self.handleState(message[1])
            elif command == "Error":
                self.handleError(message[1])
            elif command == "Chat":
                self.handleChat(message[1])
            elif command == "TLS":
                self.handleTLS(message[1])
            else:
                self.dropWithError(getMessage("unknown-command-server-error").format(message[1]))  # TODO: log, not drop

    def lineReceived(self, line):
        try:
            line = line.decode('utf-8').strip()
        except UnicodeDecodeError:
            self.dropWithError(getMessage("line-decode-server-error"))
            return
        if not line:
            return
        self.showDebugMessage("client/server << {}".format(line))
        try:
            messages = json.loads(line)
        except json.decoder.JSONDecodeError:
            self.dropWithError(getMessage("not-json-server-error").format(line))
            return
        else:
            self.handleMessages(messages)

    def sendMessage(self, dict_):
        line = json.dumps(dict_)
        self.sendLine(line.encode('utf-8'))
        self.showDebugMessage("client/server >> {}".format(line))

    def drop(self):
        self.transport.loseConnection()

    def abort(self):
        if hasattr(self.transport, "abortConnection"):
            self.transport.abortConnection()
        else:
            self.drop()
    
    def dropWithError(self, error):
        raise NotImplementedError()


@implementer(IHandshakeListener)
class SyncClientProtocol(JSONCommandProtocol):
    def __init__(self, client, generation):
        self._client = client
        self.generation = generation
        self.clientIgnoringOnTheFly = 0
        self.serverIgnoringOnTheFly = 0
        self.logged = False
        self.hadFirstPlaylistIndex = False
        self._pingService = PingService()
        self._earlyMessages = []
        self._sentHello = False
        self._waitingForSpeedProbe = False
        self._advertisedSharedSpeed = False
        self._startedTLS = False
        self._tlsVerified = False
        self._awaitingRoom = False

    def showDebugMessage(self, line):
        pass  # Client wire messages may contain passwords and private media details.

    def _isCurrent(self):
        return self._client.connection.isCurrent(self)

    def lineReceived(self, line):
        if not self._isCurrent():
            return
        try:
            line = line.decode("utf-8").strip()
            if not line:
                return
            messages = json.loads(line)
        except (UnicodeError, ValueError):
            self.dropWithError("protocol")
            return
        if not isinstance(messages, dict):
            self.dropWithError("protocol")
            return
        self.handleMessages(messages)

    def lineLengthExceeded(self, line):
        self.dropWithError("protocol")

    def handleMessages(self, messages):
        for command, value in messages.items():
            if not self._isCurrent():
                break
            if command in ("Set", "State", "List", "Chat") and not self.logged:
                # Stock servers send room data while handling Hello, before their reply.
                if not self._sentHello or len(self._earlyMessages) >= 128:
                    self.dropWithError("protocol")
                    break
                self._earlyMessages.append((command, value))
            else:
                try:
                    super().handleMessages({command: value})
                except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                    self.dropWithError("protocol")

    def sendMessage(self, message):
        if self._isCurrent():
            super().sendMessage(message)

    def connectionMade(self):
        if not self._client.initProtocol(self):
            self.abort()
            return
        self.sendTLS({"startTLS": "send"})

    def connectionLost(self, reason):
        if not self._isCurrent():
            return
        detail = str(reason.value).lower()
        if self._startedTLS and not self._tlsVerified:
            if any(text in detail for text in ("certificate", "dns-id", "ip-address",
                                                "mismatched_id", "hostname")):
                self._client.connection.lost(self, "certificate", fatal=True)
                return
            if "protocol version" in detail or "unsupported protocol" in detail:
                self._client.connection.lost(self, "tls-version", fatal=True)
                return
        self._client.connection.lost(self, "closed")

    def dropWithError(self, error):
        reason = error if error in ("hello", "certificate", "password", "protocol") else "protocol"
        self._client.connection.reject(self, reason)

    def handleHello(self, hello):
        try:
            username = hello["username"]
            roomName = hello["room"]["name"]
            version = hello.get("realversion", hello.get("version"))
            motd = hello.get("motd") or ""
            featureList = hello.get("features")
            valid = (self._sentHello and not self.logged
                     and isinstance(username, str) and bool(username.strip())
                     and isinstance(roomName, str) and bool(roomName.strip())
                     and isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
                     and isinstance(motd, str)
                     and (featureList is None or isinstance(featureList, dict)))
        except (AttributeError, KeyError, TypeError):
            valid = False
        if not valid or not self._client.connection.admitted(self):
            self.dropWithError("hello")
            return

        self._client.setUsername(username)
        self._client.setRoom(roomName)
        self._client.ui.updateRoomName(roomName)
        self._client.connection.refreshStatus()
        self.logged = True
        self._client.setServerVersion(version, featureList)
        if featureList and featureList.get("persistentRooms"):
            span = "\n\n" if motd else ""
            motd = getMessage("persistent-rooms-notice") + span + motd
        if motd:
            self._client.ui.showMessage(motd, noPlayer=True, noTimestamp=True, isMotd=True)
        self._client.connected()
        early, self._earlyMessages = self._earlyMessages, []
        for command, value in early:
            if not self._isCurrent():
                break
            if command == "Set" and isinstance(value, dict) and "ready" in value:
                ready = value["ready"]
                if isinstance(ready, dict) and ready.get("username") == username:
                    continue
            self.handleMessages({command: value})

    def sendHello(self):
        if not self._isCurrent() or self._sentHello:
            return
        self._client.connection.awaitingHello(self)
        self._waitingForSpeedProbe = self._client._waitingForSpeedProbe
        if self._waitingForSpeedProbe:
            return
        self._sentHello = True
        hello = {}
        hello["username"] = self._client.requestedUsername
        password = self._client.getPassword()
        if password:
            hello["password"] = password
        room = self._client.getRoom()
        if room:
            hello["room"] = {"name": room}
        hello["version"] = "1.2.255"  # Used so newer clients work on 1.2.X server
        hello["realversion"] = syncplay.version
        hello["features"] = self._client.getFeatures()
        self._advertisedSharedSpeed = hello["features"][SHARED_PLAYBACK_SPEED]
        self.sendMessage({"Hello": hello})

    def _SetUser(self, users):
        for user in users.items():
            username = user[0]
            settings = user[1]
            room = settings["room"]["name"] if "room" in settings else None
            file_ = settings["file"] if "file" in settings else None
            if "event" in settings:
                if "joined" in settings["event"]:
                    self._client.userlist.addUser(username, room, file_)
                elif "left" in settings["event"]:
                    self._client.removeUser(username)
            elif username == self._client.getUsername() and room and self._awaitingRoom:
                self._acceptRoom(room)
            else:
                self._client.userlist.modUser(username, room, file_)

    def _acceptRoom(self, room):
        if room != utils.truncateText(self._client.getRoom(), self._client.serverFeatures["maxRoomNameLength"]):
            return
        self._awaitingRoom = False
        self._client.setRoom(room)
        self._client.ui.updateRoomName(room)
        self._client.connection.refreshStatus()

    def handleSet(self, settings):
        for (command, values) in settings.items():
            if command == "room":
                roomName = values["name"] if "name" in values else None
                if roomName and self._awaitingRoom:
                    self._acceptRoom(roomName)
            elif command == "user":
                self._SetUser(values)
            elif command == "controllerAuth":
                if values['success']:
                    self._client.controllerIdentificationSuccess(values["user"], values["room"])
                else:
                    self._client.controllerIdentificationError(values["user"], values["room"])
            elif command == "newControlledRoom":
                controlPassword = values['password']
                roomName = values['roomName']
                self._client.controlledRoomCreated(roomName, controlPassword)
            elif command == "ready":
                user, isReady = values["username"], values["isReady"]
                manuallyInitiated = values["manuallyInitiated"] if "manuallyInitiated" in values else True
                setBy = values["setBy"] if "setBy" in values else None
                self._client.setReady(user, isReady, manuallyInitiated, setBy)
            elif command == "playlistIndex" and not self._awaitingRoom:
                resetPosition = self.hadFirstPlaylistIndex
                self.hadFirstPlaylistIndex = True
                self._client.playlist.changeToPlaylistIndex(
                    values["index"], values["user"], resetPosition=resetPosition, remote=True)
            elif command == "playlistChange" and not self._awaitingRoom:
                self._client.playlist.changePlaylist(values["files"], values["user"], remote=True)
            elif command == "features":
                self._client.setUserFeatures(values["username"], values['features'])
            elif command == "playbackSpeed" and self._client.hasSharedSpeedServer() and not self._awaitingRoom:
                if isinstance(values, dict) and validSpeedRevision(values.get("speedRevision")):
                    self._client.playbackSpeedResult(values["speedRevision"], values.get("accepted") is True)

    def playerCapabilityReady(self):
        if self._waitingForSpeedProbe:
            self.sendHello()

    def updateSharedSpeedCapability(self):
        capable = self._client.supportsSharedSpeed()
        if self.logged and self._client.hasSharedSpeedServer() and capable != self._advertisedSharedSpeed:
            self._advertisedSharedSpeed = capable
            self.sendFeaturesUpdate({SHARED_PLAYBACK_SPEED: capable})

    def requestPlaybackSpeed(self, speed, revision):
        self.sendSet({"playbackSpeed": {"speed": speed, "speedRevision": revision}})

    def sendFeaturesUpdate(self, features):
        self.sendSet({"features": features})

    def sendSet(self, setting):
        self.sendMessage({"Set": setting})

    def sendRoomSetting(self, roomName, password=None):
        setting = {}
        self._awaitingRoom = True
        self.hadFirstPlaylistIndex = False
        setting["name"] = roomName
        if password:
            setting["password"] = password
        self.sendSet({"room": setting})

    def sendFileSetting(self, file_):
        self.sendSet({"file": file_})
        self.sendList()

    def sendChatMessage(self, chatMessage):
        self.sendMessage({"Chat": chatMessage})

    def handleList(self, userList):
        self._client.userlist.clearList()
        for room in userList.items():
            roomName = room[0]
            for user in room[1].items():
                userName = user[0]
                file_ = user[1]['file'] if user[1]['file'] != {} else None
                isController = user[1]['controller'] if 'controller' in user[1] else False
                isReady = user[1]['isReady'] if 'isReady' in user[1] else None
                features = user[1]['features'] if 'features' in user[1] else None
                self._client.userlist.addUser(userName, roomName, file_, noMessage=True, isController=isController, isReady=isReady, features=features)
        self._client.userlist.showUserList()

    def sendList(self):
        self.sendMessage({"List": None})

    def _extractStatePlaystateArguments(self, state):
        position = state["playstate"].get("position")
        paused = state["playstate"]["paused"] if "paused" in state["playstate"] else None
        doSeek = state["playstate"]["doSeek"] if "doSeek" in state["playstate"] else None
        setBy = state["playstate"]["setBy"] if "setBy" in state["playstate"] else None
        return position, paused, doSeek, setBy

    def _handleStatePing(self, state):
        latencyCalculation = None
        if "latencyCalculation" in state["ping"]:
            latencyCalculation = state["ping"]["latencyCalculation"]
        if "clientLatencyCalculation" in state["ping"]:
            timestamp = state["ping"]["clientLatencyCalculation"]
            senderRtt = state["ping"]["serverRtt"]
            self._pingService.receiveMessage(timestamp, senderRtt)
        messageAge = self._pingService.getLastForwardDelay()
        return messageAge, latencyCalculation

    def handleState(self, state):
        position, paused, doSeek, setBy = None, None, None, None
        speedState = None
        messageAge, latencyCalculation = 0, None
        try:
            ignore = state.get("ignoringOnTheFly", {})
            if "server" in ignore:
                self.serverIgnoringOnTheFly = ignore["server"]
                self.clientIgnoringOnTheFly = 0
            elif ignore.get("client") == self.clientIgnoringOnTheFly:
                self.clientIgnoringOnTheFly = 0
            if "playstate" in state:
                position, paused, doSeek, setBy = self._extractStatePlaystateArguments(state)
                if self._client.hasSharedSpeedServer() and self._advertisedSharedSpeed:
                    playstate = state["playstate"]
                    speed = playstate.get("speed")
                    enabled = playstate.get("speedEnabled")
                    revision = playstate.get("speedRevision")
                    if (validRoomSpeed(speed) and type(enabled) is bool and validSpeedRevision(revision)
                            and (enabled or speed == 1.0)):
                        speedState = speed, enabled, revision
            if "ping" in state:
                messageAge, latencyCalculation = self._handleStatePing(state)
            usable = (type(position) in (float, int) and math.isfinite(position)
                      and isinstance(paused, bool))
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            self.dropWithError("protocol")
            return
        if not self._awaitingRoom and (usable or "ping" in state):
            self._client.connection.stateReceived(self, usable)
        if usable and not self._awaitingRoom and not self.clientIgnoringOnTheFly:
            if speedState is None:
                self._client.updateGlobalState(position, paused, doSeek, setBy, messageAge)
            else:
                self._client.updateGlobalState(position, paused, doSeek, setBy, messageAge, speedState)
        if self.serverIgnoringOnTheFly:
            # Acknowledge the server's forced update without echoing the old player state.
            self.sendState(None, None, None, latencyCalculation)
        else:
            position, paused, doSeek, stateChange = self._client.getLocalState()
            self.sendState(position, paused, doSeek, latencyCalculation, stateChange)

    def sendState(self, position, paused, doSeek, latencyCalculation, stateChange=False):
        if not self._client.canSendPlayback():
            position, paused, stateChange = None, None, False
        speedState = self._client.getLocalSpeedState(stateChange)
        if self._client.hasSharedSpeedServer() and self._advertisedSharedSpeed and speedState is None:
            position, paused, stateChange = None, None, False
        state = {}
        positionAndPausedIsSet = position is not None and paused is not None
        clientIgnoreIsNotSet = self.clientIgnoringOnTheFly == 0 or self.serverIgnoringOnTheFly != 0
        if clientIgnoreIsNotSet and positionAndPausedIsSet:
            state["playstate"] = {}
            state["playstate"]["position"] = position
            state["playstate"]["paused"] = paused
            if doSeek:
                state["playstate"]["doSeek"] = doSeek
            if speedState is not None:
                state["playstate"].update(speedState)
        state["ping"] = {}
        if latencyCalculation:
            state["ping"]["latencyCalculation"] = latencyCalculation
        state["ping"]["clientLatencyCalculation"] = self._pingService.newTimestamp()
        state["ping"]["clientRtt"] = self._pingService.getRtt()
        if stateChange:
            self.clientIgnoringOnTheFly += 1
        if self.serverIgnoringOnTheFly or self.clientIgnoringOnTheFly:
            state["ignoringOnTheFly"] = {}
            if self.serverIgnoringOnTheFly:
                state["ignoringOnTheFly"]["server"] = self.serverIgnoringOnTheFly
                self.serverIgnoringOnTheFly = 0
            if self.clientIgnoringOnTheFly:
                state["ignoringOnTheFly"]["client"] = self.clientIgnoringOnTheFly
        self.sendMessage({"State": state})

    def requestControlledRoom(self, room, password):
        self.sendSet({
            "controllerAuth": {
                "room": room,
                "password": password
            }
        })

    def handleChat(self, message):
        username = message['username']
        userMessage = message['message']
        self._client.ui.showChatMessage(username, userMessage)

    def setReady(self, isReady, manuallyInitiated=True, username=None):
        if username:
            self.sendSet({
                "ready": {
                    "isReady": isReady,
                    "manuallyInitiated": manuallyInitiated,
                    "username": username
                }
            })
        else:
            self.sendSet({
                "ready": {
                    "isReady": isReady,
                    "manuallyInitiated": manuallyInitiated
                }
            })

    def setPlaylist(self, files):
        if not self._client.canSendPlaylist():
            return
        self.sendSet({
            "playlistChange": {
                "files": files
            }
        })

    def setPlaylistIndex(self, index):
        if not self._client.canSendPlaylist():
            return
        self.sendSet({
            "playlistIndex": {
                "index": index
            }
        })

    def handleError(self, error):
        message = error.get("message", "") if isinstance(error, dict) else ""
        passwordErrors = (getMessage("password-required-server-error", "en"),
                          getMessage("wrong-password-server-error", "en"))
        self.dropWithError("password" if message in passwordErrors else "protocol")

    def sendError(self, message):
        self.sendMessage({"Error": {"message": message}})

    def sendTLS(self, message):
        self.sendMessage({"TLS": message})

    def handleTLS(self, message):
        answer = message.get("startTLS") if isinstance(message, dict) else None
        if self._sentHello or self._startedTLS:
            self.dropWithError("protocol")
        elif answer == "true":
            options = self._client.protocolFactory.options
            # Older Twisted versions do not notify IHandshakeListener. Replacing
            # their OpenSSL callback would interfere with peer identity verification.
            if options is None or twistedVersion < Version("twisted", 17, 1, 0):
                self._client.connection.reject(self, "tls-unavailable")
                return
            self._startedTLS = True
            try:
                self.transport.startTLS(options)
            except Exception:
                self.dropWithError("certificate")
                return
        elif answer == "false":
            self.sendHello()
        else:
            self.dropWithError("protocol")

    def handshakeCompleted(self):
        if not self._isCurrent() or not self._startedTLS or self._sentHello:
            return
        self._serverCertificateTLS = self.transport.getPeerCertificate()
        if not self._serverCertificateTLS:
            self.dropWithError("certificate")
            return
        self._tlsVerified = True

        self._subjectTLS = ""
        try:
            from cryptography import x509
            cryptographyCertificate = self._serverCertificateTLS.to_cryptography()
            subjectAltName = cryptographyCertificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = subjectAltName.get_values_for_type(x509.DNSName)
            names = names + ["IP Address:{}".format(ipAddress) for ipAddress in subjectAltName.get_values_for_type(x509.IPAddress)]
            self._subjectTLS = ", ".join([str(name) for name in names])
        except Exception:
            pass

        if not self._subjectTLS:
            try:
                if hasattr(self._serverCertificateTLS, "get_extension_count") and hasattr(self._serverCertificateTLS, "get_extension"):
                    for x in range(0,self._serverCertificateTLS.get_extension_count()):
                        extension = self._serverCertificateTLS.get_extension(x)
                        if extension.get_short_name() == b'subjectAltName':
                            self._subjectTLS = str(extension).replace("DNS:", "")
                            break
            except Exception:
                pass

        if not self._subjectTLS:
            self._subjectTLS = self._client._config.get("host", "") or ""

        self._issuerTLS = self._serverCertificateTLS.get_issuer().CN
        self._expiredTLS =self._serverCertificateTLS.has_expired()
        self._expireDateTLS = datetime.strptime(self._serverCertificateTLS.get_notAfter().decode('ascii'), '%Y%m%d%H%M%SZ')

        self._encryptedConnectionTLS = self.transport.protocol._tlsConnection
        self._connVersionNumberTLS = self._encryptedConnectionTLS.get_protocol_version()
        self._connVersionStringTLS = self._encryptedConnectionTLS.get_protocol_version_name()
        self._cipherNameTLS = self._encryptedConnectionTLS.get_cipher_name()
    
        if self._connVersionNumberTLS == 771:
            self._connVersionNumberTLS = '1.2'
        elif self._connVersionNumberTLS == 772:
            self._connVersionNumberTLS = '1.3'

        self._client.ui.setSSLMode( True,
                                    {'subject': self._subjectTLS, 'issuer': self._issuerTLS, 'expires': self._expireDateTLS,
                                    'protocolString': self._connVersionStringTLS, 'protocolVersion': self._connVersionNumberTLS,
                                    'cipher': self._cipherNameTLS})

        self.sendHello()


class SyncServerProtocol(JSONCommandProtocol):
    def __init__(self, factory):
        self._factory = factory
        self._version = None
        self._features = None
        self._logged = False
        self.clientIgnoringOnTheFly = 0
        self.serverIgnoringOnTheFly = 0
        self._forcedStateSequence = 0
        self._pingService = PingService()
        self._clientLatencyCalculation = 0
        self._clientLatencyCalculationArrivalTime = 0
        self._watcher = None

    def __hash__(self):
        return hash('|'.join((
            self.transport.getPeer().host,
            str(id(self)),
        )))

    def requireLogged(f):  # @NoSelf
        @wraps(f)
        def wrapper(self, *args, **kwds):
            if not self._logged:
                self.dropWithError(getMessage("not-known-server-error"))
            return f(self, *args, **kwds)
        return wrapper

    def showDebugMessage(self, line):
        pass

    def dropWithError(self, error):
        print(getMessage("client-drop-server-error").format(self.transport.getPeer().host, error))
        self.sendError(error)
        self.drop()

    def connectionLost(self, reason):
        self._factory.removeWatcher(self._watcher)

    def getFeatures(self):
        if not self._features:
            self._features = {}
            self._features["sharedPlaylists"] = meetsMinVersion(self._version, SHARED_PLAYLIST_MIN_VERSION)
            self._features["chat"] = meetsMinVersion(self._version, CHAT_MIN_VERSION)
            self._features["featureList"] = False
            self._features["readiness"] = meetsMinVersion(self._version, USER_READY_MIN_VERSION)
            self._features["managedRooms"] = meetsMinVersion(self._version, CONTROLLED_ROOMS_MIN_VERSION)
            self._features["persistentRooms"] = False
            self._features["uiMode"] = UNKNOWN_UI_MODE
        return self._features

    def isLogged(self):
        return self._logged

    def meetsMinVersion(self, version):
        return self._version >= version

    def getVersion(self):
        return self._version

    def _extractHelloArguments(self, hello):
        roomName = None
        if "username" in hello:
            username = hello["username"]
            username = username.strip()
        else:
            username = None
        serverPassword = hello["password"] if "password" in hello else None
        room = hello["room"] if "room" in hello else None
        if room:
            if "name" in room:
                roomName = room["name"]
                roomName = roomName.strip()
            else:
                roomName = None
        version = hello["version"] if "version" in hello else None
        version = hello["realversion"] if "realversion" in hello else version
        features = hello["features"] if "features" in hello else None
        return username, serverPassword, roomName, version, features

    def _checkPassword(self, serverPassword):
        if self._factory.password:
            if not serverPassword:
                self.dropWithError(getMessage("password-required-server-error"))
                return False
            if serverPassword != self._factory.password:
                self.dropWithError(getMessage("wrong-password-server-error"))
                return False
        return True

    def handleHello(self, hello):
        username, serverPassword, roomName, version, features = self._extractHelloArguments(hello)
        if not username or not roomName or not version:
            self.dropWithError(getMessage("hello-server-error"))
            return
        else:
            if not self._checkPassword(serverPassword):
                return
            self._version = version
            self.setFeatures(features)
            self._factory.addWatcher(self, username, roomName)
            self._logged = True
            self.sendHello(version)

    def persistentRoomWarning(self, clientFeatures, serverFeatures):
        serverPersistentRooms = serverFeatures["persistentRooms"]
        clientPersistentRooms = clientFeatures["persistentRooms"] if "persistentRooms" in clientFeatures else False
        return serverPersistentRooms and not clientPersistentRooms

    @requireLogged
    def handleChat(self, chatMessage):
        if not self._factory.disableChat:
            self._factory.sendChat(self._watcher, chatMessage)

    def setFeatures(self, features):
        self._features = features if isinstance(features, dict) else None

    def sendFeaturesUpdate(self):
        self.sendSet({"features": self.getFeatures()})

    def setWatcher(self, watcher):
        self._watcher = watcher

    def sendHello(self, clientVersion):
        hello = {}
        username = self._watcher.getName()
        hello["username"] = username
        userIp = self.transport.getPeer().host
        room = self._watcher.getRoom()
        if room:
            hello["room"] = {"name": room.getName()}
        hello["version"] = clientVersion  # Used so 1.2.X client works on newer server
        hello["realversion"] = syncplay.version
        hello["features"] = self._factory.getFeatures()
        hello["motd"] = self._factory.getMotd(userIp, username, room, clientVersion)
        if self.persistentRoomWarning(clientFeatures=self._features, serverFeatures=hello["features"]):
            span = "\n\n" if hello["motd"] else ""
            hello["motd"] = getMessage("persistent-rooms-notice") + span + hello["motd"]
        self.sendMessage({"Hello": hello})

    @requireLogged
    def handleSet(self, settings):
        for set_ in settings.items():
            command = set_[0]
            if command == "room":
                roomName = set_[1]["name"] if "name" in set_[1] else None
                self._factory.setWatcherRoom(self._watcher, roomName)
            elif command == "file":
                self._watcher.setFile(set_[1])
            elif command == "controllerAuth":
                password = set_[1]["password"] if "password" in set_[1] else None
                room = set_[1]["room"] if "room" in set_[1] else None
                self._factory.authRoomController(self._watcher, password, room)
            elif command == "ready":
                manuallyInitiated = set_[1]['manuallyInitiated'] if "manuallyInitiated" in set_[1] else False
                username = set_[1]['username'] if "username" in set_[1] else None
                self._factory.setReady(self._watcher, set_[1]['isReady'], manuallyInitiated=manuallyInitiated, username=username)
            elif command == "playlistChange":
                self._factory.setPlaylist(self._watcher, set_[1]['files'])
            elif command == "playlistIndex":
                self._factory.setPlaylistIndex(self._watcher, set_[1]['index'])
            elif command == "features":
                if isinstance(set_[1], dict):
                    self.getFeatures().update(set_[1])
                    self._watcher.getRoom().refreshSpeedEligibility()
            elif command == "playbackSpeed":
                self._handlePlaybackSpeed(set_[1])

    def _handlePlaybackSpeed(self, request):
        if not self._watcher.supportsSharedSpeed() or not isinstance(request, dict):
            return
        revision = request.get("speedRevision")
        if not validSpeedRevision(revision):
            return
        room = self._watcher.getRoom()
        accepted = room.requestPlaybackSpeed(self._watcher, request.get("speed"), revision)
        self.sendSet({"playbackSpeed": {"speedRevision": revision, "accepted": accepted}})

    def sendSet(self, setting):
        self.sendMessage({"Set": setting})

    def sendNewControlledRoom(self, roomName, password):
        self.sendSet({
            "newControlledRoom": {
                "password": password,
                "roomName": roomName
            }
        })

    def sendControlledRoomAuthStatus(self, success, username, roomname):
        self.sendSet({
            "controllerAuth": {
                "user": username,
                "room": roomname,
                "success": success
            }
        })

    def sendSetReady(self, username, isReady, manuallyInitiated=True, setByUsername=None):
        if setByUsername:
            self.sendSet({
                "ready": {
                    "username": username,
                    "isReady": isReady,
                    "manuallyInitiated": manuallyInitiated,
                    "setBy": setByUsername
                }
            })
        else:
            self.sendSet({
                "ready": {
                    "username": username,
                    "isReady": isReady,
                    "manuallyInitiated": manuallyInitiated
                }
            })

    def setPlaylist(self, username, files):
        self.sendSet({
            "playlistChange": {
                "user": username,
                "files": files
            }
        })

    def setPlaylistIndex(self, username, index):
        self.sendSet({
            "playlistIndex": {
                "user": username,
                "index": index
            }
        })

    def sendUserSetting(self, username, room, file_, event):
        room = {"name": room.getName()}
        user = {username: {}}
        user[username]["room"] = room
        if file_:
            user[username]["file"] = file_
        if event:
            user[username]["event"] = event
        self.sendSet({"user": user})

    def _addUserOnList(self, userlist, watcher):
        room = watcher.getRoom()
        if room:
            if room.getName() not in userlist:
                userlist[room.getName()] = {}
            userFile = {
                "position": 0,
                "file": watcher.getFile() if watcher.getFile() else {},
                "controller": watcher.isController(),
                "isReady": watcher.isReady(),
                "features": watcher.getFeatures()
            }
            userlist[room.getName()][watcher.getName()] = userFile

    def _addDummyUserOnList(self, userlist, dummyRoom,dummyCount):
        if dummyRoom not in userlist:
            userlist[dummyRoom] = {}
        dummyFile = {
            "position": 0,
            "file": {},
            "controller": False,
            "isReady": True,
            "features": []
        }
        userlist[dummyRoom][" " * dummyCount] = dummyFile

    def sendList(self):
        userlist = {}
        watchers = self._factory.getAllWatchersForUser(self._watcher)
        dummyCount = 0
        for watcher in watchers:
            self._addUserOnList(userlist, watcher)
        if self._watcher.isGUIUser(self.getFeatures()):
            for emptyRoom in self._factory.getEmptyPersistentRooms():
                dummyCount += 1
                self._addDummyUserOnList(userlist, emptyRoom, dummyCount)
        self.sendMessage({"List": userlist})

    @requireLogged
    def handleList(self, _):
        self.sendList()

    def sendState(self, position, paused, doSeek, setBy, forced=False):
        if self._clientLatencyCalculationArrivalTime:
            processingTime = time.time() - self._clientLatencyCalculationArrivalTime
        else:
            processingTime = 0
        playstate = {
                     "position": position if position else 0,
                     "paused": paused,
                     "doSeek": doSeek,
                     "setBy": setBy.getName() if setBy else None
        }
        if self._watcher.supportsSharedSpeed():
            speed, enabled, revision = self._watcher.getRoom().getSharedSpeed()
            playstate.update(speed=speed, speedEnabled=enabled, speedRevision=revision)
        ping = {
                "latencyCalculation": self._pingService.newTimestamp(),
                "serverRtt": self._pingService.getRtt()
                }
        if self._clientLatencyCalculation:
            ping["clientLatencyCalculation"] = self._clientLatencyCalculation + processingTime
            self._clientLatencyCalculation = 0
        state = {
                 "ping": ping,
                 "playstate": playstate,
                }
        if forced:
            self._forcedStateSequence += 1
            self.serverIgnoringOnTheFly = self._forcedStateSequence
        if self.serverIgnoringOnTheFly or self.clientIgnoringOnTheFly:
            state["ignoringOnTheFly"] = {}
            if self.serverIgnoringOnTheFly:
                state["ignoringOnTheFly"]["server"] = self.serverIgnoringOnTheFly
            if self.clientIgnoringOnTheFly:
                state["ignoringOnTheFly"]["client"] = self.clientIgnoringOnTheFly
                self.clientIgnoringOnTheFly = 0
        if self.serverIgnoringOnTheFly == 0 or forced:
            self.sendMessage({"State": state})

    def _extractStatePlaystateArguments(self, state):
        position = state["playstate"]["position"] if "position" in state["playstate"] else 0
        paused = state["playstate"]["paused"] if "paused" in state["playstate"] else None
        doSeek = state["playstate"]["doSeek"] if "doSeek" in state["playstate"] else None
        return position, paused, doSeek

    @requireLogged
    def handleState(self, state):
        position, paused, doSeek, latencyCalculation = None, None, None, None
        speedRevision, localSpeed = None, None
        if "ignoringOnTheFly" in state:
            ignore = state["ignoringOnTheFly"]
            if "server" in ignore:
                if self.serverIgnoringOnTheFly == ignore["server"]:
                    self.serverIgnoringOnTheFly = 0
            if "client" in ignore:
                self.clientIgnoringOnTheFly = ignore["client"]
        if "playstate" in state:
            position, paused, doSeek = self._extractStatePlaystateArguments(state)
            if self._watcher.supportsSharedSpeed():
                speedRevision = state["playstate"].get("speedRevision")
                localSpeed = state["playstate"].get("localSpeed")
        if "ping" in state:
            latencyCalculation = state["ping"]["latencyCalculation"] if "latencyCalculation" in state["ping"] else 0
            clientRtt = state["ping"]["clientRtt"] if "clientRtt" in state["ping"] else 0
            self._clientLatencyCalculation = state["ping"]["clientLatencyCalculation"] if "clientLatencyCalculation" in state["ping"] else 0
            self._clientLatencyCalculationArrivalTime = time.time()
            self._pingService.receiveMessage(latencyCalculation, clientRtt)
        if self.serverIgnoringOnTheFly == 0:
            self._watcher.updateState(position, paused, doSeek, self._pingService.getLastForwardDelay(),
                                      speedRevision, localSpeed)

    def handleError(self, error):
        self.dropWithError(error["message"])  # TODO: more processing and fallbacking

    def sendError(self, message):
        self.sendMessage({"Error": {"message": message}})

    def sendTLS(self, message):
        self.sendMessage({"TLS": message})

    def handleTLS(self, message):
        inquiry = message["startTLS"] if "startTLS" in message else None
        if "send" in inquiry:
            if not self.isLogged() and self._factory.serverAcceptsTLS:
                lastEditCertTime = self._factory.checkLastEditCertTime()
                if lastEditCertTime is not None and lastEditCertTime != self._factory.lastEditCertTime:
                    self._factory.updateTLSContextFactory()
                if self._factory.options is not None:
                    self.sendTLS({"startTLS": "true"})
                    self.transport.startTLS(self._factory.options)
                else:
                    self.sendTLS({"startTLS": "false"})
            else:
                self.sendTLS({"startTLS": "false"})


class PingService(object):

    def __init__(self):
        self._rtt = 0
        self._fd = 0
        self._avrRtt = 0

    def newTimestamp(self):
        return time.time()

    def receiveMessage(self, timestamp, senderRtt):
        if not timestamp:
            return
        self._rtt = time.time() - timestamp
        if self._rtt < 0 or senderRtt < 0:
            return
        if not self._avrRtt:
            self._avrRtt = self._rtt
        self._avrRtt = self._avrRtt * PING_MOVING_AVERAGE_WEIGHT + self._rtt * (1 - PING_MOVING_AVERAGE_WEIGHT)
        if senderRtt < self._rtt:
            self._fd = self._avrRtt / 2 + (self._rtt - senderRtt)
        else:
            self._fd = self._avrRtt / 2

    def getLastForwardDelay(self):
        return self._fd

    def getRtt(self):
        return self._rtt
