#!/bin/bash
set -e
cd "$(dirname "$0")"
mkdir -p /tmp/rosie-model && cd /tmp/rosie-model
[ -f vosk.zip ] || curl -fL -o vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip -qo vosk.zip
rm -rf "$OLDPWD/app/src/main/assets/model-en-us"
mkdir -p "$OLDPWD/app/src/main/assets"
mv vosk-model-small-en-us-0.15 "$OLDPWD/app/src/main/assets/model-en-us"
rm -f "$OLDPWD/app/src/main/assets/model-en-us/README"
echo "model ready"
