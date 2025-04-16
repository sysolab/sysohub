import os
import subprocess
import yaml
import jinja2
import argparse
import shutil
import getpass
from pathlib import Path

# Determine the invoking user's home directory
def get_user_home():
    if 'SUDO_USER' in os.environ and os.environ['SUDO_USER']:
        # When running with sudo, use SUDO_USER's home directory
        return os.path.expanduser(f"~{os.environ['SUDO_USER']}")
    else:
        # Otherwise, use the current user's home directory
        return os.path.expanduser("~")

# Check if running as root
def check_root():
    if os.geteuid() != 0:
        raise PermissionError("This script must be run with sudo (e.g., 'sudo python3 sysohub.py setup')")

HOME_DIR = get_user_home()
INSTALL_DIR = os.path.join(HOME_DIR, "sysohub")
CONFIG_PATH = os.path.join(INSTALL_DIR, "config", "config.yml")
TEMPLATES_DIR = os.path.join(INSTALL_DIR, "templates")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)['project']

def render_template(template_name, dest, context):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template(template_name)
    with open(dest, 'w') as f:
        f.write(template.render(**context))

def run_command(command, check=True, ignore_errors=False):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0 and not ignore_errors:
        raise Exception(f"Command failed: {command}\n{result.stderr}")
    return result

def setup_wifi_ap(config):
    print("Configuring WiFi AP...")
    run_command("sudo apt update && sudo apt install -y hostapd dnsmasq avahi-daemon")
    run_command("sudo systemctl stop hostapd dnsmasq", ignore_errors=True)

    # Unmask services to ensure they can be enabled
    for service in ["hostapd", "dnsmasq", "avahi-daemon"]:
        run_command(f"sudo systemctl unmask {service}", ignore_errors=True)

    render_template("dhcpcd.conf.j2", "/etc/dhcpcd.conf", config)
    render_template("hostapd.conf.j2", "/etc/hostapd/hostapd.conf", config)
    render_template("dnsmasq.conf.j2", "/etc/dnsmasq.conf", config)

    run_command('echo "DAEMON_CONF=/etc/hostapd/hostapd.conf" | sudo tee /etc/default/hostapd')
    run_command(f"echo {config['hostname']} | sudo tee /etc/hostname")
    run_command(f"sudo sed -i 's/127.0.0.1.*/127.0.0.1 {config['hostname']}/' /etc/hosts")
    run_command("sudo sysctl -w net.ipv4.ip_forward=1")

    # Enable and start services, ignoring errors if already enabled
    for service in ["hostapd", "dnsmasq", "avahi-daemon"]:
        run_command(f"sudo systemctl enable {service}", ignore_errors=True)
        run_command(f"sudo systemctl start {service}", ignore_errors=True)

def install_mosquitto(config):
    print("Installing Mosquitto...")
    run_command("sudo apt install -y mosquitto mosquitto-clients")
    render_template("mosquitto.conf.j2", "/etc/mosquitto/mosquitto.conf", config)
    run_command(f"echo {config['mqtt']['username']}:{config['mqtt']['password']} | sudo tee /etc/mosquitto/passwd")
    run_command("sudo mosquitto_passwd -U /etc/mosquitto/passwd")
    run_command("sudo systemctl enable mosquitto", ignore_errors=True)
    run_command("sudo systemctl start mosquitto", ignore_errors=True)

def install_victoria_metrics(config):
    print("Installing VictoriaMetrics...")
    run_command("wget https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v1.115.0/victoria-metrics-darwin-arm64-v1.115.0.tar.gz -O /tmp/vm.tar.gz")
    run_command("sudo tar -xzf /tmp/vm.tar.gz -C /usr/local/bin")
    render_template("victoria_metrics.yml.j2", "/etc/victoria-metrics.yml", config)
    run_command("sudo useradd -r victoria-metrics", ignore_errors=True)
    run_command("sudo chown victoria-metrics:victoria-metrics /usr/local/bin/victoria-metrics")
    run_command("sudo mkdir -p /var/lib/victoria-metrics")
    run_command("sudo chown victoria-metrics:victoria-metrics /var/lib/victoria-metrics")
    run_command("sudo bash -c 'cat <<EOF > /etc/systemd/system/victoria-metrics.service\n[Unit]\nDescription=VictoriaMetrics\nAfter=network.target\n\n[Service]\nUser=victoria-metrics\nGroup=victoria-metrics\nExecStart=/usr/local/bin/victoria-metrics --storageDataPath=/var/lib/victoria-metrics --httpListenAddr=:{}\nRestart=always\n\n[Install]\nWantedBy=multi-user.target\nEOF'".format(config['victoria_metrics']['port']))
    run_command("sudo systemctl daemon-reload")
    run_command("sudo systemctl enable victoria-metrics", ignore_errors=True)
    run_command("sudo systemctl start victoria-metrics", ignore_errors=True)

def install_node_red(config):
    print("Installing Node-RED...")
    run_command("sudo apt install -y nodejs npm")
    run_command("sudo npm install -g --unsafe-perm node-red")
    node_red_dir = os.path.join(HOME_DIR, ".node-red")
    os.makedirs(node_red_dir, exist_ok=True)
    render_template("node_red_settings.js.j2", os.path.join(node_red_dir, "settings.js"), config)
    run_command(f"sudo bash -c 'cat <<EOF > /etc/systemd/system/nodered.service\n[Unit]\nDescription=Node-RED\nAfter=network.target\n\n[Service]\nUser={os.getlogin()}\nExecStart=/usr/bin/node-red\nWorkingDirectory={node_red_dir}\nRestart=always\n\n[Install]\nWantedBy=multi-user.target\nEOF'")
    run_command("sudo systemctl daemon-reload")
    run_command("sudo systemctl enable nodered", ignore_errors=True)
    run_command("sudo systemctl start nodered", ignore_errors=True)

def install_dashboard(config):
    print("Installing Dashboard...")
    run_command("sudo pip3 install flask python-socketio paho-mqtt requests")
    shutil.copy(f"{TEMPLATES_DIR}/flask_app.py", f"{INSTALL_DIR}/flask_app.py")
    run_command(f"sudo bash -c 'cat <<EOF > /etc/systemd/system/sysohub-dashboard.service\n[Unit]\nDescription=sysohub Dashboard\nAfter=network.target\n\n[Service]\nUser={os.getlogin()}\nExecStart=/usr/bin/python3 {INSTALL_DIR}/flask_app.py\nRestart=always\n\n[Install]\nWantedBy=multi-user.target\nEOF'")
    run_command("sudo systemctl daemon-reload")
    run_command("sudo systemctl enable sysohub-dashboard", ignore_errors=True)
    run_command("sudo systemctl start sysohub-dashboard", ignore_errors=True)

def backup():
    print("Creating backup...")
    backup_dir = os.path.join(HOME_DIR, "backups")
    timestamp = run_command("date +%Y%m%d_%H%M%S", check=False).stdout.strip()
    os.makedirs(backup_dir, exist_ok=True)
    run_command(f"tar -czf {backup_dir}/iot_backup_{timestamp}.tar.gz {INSTALL_DIR}")
    print(f"Backup created at {backup_dir}/iot_backup_{timestamp}.tar.gz")

def update():
    print("Updating services...")
    run_command("sudo apt update && sudo apt upgrade -y")
    run_command("sudo npm install -g --unsafe-perm node-red")
    run_command("sudo pip3 install --upgrade flask python-socketio paho-mqtt requests")
    run_command("sudo systemctl restart mosquitto victoria-metrics nodered sysohub-dashboard", ignore_errors=True)

def status():
    print("Service status:")
    for service in ["hostapd", "dnsmasq", "avahi-daemon", "mosquitto", "victoria-metrics", "nodered", "sysohub-dashboard"]:
        run_command(f"systemctl status {service} --no-pager", check=False)

def main():
    # Ensure the script is run with sudo
    check_root()

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