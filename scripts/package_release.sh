#!/bin/sh
# Build a shareable macOS release.
#
#   make release
#
# Produces dist/Japanese Live Translator <version>.dmg containing the app and
# a shortcut to /Applications.
#
# The app is ad-hoc signed, not Developer ID signed and not notarized, because
# that needs a paid Apple account. The practical consequence is in
# docs/sharing.md: whoever receives this has to clear the quarantine flag
# themselves. There is no way around that without an Apple Developer ID.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE="$ROOT/packages/desktop/src-tauri/target/release/bundle"
APP="$BUNDLE/macos/Japanese Live Translator.app"
ENTITLEMENTS="$ROOT/packages/desktop/src-tauri/entitlements.plist"
VERSION="$(python3 -c "import json;print(json.load(open('$ROOT/packages/desktop/src-tauri/tauri.conf.json'))['version'])")"
OUT="$ROOT/dist"
DMG="$OUT/Japanese Live Translator $VERSION.dmg"

[ -d "$APP" ] || { echo "No app bundle. Run 'make bundle' first."; exit 1; }

# Sign the whole tree, including the backend and capture helper in Resources.
# Without this the nested binaries keep their own signatures, the bundle is
# internally inconsistent, and another Mac reports the app as damaged.
echo "Signing (ad-hoc)…"
codesign --force --deep --sign - --options runtime --entitlements "$ENTITLEMENTS" "$APP"
codesign --verify --deep --strict "$APP"

echo "Building the disk image…"
rm -rf "$OUT" "${TMPDIR:-/tmp}/jlt-dmg"
mkdir -p "$OUT" "${TMPDIR:-/tmp}/jlt-dmg"
cp -R "$APP" "${TMPDIR:-/tmp}/jlt-dmg/"
ln -s /Applications "${TMPDIR:-/tmp}/jlt-dmg/Applications"
cp "$ROOT/docs/sharing.md" "${TMPDIR:-/tmp}/jlt-dmg/READ ME FIRST.md"

hdiutil create -volname "Japanese Live Translator" \
  -srcfolder "${TMPDIR:-/tmp}/jlt-dmg" -ov -format UDZO "$DMG" >/dev/null
rm -rf "${TMPDIR:-/tmp}/jlt-dmg"

echo
echo "Created: $DMG"
echo "Size:    $(du -h "$DMG" | cut -f1)"
echo
echo "Before sharing, read docs/sharing.md: the app is unsigned, so the"
echo "recipient must clear the quarantine flag or macOS will refuse to open it."
