#!/usr/bin/env bash

set -ex

python3 ci/macos_app_cleaner.py
python3 ci/macos_app_arch_check.py

mkdir "dist/Syncplay Night.app/Contents/Resources/"{English,en_AU,en_GB,German,Italian,ru,Spanish,es_419}.lproj

python3 ci/macos_app_adhoc_sign.py

pip3 install dmgbuild
mv syncplay/resources/macOS_readme.pdf syncplay/resources/.macOS_readme.pdf

python3 -m dmgbuild -s appdmg.py "Syncplay Night" "dist_actions/Syncplay_${VER}.dmg"
