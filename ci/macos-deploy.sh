#!/usr/bin/env bash

set -ex

python3 ci/macos_app_cleaner.py
python3 ci/macos_app_arch_check.py

mkdir "dist/SyncPlayU.app/Contents/Resources/"{English,en_AU,en_GB,German,Italian,ru,Spanish,es_419}.lproj

python3 ci/macos_app_adhoc_sign.py

pip3 install dmgbuild
mv syncplay/resources/macOS_readme.pdf syncplay/resources/.macOS_readme.pdf

python3 -m dmgbuild -s appdmg.py "SyncPlayU" "dist_actions/SyncPlayU-macOS.dmg"
