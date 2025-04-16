#!/bin/bash

HOME_DIR=$(eval echo \~$USER) INSTALL_DIR="$HOME_DIR/sysohub" BACKUP_DIR="$HOME_DIR/backups" TIMESTAMP=$(date +%Y%m%d\_%H%M%S)

mkdir -p "$BACKUP_DIR" && tar -czf "$BACKUP_DIR/sysohub_backup\_$TIMESTAMP.tar.gz" "$INSTALL_DIR" && echo "Backup created at $BACKUP_DIR/sysohub_backup\_$TIMESTAMP.tar.gz"