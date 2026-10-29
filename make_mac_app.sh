#!/bin/bash
# Creates "Amazon Scanner.app" in /Applications that starts the scanner without a Terminal window.
# Keep this folder outside Downloads/Desktop/Documents: macOS blocks apps from reading those.
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Amazon Scanner.app"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/MacOS/run" <<EOF
#!/bin/bash
cd "$DIR" || exit 1
[ -d .venv ] || { open "Start Scanner.command"; exit 0; }  # first-time setup needs Terminal
if curl -s http://127.0.0.1:8791/api/status | grep -q phase; then
  open http://127.0.0.1:8791
  exit 0
fi
# Stay in the foreground: macOS stops an app's background children when the app exits.
exec .venv/bin/python app.py > scanner.log 2>&1
EOF
cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>run</string>
  <key>CFBundleName</key><string>Amazon Scanner</string>
  <key>CFBundleIdentifier</key><string>local.amazon-store-scanner</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict></plist>
EOF
chmod +x "$APP/Contents/MacOS/run"
echo "Created $APP"
