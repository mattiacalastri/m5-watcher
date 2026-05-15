#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# 🐙 M5 Watcher — Ghostty Bundle Rebrand v2 CANONICAL (sess.1988)
#
# Clona /Applications/Ghostty.app in ~/Applications/M5 Watcher.app con identità
# Polpo (CFBundleName=M5 Watcher, CFBundleIdentifier=com.polpo.m5-watcher,
# icona M5) + Mach-O launcher che inietta --title/--fullscreen/--command=run.sh.
#
# Risultato: Dock + menu bar mostrano "M5 Watcher" invece di "Ghostty".
# Singola finestra fullscreen, niente security prompt.
#
# v2 (sess.1988 finale, post-debug):
#   - Mach-O launcher compilato in C (bash wrapper rifiutato POSIX 162)
#   - Re-sign bottom-up esplicito (codesign --deep insufficient su Sparkle)
#   - Entitlement disable-library-validation (Sparkle Team ID mismatch)
#   - --command= invece di -e (no security prompt, no window split)
#   - xattr -cr (rimuove quarantine ereditato da cp -R)
#
# Vault doctrine: ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/
#   Astra Digital Marketing/Patterns/macOS .app Rebrand — Mach-O Wrapper
#   Canonical Sess.1988.md
#
# ROLLBACK:
#   rm -rf "$HOME/Applications/M5 Watcher.app"
#   mv "$HOME/Applications/M5 Watcher.app.applet.bak.sess1988" \
#      "$HOME/Applications/M5 Watcher.app"
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SRC="/Applications/Ghostty.app"
DST="$HOME/Applications/M5 Watcher.app"
BAK="$HOME/Applications/M5 Watcher.app.applet.bak.sess1988"
RUN_SCRIPT="$HOME/projects/m5-watcher/run.sh"

REBRAND_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER_SRC="$REBRAND_DIR/m5_launcher.c"
ENT_FILE="$REBRAND_DIR/entitlements_minimal.plist"
PB="/usr/libexec/PlistBuddy"

echo "─── Pre-flight ───"
[ -d "$SRC" ] || { echo "ERROR: $SRC mancante"; exit 1; }
[ -x "$RUN_SCRIPT" ] || { echo "ERROR: $RUN_SCRIPT mancante/non eseguibile"; exit 1; }
[ -f "$LAUNCHER_SRC" ] || { echo "ERROR: $LAUNCHER_SRC mancante"; exit 1; }
[ -f "$ENT_FILE" ] || { echo "ERROR: $ENT_FILE mancante"; exit 1; }
which clang >/dev/null || { echo "ERROR: clang non installato (xcode-select --install)"; exit 1; }

echo "─── Step 0: kill running instances ───"
pkill -f "m5-watcher/app.py" 2>/dev/null || true
osascript -e 'tell application "M5 Watcher" to quit' 2>/dev/null || true
osascript -e 'tell application "Ghostty" to quit' 2>/dev/null || true
sleep 2

echo "─── Step 1: backup applet attuale ───"
if [ -d "$DST" ] && [ ! -d "$BAK" ]; then
  mv "$DST" "$BAK"
  echo "  → BACKUP_OK: $BAK"
elif [ -d "$BAK" ]; then
  echo "  → backup già presente: $BAK (riuso)"
  [ -d "$DST" ] && rm -rf "$DST"
fi

ICON_SRC="$BAK/Contents/Resources/applet.icns"
[ -f "$ICON_SRC" ] || { echo "ERROR: icona M5 non trovata in $ICON_SRC"; exit 1; }

echo "─── Step 2: clone Ghostty.app → M5 Watcher.app ───"
cp -R "$SRC" "$DST"

echo "─── Step 3: inject icona M5 + Info.plist edit ───"
cp "$ICON_SRC" "$DST/Contents/Resources/m5_watcher.icns"
PLIST="$DST/Contents/Info.plist"
$PB -c "Set :CFBundleName 'M5 Watcher'" "$PLIST"
if $PB -c "Print :CFBundleDisplayName" "$PLIST" &>/dev/null; then
  $PB -c "Set :CFBundleDisplayName 'M5 Watcher'" "$PLIST"
else
  $PB -c "Add :CFBundleDisplayName string 'M5 Watcher'" "$PLIST"
fi
$PB -c "Set :CFBundleIdentifier com.polpo.m5-watcher" "$PLIST"
if $PB -c "Print :CFBundleIconFile" "$PLIST" &>/dev/null; then
  $PB -c "Set :CFBundleIconFile m5_watcher" "$PLIST"
else
  $PB -c "Add :CFBundleIconFile string m5_watcher" "$PLIST"
fi
$PB -c "Delete :CFBundleIconName" "$PLIST" 2>/dev/null || true

echo "─── Step 4: compile Mach-O launcher (universal) ───"
clang -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 \
  -o /tmp/m5_watcher_launcher \
  "$LAUNCHER_SRC"
file /tmp/m5_watcher_launcher | head -1

echo "─── Step 5: install launcher come CFBundleExecutable ───"
mv "$DST/Contents/MacOS/ghostty" "$DST/Contents/MacOS/ghostty.bin"
cp /tmp/m5_watcher_launcher "$DST/Contents/MacOS/ghostty"
chmod +x "$DST/Contents/MacOS/ghostty"

echo "─── Step 6: rimuovi vecchia signature ───"
rm -rf "$DST/Contents/_CodeSignature"

echo "─── Step 7: re-sign BOTTOM-UP (XPC → app → plugin → framework → dylib) ───"
find "$DST" -name "*.xpc" -depth -exec codesign --force --sign - --options runtime {} \;
find "$DST" -name "*.app" -depth -not -path "$DST" -exec codesign --force --sign - --options runtime {} \;
find "$DST" -name "*.plugin" -depth -exec codesign --force --sign - --options runtime {} \;
find "$DST" -name "*.framework" -depth -exec codesign --force --sign - --options runtime {} \;
find "$DST" -name "*.dylib" -exec codesign --force --sign - --options runtime {} \;

echo "─── Step 8: sign main bin + launcher con entitlements ───"
codesign --force --sign - --options runtime \
  --entitlements "$ENT_FILE" \
  "$DST/Contents/MacOS/ghostty.bin"
codesign --force --sign - --options runtime \
  --entitlements "$ENT_FILE" \
  "$DST/Contents/MacOS/ghostty"

echo "─── Step 9: sign outer bundle ───"
codesign --force --sign - --options runtime "$DST"

echo "─── Step 10: verify + xattr cleanup + LS refresh ───"
codesign --verify --deep --strict "$DST" && echo "  → CODESIGN_VALID"
xattr -cr "$DST"
touch "$DST"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$DST" 2>/dev/null || true

echo ""
echo "✅ DONE — verifica:"
echo ""
echo "Bundle identity:"
$PB -c "Print :CFBundleName" "$PLIST"
$PB -c "Print :CFBundleIdentifier" "$PLIST"
$PB -c "Print :CFBundleIconFile" "$PLIST"
echo ""
codesign -dv "$DST" 2>&1 | grep -E "Identifier|Format|Signature" | head -3
echo ""
echo "Test launch:"
echo "  open \"$DST\""
echo ""
echo "Verify post-launch:"
echo "  lsappinfo info -only name,displayname,bundleid,bundlepath -app com.polpo.m5-watcher"
echo "  ps -axo pid,command | grep \"M5 Watcher.app/Contents/MacOS\""
