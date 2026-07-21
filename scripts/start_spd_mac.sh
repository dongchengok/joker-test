#!/bin/bash
# 在 macOS 上启动 SPD（窗口模式）。
#
# 实测踩坑记录（2026-07-20，macOS 15.1 arm64）：
# 1. 官方 macOS.zip 是 .app，launcher 为 x86_64，classpath 里 arm64 natives 排在前面
#    会让 LWJGL 加载错误 dylib（glfwGetMonitorPos NPE）。用 arm64 JDK + 只保留
#    macos-arm64 natives 可正常运行。
# 2. SPD 默认全屏；全屏窗口独占一个 Space，切走后截图/点击都投递不到。
#    启动前在 settings.xml 写 fullscreen=false 强制窗口模式。
# 3. GLFW 在 macOS 必须 -XstartOnFirstThread。
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$REPO/.test-targets/SPD-mac/Shattered Pixel Dungeon.app/Contents/app"
SETTINGS="$HOME/Library/Application Support/Shattered Pixel Dungeon/settings.xml"
JDK_BIN="$(echo "$REPO"/.test-targets/jdk/*/Contents/Home/bin)"

# 强制窗口模式（全屏窗口独占 Space，自动化无法点击）
mkdir -p "$(dirname "$SETTINGS")"
if [ -f "$SETTINGS" ]; then
  grep -q '"fullscreen"' "$SETTINGS" || sed -i '' \
    's|<entry key="version">|<entry key="fullscreen">false</entry>\n<entry key="version">|' \
    "$SETTINGS"
else
  cat > "$SETTINGS" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<properties>
<entry key="fullscreen">false</entry>
</properties>
XML
fi

# classpath 只保留 arm64 mac natives（x64 natives 会让 LWJGL 加载错误 dylib）
CP=$(ls "$APP_DIR"/*.jar | grep -v "natives-linux\|natives-windows\|natives-macos.jar" | tr '\n' ':')
exec "$JDK_BIN/java" -XstartOnFirstThread -cp "$CP" \
  com.shatteredpixel.shatteredpixeldungeon.desktop.DesktopLauncher
