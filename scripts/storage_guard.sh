#!/usr/bin/env bash
# ==============================================================================
# storage_guard.sh - Giám sát và duy trì vùng đệm an toàn cho ổ cứng Frigate
#
# Chức năng:
#   - Kiểm tra dung lượng trống của ổ cứng lưu trữ camera (/mnt/hdd_1tb_seagate)
#   - Khi dung lượng trống xuống dưới MIN_FREE_GB (mặc định 20 GB):
#     Tự động dọn dẹp các thư mục ghi hình cũ nhất (FIFO) để bảo vệ ổ cứng
#     và cơ sở dữ liệu SQLite luôn chạy mượt mà, không bao giờ bị nghẽn I/O.
# ==============================================================================

set -euo pipefail

TARGET_MOUNT="/mnt/hdd_1tb_seagate"
RECORDINGS_DIR="${TARGET_MOUNT}/camera_recordings/recordings"
MIN_FREE_GB=20           # Ngưỡng dung lượng trống an toàn tối thiểu (GB)
LOG_FILE="/tmp/storage_guard.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

if [ ! -d "$RECORDINGS_DIR" ]; then
    exit 0
fi

# Lấy dung lượng còn trống (GB)
FREE_KB=$(df -k "$TARGET_MOUNT" | awk 'NR==2 {print $4}')
FREE_GB=$(( FREE_KB / 1024 / 1024 ))

log "Kiểm tra dung lượng ổ: Còn trống ${FREE_GB} GB (Ngưỡng an toàn: ${MIN_FREE_GB} GB)"

if [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
    log "CẢNH BÁO: Dung lượng trống (${FREE_GB} GB) < ngưỡng an toàn (${MIN_FREE_GB} GB)!"
    log "Bắt đầu dọn dẹp cuốn chiếu ngày ghi hình cũ nhất..."

    # Tìm ngày cũ nhất trong thư mục recordings
    OLDEST_DATE_DIR=$(find "$RECORDINGS_DIR" -mindepth 1 -maxdepth 1 -type d | sort | head -n 1)

    if [ -n "$OLDEST_DATE_DIR" ] && [ -d "$OLDEST_DATE_DIR" ]; then
        log "Đang xóa thư mục ngày cũ nhất: $OLDEST_DATE_DIR"
        rm -rf "$OLDEST_DATE_DIR"
        log "Đã giải phóng thành công ngày cũ nhất."
    fi
fi
