import os
import subprocess
import yaml
import jinja2
import argparse
import shutil
import tempfile
import hashlib
from pathlib import Path

# Determine the invoking user's home directory
def get_user_home():
    if 'SUDO_USER' in os.environ and os.environ['SUDO_USER']:
        return os.path.expanduser(f"~{os.environ['SUDO_USER']}")
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

def run_command(command, check=True, ignore_errors=False):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0 and not ignore_errors:
        raise Exception(f"Command failed: {command}\n{result.stderr}")
    return result

def is_package_installed(package):
    result = run_command(f"dpkg -l | grep {package}", check=False)
    return result.returncode == 0

def is_service_enabled(service):
    result = run_command(f"systemctl is-enabled {service}", check=False)
    return result.stdout.strip() == "enabled"

def is_service_running(service):
    result = run_command(f"systemctl is-active {service}", check=False)
    return result.stdout.strip() == "active"

def file_hash(file_path):
    if not os.path.exists(file_path):
        return None
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def render_template(template_name, dest, context, temp_dir):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template(template_name)
    temp_file = os.path.join(temp_dir, os.path.basename(dest))
    with open(temp_file, 'w') as f:
        f.write(template.render(**context))
    return temp_file

def update_file_if_changed(template_name, dest, context, temp_dir):
    temp_file = render_template(template_name, dest, context, temp_dir)
    temp_hash = file_hash(temp_file)
    dest_hash = file_hash(dest)
    if temp_hash != dest_hash:
        print(f"Updating {dest}...")
        run_command(f"sudo mv {temp_file} {dest}")
        run_command(f"sudo chown root:root {dest}")
        run_command(f"sudo chmod 644 {dest}")
    else:
        print(f"{dest} is up-to-date, skipping.")
    return temp_hash != dest_hash

def setup_wifi_ap(config, temp_dir):
    print("Configuring WiFi AP...")
    packages = ["hostapd", "dnsmasq", "avahi-daemon"]
    for pkg in packages:
        if is_package_installed(pkg):
            print(f"{pkg} is already installed, skipping.")
        else:
            run_command(f"sudo apt update && sudo apt install -y {pkg}")

    services = ["hostapd", "dnsmasq", "avahi-daemon"]
    for service in services:
        run_command(f"sudo systemctl unmask {service}", ignore_errors=True)
        run_command(f"sudo systemctl stop {service}", ignore_errors=True)

    configs = [
        ("dhcpcd.conf.j2", "/etc/dhcpcd.conf"),
        ("hostapd.conf.j2", "/etc/hostapd/hostapd.conf"),
        ("dnsmasq.conf.j2", "/etc/dnsmasq.conf")
    ]
    configs_changed = False
    for template, dest in configs:
        if update_file_if_changed(template, dest, config, temp_dir):
            configs_changed = True

    default_hostapd = "/etc/default/hostapd"
    default_content = 'DAEMON_CONF="/etc/hostapd/hostapd.conf"'
    if file_hash(default_hostapd) != hashlib.sha256(default_content.encode()).hexdigest():
        print("Updating /etc/default/hostapd...")
        run_command(f"echo '{default_content}' | sudo tee {default_hostapd}")

    hostname = config['hostname']
    if run_command("cat /etc/hostname", check=False).stdout.strip() != hostname:
        print("Updating hostname...")
        run_command(f"echo {hostname} | sudo tee /etc/hostname")
        run_command(f"sudo sed -i 's/127.0.0.1.*/127.0.1.1 {hostname}/' /etc/hosts")

    if run_command("sysctl net.ipv4.ip_forward", check=False).stdout.strip() != "net.ipv4.ip_forward = 1":
        print("Enabling IP forwarding...")
        run_command("sudo sysctl -w net.ipv4.ip_forward=1")

    for service in services:
        if is_service_enabled(service):
            print(f"{service} is already enabled, skipping.")
        else:
            run_command(f"sudo systemctl enable {service}", ignore_errors=True)

        if is_service_running(service) and not configs_changed:
            print(f"{service} is already running, skipping start.")
        else:
            print(f"Starting {service}...")
            run_command(f"sudo systemctl start {service}", ignore_errors=True)

def install_mosquitto(config, temp_dir):
    print("Installing Mosquitto...")
    if is_package_installed("mosquitto"):
        print("Mosquitto is already installed, skipping.")
    else:
        run_command("sudo apt install -y mosquitto mosquitto-clients")

    configs_changed = update_file_if_changed("mosquitto.conf.j2", "/etc/mosquitto/mosquitto.conf", config, temp_dir)
    
    passwd_file = "/etc/mosquitto/passwd"
    passwd_content = f"{config['mqtt']['username']}:{config['mqtt']['password']}"
    if file_hash(passwd_file) != hashlib.sha256(passwd_content.encode()).hexdigest():
        print("Updating Mosquitto password...")
        run_command(f"echo '{passwd_content}' | sudo tee {passwd_file}")
        run_command(f"sudo mosquitto_passwd -U {passwd_file}")

    if is_service_enabled("mosquitto"):
        print("Mosquitto service is already enabled, skipping.")
    else:
        run_command("sudo systemctl enable mosquitto", ignore_errors=True)

    if is_service_running("mosquitto") and not configs_changed:
        print("Mosquitto is already running, skipping start.")
    else:
        print("Starting Mosquitto...")
        run_command("sudo systemctl start mosquitto", ignore_errors=True)

def install_victoria_metrics(config, temp_dir):
    print("Installing VictoriaMetrics...")
    vm_binary = "/usr/local/bin/victoria-metrics"
    # Check if binary exists and is executable
    if os.path.exists(vm_binary) and os.access(vm_binary, os.X_OK):
        print("VictoriaMetrics binary exists and is executable, skipping download.")
    else:
        # Remove any existing invalid binary
        run_command(f"sudo rm -f {vm_binary}", ignore_errors=True)
        run_command(f"sudo rm -f /usr/local/bin/victoria-metrics-prod", ignore_errors=True)
        # Ensure /usr/local/bin exists and is writable
        run_command("sudo mkdir -p /usr/local/bin")
        run_command("sudo chmod 755 /usr/local/bin")
        # Use 64-bit ARM binary for arm64 userland
        vm_url = "https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v1.115.0/victoria-metrics-linux-arm64-v1.115.0.tar.gz"
        vm_tar = "/tmp/vm.tar.gz"
        print(f"Downloading VictoriaMetrics from {vm_url}...")
        run_command(f"wget {vm_url} -O {vm_tar}")
        
        if not os.path.exists(vm_tar):
            raise FileNotFoundError(f"Failed to download VictoriaMetrics from {vm_url}. Please check the URL or internet connection.")
        
        # Verify tarball integrity
        tar_size = os.path.getsize(vm_tar) // 1024  # Size in KB
        tar_hash = file_hash(vm_tar)
        print(f"Tarball size: {tar_size} KB, SHA256: {tar_hash}")
        
        print("Inspecting tarball contents...")
        tar_contents = run_command(f"tar -tzf {vm_tar}", check=False).stdout.strip()
        print(f"Tarball contents:\n{tar_contents}")
        if "victoria-metrics" not in tar_contents and "victoria-metrics-prod" not in tar_contents:
            raise ValueError(f"Tarball does not contain expected binary ('victoria-metrics' or 'victoria-metrics-prod').")
        
        print("Extracting VictoriaMetrics binary...")
        run_command(f"sudo tar -xzf {vm_tar} -C /usr/local/bin")
        # Check for victoria-metrics-prod and rename if necessary
        prod_binary = "/usr/local/bin/victoria-metrics-prod"
        if os.path.exists(prod_binary) and not os.path.exists(vm_binary):
            print(f"Renaming {prod_binary} to {vm_binary}...")
            run_command(f"sudo mv {prod_binary} {vm_binary}")
        # Verify the binary exists
        if not os.path.exists(vm_binary):
            extracted_files = run_command("ls -l /usr/local/bin", check=False).stdout.strip()
            print(f"Files in /usr/local/bin after extraction:\n{extracted_files}")
            raise FileNotFoundError(f"Failed to extract or rename VictoriaMetrics binary to {vm_binary}.")
        run_command(f"sudo chmod +x {vm_binary}")
        run_command(f"sudo rm -f {vm_tar}")

    update_file_if_changed("victoria_metrics.yml.j2", "/etc/victoria-metrics.yml", config, temp_dir)
    
    if run_command("id victoria-metrics", check=False).returncode == 0:
        print("victoria-metrics user exists, skipping creation.")
    else:
        run_command("sudo useradd -r victoria-metrics", ignore_errors=True)

    run_command(f"sudo chown victoria-metrics:victoria-metrics {vm_binary}")
    run_command("sudo mkdir -p /var/lib/victoria-metrics")
    run_command("sudo chown victoria-metrics:victoria-metrics /var/lib/victoria-metrics")

    vm_service = "/etc/systemd/system/victoria-metrics.service"
    service_content = f"""[Unit]
Description=VictoriaMetrics
After=network.target

[Service]
User=victoria-metrics
Group=victoria-metrics
ExecStart={vm_binary} --storageDataPath=/var/lib/victoria-metrics --httpListenAddr=:{config['victoria_metrics']['port']}
Restart=always

[Install]
WantedBy=multi-user.target
"""
    if file_hash(vm_service) != hashlib.sha256(service_content.encode()).hexdigest():
        print("Updating VictoriaMetrics service file...")
        with open(os.path.join(temp_dir, "vm.service"), 'w') as f:
            f.write(service_content)
        run_command(f"sudo mv {temp_dir}/vm.service {vm_service}")
        run_command("sudo systemctl daemon-reload")

    if is_service_enabled("victoria-metrics"):
        print("VictoriaMetrics service is already enabled, skipping.")
    else:
        run_command("sudo systemctl enable victoria-metrics", ignore_errors=True)

    if is_service_running("victoria-metrics"):
        print("VictoriaMetrics is already running, skipping start.")
    else:
        print("Starting VictoriaMetrics...")
        run_command("sudo systemctl start victoria-metrics", ignore_errors=True)

def install_node_red(config, temp_dir):
    print("Installing Node-RED...")
    if is_package_installed("nodejs") and is_package_installed("npm") and shutil.which("node-red"):
        print("Node-RED and dependencies are already installed, skipping.")
    else:
        run_command("sudo apt install -y nodejs npm")
        run_command("sudo npm install -g --unsafe-perm node-red")

    node_red_dir = os.path.join(HOME_DIR, ".node-red")
    os.makedirs(node_red_dir, exist_ok=True)
    configs_changed = update_file_if_changed("node_red_settings.js.j2", os.path.join(node_red_dir, "settings.js"), config, temp_dir)

    nodered_service = "/etc/systemd/system/nodered.service"
    service_content = f"""[Unit]
Description=Node-RED
After=network.target

[Service]
User={os.getlogin()}
ExecStart=/usr/bin/node-red
WorkingDirectory={node_red_dir}
Restart=always

[Install]
WantedBy=multi-user.target
"""
    if file_hash(nodered_service) != hashlib.sha256(service_content.encode()).hexdigest():
        print("Updating Node-RED service file...")
        with open(os.path.join(temp_dir, "nodered.service"), 'w') as f:
            f.write(service_content)
        run_command(f"sudo mv {temp_dir}/nodered.service {nodered_service}")
        run_command("sudo systemctl daemon-reload")

    if is_service_enabled("nodered"):
        print("Node-RED service is already enabled, skipping.")
    else:
        run_command("sudo systemctl enable nodered", ignore_errors=True)

    if is_service_running("nodered") and not configs_changed:
        print("Node-RED is already running, skipping start.")
    else:
        print("Starting Node-RED...")
        run_command("sudo systemctl start nodered", ignore_errors=True)

def install_dashboard(config, temp_dir):
    print("Installing Dashboard...")
    dashboard_packages = ["python3-flask", "python3-socketio", "python3-paho-mqtt", "python3-requests"]
    packages_missing = False
    for pkg in dashboard_packages:
        if is_package_installed(pkg):
            print(f"{pkg} is already installed, skipping.")
        else:
            packages_missing = True
    if packages_missing:
        print("Installing dashboard dependencies via apt...")
        run_command("sudo apt update && sudo apt install -y python3-flask python3-socketio python3-paho-mqtt python3-requests")

    dashboard_file = os.path.join(INSTALL_DIR, "flask_app.py")
    if update_file_if_changed("flask_app.py", dashboard_file, config, temp_dir):
        run_command(f"sudo chown {os.getlogin()}:{os.getlogin()} {dashboard_file}")
        run_command(f"sudo chmod 644 {dashboard_file}")

    dashboard_service = "/etc/systemd/system/sysohub-dashboard.service"
    service_content = f"""[Unit]
Description=sysohub Dashboard
After=network.target

[Service]
User={os.getlogin()}
ExecStart=/usr/bin/python3 {dashboard_file}
Restart=always

[Install]
WantedBy=multi-user.target
"""
    if file_hash(dashboard_service) != hashlib.sha256(service_content.encode()).hexdigest():
        print("Updating Dashboard service file...")
        with open(os.path.join(temp_dir, "dashboard.service"), 'w') as f:
            f.write(service_content)
        run_command(f"sudo mv {temp_dir}/dashboard.service {dashboard_service}")
        run_command("sudo systemctl daemon-reload")

    if is_service_enabled("sysohub-dashboard"):
        print("Dashboard service is already enabled, skipping.")
    else:
        run_command("sudo systemctl enable sysohub-dashboard", ignore_errors=True)

    if is_service_running("sysohub-dashboard"):
        print("Dashboard is already running, skipping start.")
    else:
        print("Starting Dashboard...")
        run_command("sudo systemctl start sysohub-dashboard", ignore_errors=True)

def backup():
    print("Creating backup...")
    backup_dir = os.path.join(HOME_DIR, "backups")
    timestamp = run_command("date +%Y%m%d_%H%M%S", check=False).stdout.strip()
    os.makedirs(backup_dir, exist_ok=True)
    backup_file = f"{backup_dir}/iot_backup_{timestamp}.tar.gz"
    run_command(f"tar -czf {backup_file} {INSTALL_DIR}")
    print(f"Backup created at {backup_file}")

def update():
    print("Updating services...")
    run_command("sudo apt update && sudo apt upgrade -y")
    run_command("sudo npm install -g --unsafe-perm node-red")
    run_command("sudo apt install -y python3-flask python3-socketio python3-paho-mqtt python3-requests")
    run_command("sudo systemctl restart mosquitto victoria-metrics nodered sysohub-dashboard", ignore_errors=True)

def status():
    print("Service status:")
    for service in ["hostapd", "dnsmasq", "avahi-daemon", "mosquitto", "victoria-metrics", "nodered", "sysohub-dashboard"]:
        run_command(f"systemctl status {service} --no-pager", check=False)

def main():
    check_root()
    parser = argparse.ArgumentParser(description="sysohub IoT Lite Setup and Management")
    parser.add_argument("command", choices=["setup", "backup", "update", "status"], help="Command to execute")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temp_dir:
        config = load_config()

        if args.command == "setup":
            setup_wifi_ap(config, temp_dir)
            install_mosquitto(config, temp_dir)
            install_victoria_metrics(config, temp_dir)
            install_node_red(config, temp_dir)
            install_dashboard(config, temp_dir)
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