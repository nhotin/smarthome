#!/usr/bin/env bash
# =============================================================================
# tuya_stream.sh - Bridge Tuya RTSP → go2rtc cho Frigate NVR
#
# Cách dùng: /scripts/tuya_stream.sh <camera_name> [output_rtsp_url]
#   camera_name: phong_khach | san_truoc | san_sau | cong | bep | ban_cong
#   output_rtsp_url: URL go2rtc sẽ truyền qua biến {{output}} (tự động)
#
# Logic:
#   1. Gọi HA API lấy RTSP URL tươi từ Tuya Cloud
#   2. ffmpeg stream-copy (không re-encode) sang go2rtc output
#   3. Khi stream đứt → tự động retry sau 2s, lấy URL mới
# =============================================================================

set -uo pipefail

CAMERA_NAME="$1"
OUTPUT_URL="${2:-}"
HA_API="http://127.0.0.1:8123/api/tuya_stream"
RETRY_DELAY=2         # giây chờ sau khi stream đứt
ERROR_DELAY=5         # giây chờ khi lấy URL thất bại

# Tìm ffmpeg binary
find_ffmpeg() {
    for path in \
        "/usr/lib/ffmpeg/8.0/bin/ffmpeg" \
        "/usr/lib/ffmpeg/7.0/bin/ffmpeg" \
        "/usr/local/bin/ffmpeg" \
        "/usr/bin/ffmpeg"; do
        [ -x "$path" ] && echo "$path" && return
    done
    command -v ffmpeg 2>/dev/null
}

FFMPEG="$(find_ffmpeg)"
if [ -z "$FFMPEG" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] LỖI: Không tìm thấy ffmpeg!" >&2
    exit 1
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$CAMERA_NAME] $*"
}

log "Khởi động stream bridge (ffmpeg: $FFMPEG)"

while true; do
    log "Đang lấy RTSP URL từ HA..."
    URL=$(curl -s --connect-timeout 5 --max-time 15 "$HA_API/$CAMERA_NAME")

    if [[ "$URL" =~ ^rtsps?:// ]]; then
        log "Bắt đầu stream → $OUTPUT_URL"
        "$FFMPEG" -hide_banner -loglevel warning \
            -rtsp_transport tcp \
            -i "$URL" \
            -c copy \
            -f rtsp "${OUTPUT_URL}"
        EXIT_CODE=$?
        log "Stream kết thúc (exit=$EXIT_CODE). Thử lại sau ${RETRY_DELAY}s..."
    else
        log "Không lấy được URL (response: '$URL'). Thử lại sau ${ERROR_DELAY}s..."
        sleep "$ERROR_DELAY"
        continue
    fi

    sleep "$RETRY_DELAY"
done
