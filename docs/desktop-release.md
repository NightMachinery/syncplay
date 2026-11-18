# Syncplay Night desktop release

The first release pairs upstream Syncplay 1.7.7 with Night 1. About, the normal desktop startup log and `--version` display both. `--version-json` returns `upstream_version` and `night_version` separately. Connections continue to use the upstream numeric version.

The [GitHub release](https://github.com/NightMachinery/syncplay/releases/tag/v1.7.7-night.1) includes `Syncplay-Night-macOS.dmg` and `Syncplay-Night-release.json`. The DMG contains `Syncplay Night.app` for Apple Silicon with macOS 14.0 or later. Its bundle identifier is `io.github.NightMachinery.SyncplayNight`. The JSON records the architecture, minimum macOS version and SHA-256.

## Build on macOS

Use an Apple Silicon Mac and a framework build of Python. The Night 1 build uses Python 3.12, PySide6 Essentials 6.8.3 and py2app 0.28.8. Build in a disposable checkout of the release commit on a disk with room for a virtual environment and the application bundle.

```sh
python3.12 -m venv .build-venv
.build-venv/bin/python -m pip install -r requirements.txt 'requests==2.34.2' \
    'PySide6-Essentials==6.8.3' 'py2app==0.28.8' 'setuptools==70.3.0' wheel
.build-venv/bin/python buildPy2app.py py2app --arch=arm64
.build-venv/bin/python ci/macos_app_cleaner.py
.build-venv/bin/python ci/macos_app_adhoc_sign.py
mkdir image
ditto 'dist/Syncplay Night.app' 'image/Syncplay Night.app'
ln -s /Applications image/Applications
hdiutil create -volname 'Syncplay Night' -srcfolder image -format UDZO Syncplay-Night-macOS.dmg
```

The signing step removes library search paths inherited from the build machine. The release uses ad-hoc code signing and has no Apple notarization. Check the bundle signature and the published checksum. macOS may require local approval to open a downloaded copy.

## Verification

Night 1 passed 25 deterministic tests. On macOS 14.3.1, the app copied from the DMG joined a stock server in console and offscreen Qt modes and synchronized synthetic media in mpv. Both clients sent `1.7.7` in their Hello. The packaged client also rejected an untrusted TLS certificate. Version output and About displayed both values. The bundle passed strict code-signature verification and an audit of all 90 Mach-O files.
