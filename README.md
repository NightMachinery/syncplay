<!---
# Copyright (C) 2019 Syncplay
# This file is licensed under the MIT license - http://opensource.org/licenses/MIT

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
-->

# SyncPlayU
![GitHub Actions build status](https://github.com/Syncplay/syncplay/workflows/Build/badge.svg)

Solution to synchronize video playback across multiple instances of mpv, VLC, MPC-HC, MPC-BE and mplayer2 over the Internet.

This fork automatically rejoins a desktop room after an interrupted connection. An unsuccessful first connection leaves the window and player open. Use **File → Reconnect** or type `retry` in the console.

With the Night 3 server and a room full of supported mpv viewers, native mpv speed controls change playback speed for everyone. On an upstream server, mpv speed changes stay local. See [shared playback speed](docs/shared-playback-speed.md).

## Install on macOS

Download `SyncPlayU-macOS.dmg` from the [latest SyncPlayU release](https://github.com/NightMachinery/syncplay/releases/latest), or install it with Homebrew. If Homebrew installed Syncplay, remove that cask first to switch while keeping its settings.

```sh
if brew list --cask syncplay >/dev/null 2>&1; then
  brew uninstall --cask syncplay
fi
brew install --cask NightMachinery/tap/syncplayu
```

For a bug report, include both the upstream and Night versions from About, the startup log or `--version`. Use `--version-json` to read them separately from a script. See the [desktop release details](docs/desktop-release.md).

To run from this checkout, install the dependencies with `python -m pip install -r requirements.txt -r requirements_gui.txt`, then run `python syncplayClient.py` and choose your server, room and media player. See the [desktop recovery behavior and verification](docs/desktop-reconnection.md).

## Upstream Syncplay

[Project website](https://syncplay.pl/) and [downloads](https://syncplay.pl/download/).

## What does it do

Syncplay synchronises the position and play state of multiple media players so that the viewers can watch the same thing at the same time.
This means that when one person pauses/unpauses playback or seeks (jumps position) within their media player then this will be replicated across all media players connected to the same server and in the same 'room' (viewing session).
When a new person joins they will also be synchronised. Syncplay also includes text-based chat so you can discuss a video as you watch it (or you could use third-party Voice over IP software to talk over a video).

## What it doesn't do

Syncplay is not a file sharing service.

## License

This project, the Syncplay released binaries, and all the files included in this repository unless stated otherwise in the header of the file, are licensed under the [Apache License, version 2.0](https://www.apache.org/licenses/LICENSE-2.0.html). A copy of this license is included in the LICENSE file of this repository. Licenses and attribution notices for third-party media are set out in [third-party-notices.txt](syncplay/resources/third-party-notices.txt).

## Authors
* *Initial concept and core internals developer* - Uriziel.
* *GUI design and current lead developer* - Et0h.
* *Original SyncPlay code* - Tomasz Kowalczyk (Fluxid), who developed SyncPlay at https://github.com/fluxid/syncplay
* *Other contributors* - See http://syncplay.pl/about/development/
