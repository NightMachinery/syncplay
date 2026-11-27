# SyncPlayU desktop release

The SyncPlayU release pairs upstream Syncplay 1.7.7 with Night 3. About, the normal desktop startup log and `--version` display both. `--version-json` returns `upstream_version` and `night_version` separately. Connections continue to use the upstream numeric version.

The [latest GitHub release](https://github.com/NightMachinery/syncplay/releases/latest) includes `SyncPlayU-macOS.dmg` and `SyncPlayU-release.json`. The DMG contains `SyncPlayU.app` for Apple Silicon with macOS 14.0 or later. Its bundle identifier is `io.github.NightMachinery.SyncPlayU`. The JSON records the source commit, architecture, minimum macOS version and SHA-256 of the branded download.

## Build on Windows

Run the **SyncPlayU Windows portable** workflow from the intended source commit. It uses Windows Server 2022 x64, Python 3.11, PySide6 Essentials 6.11.1 and py2exe 0.14.0.0. The workflow runs the unit tests, extracts the ZIP, checks both CLI version formats, and opens the packaged configuration window before uploading the build artifact.

The Windows download is `SyncPlayU-Windows-x64.zip`. Extract the entire archive to a writable folder and run `SyncPlayU.exe`. The paired `SyncPlayUConsole.exe` supports `--version` and `--version-json`. The archive includes a blank `syncplay.ini` for portable connection and playback settings, and `build-info.json` records the exact source commit and both version numbers. The Windows executables are unsigned.

## Build on macOS

Use an Apple Silicon Mac and a framework build of Python. The build uses Python 3.12, PySide6 Essentials 6.8.3 and py2app 0.28.8. Build in a disposable checkout of the release commit on a disk with room for a virtual environment and the application bundle.

```sh
python3.12 -m venv .build-venv
.build-venv/bin/python -m pip install -r requirements.txt 'requests==2.34.2' \
    'PySide6-Essentials==6.8.3' 'py2app==0.28.8' 'setuptools==70.3.0' wheel
.build-venv/bin/python buildPy2app.py py2app --arch=arm64
.build-venv/bin/python ci/macos_app_cleaner.py
.build-venv/bin/python ci/macos_app_adhoc_sign.py
mkdir image
ditto dist/SyncPlayU.app image/SyncPlayU.app
ln -s /Applications image/Applications
hdiutil create -volname SyncPlayU -srcfolder image -format UDZO SyncPlayU-macOS.dmg
```

The signing step removes library search paths inherited from the build machine. The release uses ad-hoc code signing and has no Apple notarization. Check the bundle signature and the published checksum. macOS may require local approval to open a downloaded copy.

Qt uses `syncplay/resources/syncplayu.png`. The macOS bundle uses `syncplay/resources/syncplayu.icns`, a multi-size icon with transparent Dock padding derived from the same artwork.

## Verification

Night 3 passed 34 deterministic tests. See [shared playback speed](shared-playback-speed.md) for the real-mpv, mixed-client and stock-server results and how to verify an upgraded server.

Night 2 passed 25 deterministic tests. On macOS 14.3.1, the app copied from the DMG joined a stock server in console and offscreen Qt modes and synchronized synthetic media in mpv. The console recovered after a three-second drop. Both clients sent `1.7.7` in their Hello. The packaged client also rejected an untrusted TLS certificate. The CLI, Qt startup log and About showed the SyncPlayU name and both versions. Both icons matched the project assets. The bundle passed strict code-signature verification and an audit of all 90 Mach-O files.
