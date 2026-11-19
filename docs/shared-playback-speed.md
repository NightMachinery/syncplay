# Shared playback speed

SyncPlayU Night 3 shares native mpv speed changes between eligible viewers. Use mpv's usual speed controls, including Backspace to reset to 1×, or set its native `speed` property. In a managed room, only an authenticated room operator can change the rate. The supported room range is 0.25× to 4×.

The server allows shared speed once every admitted viewer supports it. When an older client or a viewer using an unsupported player joins, the room returns to 1× at its current playback position. Existing viewers get one notice per room connection. A new speed can be chosen after everyone in the room supports it again. Newly created and empty rooms start at 1×, including rooms with a persisted playlist. Active rooms retain their rate across late joins and reconnects by capable viewers.

Night 3 advertises the feature after confirming mpv's native speed reporting. mpv.net, IINA, VLC and the other players use ordinary pause and seek synchronization. With a stock server, changing speed in mpv stays local and ordinary playback synchronization still operates.

## Server upgrade

Deploy from the `v1.7.7-night.3` source tag and run `python syncplayServer.py` with the existing server settings and Python dependencies. The server code is portable Python and the feature requires no flags, database migration or player installation on the server. Keep the existing room database, TLS paths and managed-room salt. Room speeds live in memory and default to 1× after a server restart.

A protocol check can send the usual TLS inquiry if needed and then a normal Syncplay Hello. The response advertises `Hello.features.sharedPlaybackSpeed` as JSON `true`, with `Hello.realversion` still `"1.7.7"`. A functional check uses two Night 3 clients with mpv in one room. Set mpv's `speed` to 1.5 in one client and confirm it changes in the other. Add a stock client and confirm that both Night 3 clients return to 1×.

## Protocol and clocks

- The client opts in with `Hello.features.sharedPlaybackSpeed: true`. If mpv confirms support after Hello, the client updates the Boolean with `Set.features` on a server that opted in.
- The server sends negotiated viewers the authoritative `State.playstate` fields `speed`, `speedEnabled` and `speedRevision`. It sends the usual playback fields to every viewer. The revision changes when the room rate or eligibility changes and is unique within the server process.
- A native speed change sends `Set.playbackSpeed` with `speed` and the last observed `speedRevision`. The server checks the rate, capability, revision and managed-room privileges, then acknowledges the request with `speedRevision` and `accepted`. Accepted changes are broadcast as authoritative playback state. Negotiated client samples carry `speedRevision` and `localSpeed` for position estimation. The server accepts the room rate or its drift-corrected value for telemetry.
- Rate transitions anchor the room at its current position before changing how it projects elapsed time. Pause, seek, and latency calculations use the appropriate room or local rate. The mpv bridge tags Syncplay's own speed writes, so a room baseline, drift correction or correction reset cannot be mistaken for a native change. Drift correction multiplies the room rate by its normal factor and restores the room rate when it ends.

## Verification

The deterministic suite covers rate validation, room and watcher clocks, pause and seek during a rate transition, mixed-room eligibility, managed-room permissions, stale revisions, room reset, and suppression of mpv write echoes.

On macOS 14.3.1 with Apple Silicon, two real mpv processes joined an isolated Night 3 server over loopback. Native changes to 1.5× and 0.75× reached both players. The two positions advanced 3.32 and 3.28 seconds over a 2.2-second interval at 1.5×. Native pause, resume, and a seek to 42 seconds took effect on both players. Backspace reset the room to 1×. A late join, a reconnect and room switching applied the destination room's speed. A third viewer from stock upstream commit `49e2a1b` forced 1×. Its departure left the room at 1×. Packet capture verified that the legacy client got ordinary State packets and that the returning Night 3 client sent no stale speed request.

With the same stock commit as the server, two Night 3 mpv viewers synchronized seek and resume while a native 0.75× change remained local. These tests used synthetic media and isolated configuration on macOS. Other player implementations and operating systems were outside the live test matrix.
