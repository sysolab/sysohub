#!/bin/bash

HOME_DIR=$(eval echo \~${SUDO_USER:-$USER}) INSTALL_DIR="$HOME_DIR/sysohub" BACKUP_DIR="$HOME_DIR/backups" TIMESTAMP=$(date +%Y%m%d\_%H%M%S) MAX_BACKUPS=5

mkdir -p "$BACKUP_DIR" BACKUP_FILE="$BACKUP_DIR/iot_backup\_$TIMESTAMP.tar.gz" tar -czf "$BACKUP_FILE" "$INSTALL_DIR" echo "Backup created at $BACKUP_FILE"

# Keep only the latest MAX_BACKUPS

ls -t "$BACKUP_DIR"/iot_backup\_\*.tar.gz | tail -n +$((MAX_BACKUPS + 1)) | xargs -I {} rm -f {}