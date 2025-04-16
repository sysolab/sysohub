#!/bin/bash

BACKUP_DIR="/home/plantomioX1/backups" INSTALL_DIR="/home/plantomioX1/sysohub" TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR && tar -czf $BACKUP_DIR/iot_backup_$TIMESTAMP.tar.gz $INSTALL_DIR && echo "Backup created at $BACKUP_DIR/iot_backup_$TIMESTAMP.tar.gz"