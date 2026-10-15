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

# Lấy RTSP URL tươi từ Home Assistant (thử tối đa 3 lần)
URL=""
for ((i=1; i<=3; i++)); do
    URL=$(curl -s --connect-timeout 4 --max-time 8 "$HA_API/$CAMERA_NAME")
    if [[ "$URL" =~ ^rtsps?:// ]]; then
        break
    fi
    log "Thử lấy URL lần $i/3 thất bại (response: '$URL'). Đợi 2s..."
    sleep 2
done

if [[ ! "$URL" =~ ^rtsps?:// ]]; then
    log "LỖI: Không lấy được RTSP URL hợp lệ sau 3 lần thử. Thoát để go2rtc quản lý retry."
    exit 1
fi

log "Bắt đầu stream → $OUTPUT_URL"
# Dùng exec để ffmpeg thay thế tiến trình shell.
# Khi luồng cloud ngắt, tiến trình thoát ngay lập tức để go2rtc nhận diện ngắt kết nối
# và tự khởi động lại luồng mới trong <1s (thay vì bị treo timeout 30s).
# Cờ -avoid_negative_ts và -fflags +genpts+discardcorrupt giúp ổn định timestamp, tránh bị Frigate discard segment.
exec "$FFMPEG" -hide_banner -loglevel warning \
    -avoid_negative_ts make_zero \
    -fflags +genpts+discardcorrupt \
    -rtsp_transport tcp \
    -i "$URL" \
    -c copy \
    -rtsp_transport tcp \
    -f rtsp "${OUTPUT_URL}"

