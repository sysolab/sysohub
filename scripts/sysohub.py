import os
import subprocess
import yaml
import jinja2
import argparse
import shutil
from pathlib import Path

CONFIG_PATH = "/home/pi/iot-lite/config/config.yml"
TEMPLATES_DIR = "/home/pi/iot-lite/templates"
INSTALL_DIR = "/home/pi/iot-lite"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)['project']

def render_template(template_name, dest, context):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template(template_name)
    with open(dest, 'w') as f:
        f.write(template.render(**context))

def run_command(command, check=True):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {command}\n{result.stderr}")
    return result

def setup_wifi_ap(config):
    print("Configuring WiFi AP...")
    run_command("sudo apt update && sudo apt install -y hostapd dnsmasq avahi-daemon")
    run_command("sudo systemctl stop hostapd dnsmasq")

    render_template("dhcpcd.conf.j2", "/etc/dhcpcd.conf", config)
    render_template("hostapd.conf.j2", "/etc/hostapd/hostapd.conf", config)
    render_template("dnsmasq.conf.j2", "/etc/dnsmasq.conf", config)

    run_command('echo "DAEMON_CONF=/etc/hostapd/hostapd.conf" | sudo tee /etc/default/hostapd')
    run_command(f"echo {config['hostname']} | sudo tee /etc/hostname")
    run_command(f"sudo sed -i 's/127.0.0.1.*/127.0.0.1 {config['hostname']}/' /etc/hosts")
    run_command("sudo sysctl -w net.ipv4.ip_forward=1")
    run_command("sudo systemctl enable hostapd dnsmasq avahi-daemon")
    run_command("sudo systemctl start hostapd dnsmasq avahi-daemon")

def install_mosquitto(config):
    print("Installing Mosquitto...")
    run_command("sudo apt install -y mosquitto mosquitto-clients")
    render_template("mosquitto.conf.j2", "/etc/mosquitto/mosquitto.conf", config)
    run_command(f"echo {config['mqtt']['username']}:{config['mqtt']['password']} | sudo tee /etc/mosquitto/passwd")
    run_command("sudo mosquitto_passwd -U /etc/mosquitto/passwd")
    run_command("sudo systemctl enable mosquitto")
    run_command("sudo systemctl start mosquitto")

def install_victoria_metrics(config):
    print("Installing VictoriaMetrics...")
    run_command("wget https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v1.93.0/victoria-metrics-arm-v1.93.0.tar.gz -O /tmp/vm.tar.gz")
    run_command("sudo tar -xzf /tmp/vm.tar.gz -C /usr/local/bin")
    render_template("victoria_metrics.yml.j2", "/etc/victoria-metrics.yml", config)
    run_command("sudo useradd -r victoria-metrics")
    run_command("sudo chown victoria-metrics:victoria-metrics /usr/local/bin/victoria-metrics")
    run_command("sudo mkdir -p /var/lib/victoria-metrics")
    run_command("sudo chown victoria-metrics:victoria-metrics /var/lib/victoria-metrics")
    run_command("sudo bash -c 'cat <<EOF > /etc/systemd/system/victoria-metrics.service\n[Unit]\nDescription=VictoriaMetrics\nAfter=network.target\n\n[Service]\nUser=victoria-metrics\nGroup=victoria-metrics\nExecStart=/usr/local/bin/victoria-metrics --storageDataPath=/var/lib/victoria-metrics --httpListenAddr=:{}\nRestart=always\n\n[Install]\nWantedBy=multi-user.target\nEOF'".format(config['victoria_metrics']['port']))
    run_command("sudo systemctl daemon-reload")
    run_command("sudo systemctl enable victoria-metrics")
    run_command("sudo systemctl start victoria-metrics")

def install_node_red(config):
    print("Installing Node-RED...")
    run_command("sudo apt install -y nodejs npm")
    run_command("sudo npm install -g --unsafe-perm node-red")
    render_template("node_red_settings.js.j2", "/home/pi/.node-red/settings.js", config)
    run_command("sudo systemctl enable nodered")
    run_command("sudo systemctl start nodered")

def install_dashboard(config):
    print("Installing Dashboard...")
    run_command("sudo pip3 install flask python-socketio paho-mqtt requests")
    shutil.copy(f"{TEMPLATES_DIR}/flask_app.py", f"{INSTALL_DIR}/flask_app.py")
    run_command("sudo bash -c 'cat <<EOF > /etc/systemd/system/sysohub-dashboard.service\n[Unit]\nDescription=sysohub Dashboard\nAfter=network.target\n\n[Service]\nUser=pi\nExecStart=/usr/bin/python3 {}/flask_app.py\nRestart=always\n\n[Install]\nWantedBy=multi-user.target\nEOF'".format(INSTALL_DIR))
    run_command("sudo systemctl daemon-reload")
    run_command("sudo systemctl enable sysohub-dashboard")
    run_command("sudo systemctl start sysohub-dashboard")

def backup():
    print("Creating backup...")
    backup_dir = "/home/pi/backups"
    timestamp = run_command("date +%Y%m%d_%H%M%S", check=False).stdout.strip()
    os.makedirs(backup_dir, exist_ok=True)
    run_command(f"tar -czf {backup_dir}/iot_backup_{timestamp}.tar.gz {INSTALL_DIR}")
    print(f"Backup created at {backup_dir}/iot_backup_{timestamp}.tar.gz")

def update():
    print("Updating services...")
    run_command("sudo apt update && sudo apt upgrade -y")
    run_command("sudo npm install -g --unsafe-perm node-red")
    run_command("sudo systemctl restart mosquitto victoria-metrics nodered sysohub-dashboard")

def status():
    print("Service status:")
    for service in ["hostapd", "dnsmasq", "avahi-daemon", "mosquitto", "victoria-metrics", "nodered", "sysohub-dashboard"]:
        run_command(f"systemctl status {service} --no-pager", check=False)

def main():
    parser = argparse.ArgumentParser(description="sysohub IoT Lite Setup and Management")
    parser.add_argument("command", choices=["setup", "backup", "update", "status"], help="Command to execute")
    args = parser.parse_args()

    config = load_config()

    if args.command == "setup":
        setup_wifi_ap(config)
        install_mosquitto(config)
        install_victoria_metrics(config)
        install_node_red(config)
        install_dashboard(config)
        print("Setup complete. Rebooting...")
        run_command("sudo reboot")
    elif args.command == "backup":
        backup()
    elif args.command == "update":
        update()
    elif args.command == "status":
        status()

if __name__ == "__main__":
    main()