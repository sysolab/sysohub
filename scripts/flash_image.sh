#!/bin/bash

IMAGE="raspios-lite-arm64.img"
DEVICE="$1"
CONFIG_DIR="config"

if [ -z "$DEVICE" ]; then
    echo "Usage: $0 <device> (e.g., /dev/sdX)"
    exit 1
fi

echo "Flashing $IMAGE to $DEVICE..."
sudo dd if=$IMAGE of=$DEVICE bs=4M status=progress
sudo sync

echo "Mounting boot partition..."
mkdir -p /mnt/boot
sudo mount ${DEVICE}1 /mnt/boot

echo "Enabling SSH..."
sudo touch /mnt/boot/ssh

echo "Creating empty wpa_supplicant.conf..."
sudo touch /mnt/boot/wpa_supplicant.conf

echo "Copying config.yml..."
sudo cp $CONFIG_DIR/config.yml /mnt/boot/config.yml

echo "Unmounting..."
sudo umount /mnt/boot
rmdir /mnt/boot

echo "Done."