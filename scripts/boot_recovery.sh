#!/usr/bin/env bash
# ==============================================================================
# boot_recovery.sh - Tự động phục hồi Smart Home sau khi mất điện
#
# Nguyên nhân cần script:
#   Khi có điện lại, Server máy tính khởi động nhanh hơn Modem mạng (1-2 phút).
#   Home Assistant chạy trước khi có Internet/DNS dẫn tới Tuya và Sonoff bị lỗi
#   kết nối và không tải được camera.
#
# Hoạt động:
#   1. Chờ Modem kết nối Internet và DNS phân giải thông suốt.
#   2. Kiểm tra trạng thái Tuya Camera Stream trong Home Assistant.
#   3. Nếu HA bị khởi động hụt (404) -> Tự động restart HA và Frigate.
# ==============================================================================

set -uo pipefail

LOG_FILE="/tmp/boot_recovery.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== Khởi động tiến trình Boot Recovery sau khi bật máy ==="

# 1. Chờ kết nối Internet và DNS (Tối đa 300 giây / 5 phút)
MAX_WAIT_SECONDS=300
WAIT_COUNT=0
INTERNET_OK=false

log "Đang kiểm tra kết nối Internet và DNS từ Modem nhà mạng..."
while [ "$WAIT_COUNT" -lt "$MAX_WAIT_SECONDS" ]; do
    if ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 && host -W 2 apigw-sg.iotbing.com >/dev/null 2>&1; then
        INTERNET_OK=true
        log "Internet và DNS đã thông suốt sau ${WAIT_COUNT}s!"
        break
    fi
    sleep 5
    WAIT_COUNT=$(( WAIT_COUNT + 5 ))
done

if [ "$INTERNET_OK" != "true" ]; then
    log "CẢNH BÁO: Quá 5 phút vẫn chưa có Internet từ modem. Tiếp tục thử sau..."
fi

# Chờ thêm 10 giây để Home Assistant container sẵn sàng
sleep 10

# 2. Kiểm tra xem Home Assistant có tải được Camera Tuya không
CHECK_COUNT=0
while [ "$CHECK_COUNT" -lt 6 ]; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://127.0.0.1:8123/api/tuya_stream/phong_khach" || echo "000")
    log "Kiểm tra Tuya Camera API: HTTP Code $HTTP_CODE"

    if [ "$HTTP_CODE" = "200" ]; then
        log "Home Assistant và 6 Camera Tuya đang hoạt động bình thường! Không cần restart."
        exit 0
    elif [ "$HTTP_CODE" = "404" ] || [ "$HTTP_CODE" = "500" ]; then
        log "Phát hiện Home Assistant khởi động trước khi có mạng (Lỗi $HTTP_CODE)!"
        log "Đang tự động khởi động lại Home Assistant..."
        docker restart homeassistant
        sleep 20
        log "Đang khởi động lại Frigate NVR để kết nối lại 6 luồng camera..."
        docker restart frigate
        log "Phục hồi hệ thống hoàn tất!"
        exit 0
    fi

    # Nếu HA chưa lên kịp (000 hoặc mã khác), chờ 10s rồi kiểm tra lại
    sleep 10
    CHECK_COUNT=$(( CHECK_COUNT + 1 ))
done

log "Hoàn tất kiểm tra boot recovery."
