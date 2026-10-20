#!/bin/bash
# Build the Android app. Output: ~/assistant/android/app/build/outputs/apk/debug/app-debug.apk
export ANDROID_HOME=$HOME/Android/sdk JAVA_HOME=$(dirname $(dirname $(readlink -f $(which javac))))
cd "$(dirname "$0")" && ~/Android/gradle-8.7/bin/gradle --no-daemon -q assembleDebug && ls -la app/build/outputs/apk/debug/app-debug.apk
