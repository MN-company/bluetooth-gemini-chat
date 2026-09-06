#!/bin/bash
set -eo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
IOS_DIR="$DIR/GeminiAirBridge-iOS"
cd "$IOS_DIR"

echo "=== Step 1: Generate .xcodeproj ==="
python3 "$DIR/scripts/generate_xcodeproj.py"

echo ""
echo "=== Step 2: Build .app ==="
xcodebuild -project GeminiAirBridge-iOS.xcodeproj \
  -scheme GeminiAirBridge \
  -sdk iphoneos \
  -destination generic/platform=iOS \
  -configuration Release \
  build \
  CODE_SIGNING_ALLOWED=NO

echo ""
echo "=== Step 3: Find .app and package IPA ==="
DD=~/Library/Developer/Xcode/DerivedData
APP=$(find "$DD" -maxdepth 5 -type d -name "GeminiAirBridge.app" 2>/dev/null | head -1)

if [ -z "$APP" ]; then
  APP=$(find /Users/runner -maxdepth 8 -type d -name "GeminiAirBridge.app" 2>/dev/null | head -1)
fi

if [ -z "$APP" ]; then
  echo "ERROR: .app not found. Debugging:" >&2
  find "$DD" -maxdepth 4 -type d -name "Release-*" 2>/dev/null | while read D; do
    ls -la "$D/" 2>/dev/null || echo "no dir: $D"
  done
  exit 1
fi

echo "App: $APP"
ls -la "$APP/"

mkdir -p Payload
cp -R "$APP" Payload/
cd Payload && zip -qr "$IOS_DIR/GeminiAirBridge.ipa" GeminiAirBridge.app/ && cd ..
rm -rf Payload

echo ""
echo "=== Done ==="
ls -lh "$IOS_DIR/GeminiAirBridge.ipa"