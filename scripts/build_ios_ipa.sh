#!/bin/bash
set -eo pipefail

# Build iOS IPA for Gemini AirBridge
# Works on GitHub Actions macos-15 runner or local macOS with Xcode

DIR="$(cd "$(dirname "$0")/.." && pwd)"
IOS_DIR="$DIR/GeminiAirBridge-iOS"
cd "$IOS_DIR"

echo "=== Step 1: Build .app ==="
xcodebuild -scheme GeminiAirBridge \
  -sdk iphoneos \
  -destination generic/platform=iOS \
  -configuration Release \
  build \
  CODE_SIGNING_ALLOWED=NO

echo ""
echo "=== Step 2: Find .app path ==="
# Use showBuildSettings to get the exact BUILT_PRODUCTS_DIR
# This works because xcodebuild already scanned the package in step 1
BUILT_PRODUCTS_DIR=$(xcodebuild -scheme GeminiAirBridge \
  -sdk iphoneos \
  -destination generic/platform=iOS \
  -configuration Release \
  -showBuildSettings 2>/dev/null \
  | grep "^ *BUILT_PRODUCTS_DIR" \
  | awk '{print $NF}')

if [ -z "$BUILT_PRODUCTS_DIR" ]; then
  echo "ERROR: could not determine BUILT_PRODUCTS_DIR" >&2
  # Fallback: search DerivedData
  BUILT_PRODUCTS_DIR=$(find ~/Library/Developer/Xcode/DerivedData \
    -path "*/Release-iphoneos" -type d \
    -name "GeminiAirBridge-*" 2>/dev/null | head -1)/Build/Products/Release-iphoneos
fi

APP_PATH="$BUILT_PRODUCTS_DIR/GeminiAirBridge.app"
echo "BUILT_PRODUCTS_DIR=$BUILT_PRODUCTS_DIR"
echo "APP_PATH=$APP_PATH"

if [ ! -d "$APP_PATH" ]; then
  echo "ERROR: .app not found at $APP_PATH" >&2
  echo "Searching elsewhere..."
  find /Users/runner/Library/Developer/Xcode/DerivedData -name "GeminiAirBridge.app" -maxdepth 6 -type d 2>/dev/null | head -5
  find "$PWD" -name "*.app" -maxdepth 5 -type d 2>/dev/null | head -5
  exit 1
fi

ls -la "$APP_PATH/"

echo ""
echo "=== Step 3: Package IPA ==="
mkdir -p Payload
cp -R "$APP_PATH" Payload/
cd Payload && zip -qr "$IOS_DIR/GeminiAirBridge.ipa" GeminiAirBridge.app/ && cd ..
rm -rf Payload

echo ""
echo "=== Done ==="
ls -lh "$IOS_DIR/GeminiAirBridge.ipa"