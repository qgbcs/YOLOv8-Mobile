#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

BUILD_TYPE="${1:-debug}"

case "$BUILD_TYPE" in
    release|--release|-r)
        GRADLE_TASK=":app:assembleRelease"
        OUT_DIR="$PWD/app/build/outputs/apk/release"
        ;;
    debug|--debug|-d)
        GRADLE_TASK=":app:assembleDebug"
        OUT_DIR="$PWD/app/build/outputs/apk/debug"
        ;;
    --help|-h|help)
        echo "Usage: $0 [debug|release]"
        exit 0
        ;;
    *)
        echo "未知参数: $BUILD_TYPE"
        echo "Usage: $0 [debug|release]"
        exit 1
        ;;
esac

if [ -z "${ANDROID_HOME:-}" ]; then
    if [ -d "/home/vscode/.buildozer/android/platform/android-sdk" ]; then
        export ANDROID_HOME="/home/vscode/.buildozer/android/platform/android-sdk"
    elif [ -d "/workspaces/kivy/.buildozer/android/platform/android-sdk" ]; then
        export ANDROID_HOME="/workspaces/kivy/.buildozer/android/platform/android-sdk"
    fi
fi

if [ -z "${ANDROID_SDK_ROOT:-}" ] && [ -n "${ANDROID_HOME:-}" ]; then
    export ANDROID_SDK_ROOT="$ANDROID_HOME"
fi

if [ -z "${JAVA_HOME:-}" ]; then
    if [ -d "/usr/lib/jvm/java-17-openjdk-amd64" ]; then
        export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"
    fi
fi

if [ -n "${ANDROID_HOME:-}" ]; then
    export PATH="$ANDROID_HOME/platform-tools:$PATH"
fi

if [ -x "$PWD/gradlew" ]; then
    GRADLE_CMD="$PWD/gradlew"
elif command -v gradle >/dev/null 2>&1; then
    GRADLE_CMD="gradle"
else
    echo "未找到可用的 Gradle。"
    exit 1
fi

echo "使用 Android SDK: ${ANDROID_HOME:-未设置}"
echo "使用 Java: ${JAVA_HOME:-未设置}"
echo "构建类型: $BUILD_TYPE"
"$GRADLE_CMD" "$GRADLE_TASK"

if [ "$BUILD_TYPE" = "release" ]; then
    echo "开始签名 release APK..."

    UNSIGNED_APK=""
    if [ -f "$OUT_DIR/app-release-unsigned.apk" ]; then
        UNSIGNED_APK="$OUT_DIR/app-release-unsigned.apk"
    else
        UNSIGNED_APK="$(find "$OUT_DIR" -maxdepth 1 -type f -name '*-unsigned.apk' | sort | head -n 1)"
    fi

    if [ -n "$UNSIGNED_APK" ]; then
        python3 "$PWD/apk_sign.py" "$UNSIGNED_APK"

        TMP_DIR="$PWD/app/build/tmp"
        mkdir -p "$TMP_DIR"
        mv "$UNSIGNED_APK" "$TMP_DIR/$(basename "$UNSIGNED_APK")"
        echo "已将未签名 APK 移动到: $TMP_DIR/$(basename "$UNSIGNED_APK")"
    else
        echo "未找到未签名 APK，跳过签名和移动。"
    fi
fi

APK_PATH="$(find "$OUT_DIR" -maxdepth 1 -type f -name '*.apk' | sort | head -n 1)"

if [ -n "$APK_PATH" ]; then
    echo "APK 输出: $APK_PATH"
else
    echo "未生成 APK" >&2
    exit 1
fi
