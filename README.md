sysohub IoT Lite
A lightweight, scalable IoT platform for Raspberry Pi 3B, featuring a WiFi AP, MQTT broker, VictoriaMetrics, Node-RED, and a Flask-based dashboard.
Prerequisites

Raspberry Pi 3B with Raspberry Pi OS 64-bit Lite (Bookworm).
SD card with at least 8GB.

Setup Instructions

Clone the repository:git clone git@github.com:sysolab/sysohub.git
cd sysohub


Configure config/config.yml (e.g., change name to "greenio").
Flash the SD card:./scripts/flash_image.sh /dev/sdX


Boot the Pi, connect to the WiFi AP (e.g., plantomio_ap), and SSH:ssh pi@192.168.4.1


Run the setup script:sudo python3 /home/HOST_NAME/sysohub/scripts/sysohub.py setup


Access the dashboard at http://192.168.4.1:5000.

Services

WiFi AP: Configured via config.yml.
MQTT Broker: Mosquitto at mqtt://<hostname>:1883.
VictoriaMetrics: Time-series database at http://192.168.4.1:8428.
Node-RED: Flow editor at http://192.168.4.1:1880.
Dashboard: Flask UI at http://192.168.4.1:5000.

Management

Use sysohub.py for setup, backup, update, and status:sudo python3 /home/HOST_NAME/sysohub/scripts/sysohub.py [setup|backup|update|status]


Schedule backups with cron:crontab -e
0 2 * * * /home/HOST_NAME/sysohub/scripts/backup.sh



Scaling

Flash multiple SD cards with flash_image.sh.
Use a prebuilt image for faster deployment.

