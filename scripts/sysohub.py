#!/usr/bin/env python3
import os
import subprocess
import yaml
import jinja2
import argparse
import tempfile
import hashlib

# Determine the invoking user's home directory.
# Use the SUDO_USER if available (so that services run as the non-root user).
USER = os.environ.get("SUDO_USER") if "SUDO_USER" in os.environ and os.environ["SUDO_USER"] else os.getlogin()

def get_user_home():
    # Return the home directory of the actual user (not root)
    return os.path.expanduser(f"~{USER}")

# Check if running as root.
def check_root():
    if os.geteuid() != 0:
        raise PermissionError("This script must be run with sudo")

HOME_DIR = get_user_home()
INSTALL_DIR = os.path.join(HOME_DIR, "sysohub")
CONFIG_PATH = os.path.join(INSTALL_DIR, "config", "config.yml")
TEMPLATES_DIR = os.path.join(INSTALL_DIR, "templates")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Config not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)['project']

def run_command(command, check=True, ignore_errors=False):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0 and not ignore_errors:
        raise Exception(f"Command failed: {result.stderr}")
    return result

def is_package_installed(package):
    return run_command(f"dpkg -l | grep {package}", check=False).returncode == 0

def is_service_enabled(service):
    return run_command(f"systemctl is-enabled {service}", check=False).stdout.strip() == "enabled"

def is_service_running(service):
    return run_command(f"systemctl is-active {service}", check=False).stdout.strip() == "active"

def file_hash(file_path):
    if not os.path.exists(file_path):
        return None
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def prompt_overwrite(component, condition):
    if condition:
        print(f"{component} detected.")
        response = input(f"Overwrite {component}? [y/N]: ").strip().lower()
        return response == 'y'
    return True

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
        return True
    print(f"{dest} is up-to-date, skipping.")
    return False

def setup_wifi_ap(config, temp_dir):
    print("Configuring WiFi AP...")
    packages = ["hostapd", "dnsmasq", "avahi-daemon"]
    for pkg in packages:
        if is_package_installed(pkg):
            print(f"{pkg} is installed, skipping.")
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
            print(f"{service} is enabled, skipping.")
        else:
            run_command(f"sudo systemctl enable {service}", ignore_errors=True)
        if is_service_running(service) and not configs_changed:
            print(f"{service} is running, skipping start.")
        else:
            print(f"Starting {service}...")
            run_command(f"sudo systemctl start {service}", ignore_errors=True)

def install_mosquitto(config, temp_dir):
    print("Installing Mosquitto...")
    if is_package_installed("mosquitto"):
        print("Mosquitto is installed, skipping.")
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
        print("Mosquitto service is enabled, skipping.")
    else:
        run_command("sudo systemctl enable mosquitto", ignore_errors=True)
    if is_service_running("mosquitto") and not configs_changed:
        print("Mosquitto is running, skipping start.")
    else:
        print("Starting Mosquitto...")
        run_command("sudo systemctl start mosquitto", ignore_errors=True)

def install_victoria_metrics(config, temp_dir):
    print("Installing VictoriaMetrics...")
    vm_binary = "/usr/local/bin/victoria-metrics"
    if os.path.exists(vm_binary) and os.access(vm_binary, os.X_OK):
        print("VictoriaMetrics binary exists, skipping download.")
    else:
        run_command(f"sudo rm -f {vm_binary} /usr/local/bin/victoria-metrics-prod", ignore_errors=True)
        run_command("sudo mkdir -p /usr/local/bin")
        run_command("sudo chmod 755 /usr/local/bin")
        vm_url = "https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v1.115.0/victoria-metrics-linux-arm64-v1.115.0.tar.gz"
        vm_tar = "/tmp/vm.tar.gz"
        print(f"Downloading VictoriaMetrics from {vm_url}...")
        run_command(f"wget {vm_url} -O {vm_tar}")
        if not os.path.exists(vm_tar):
            raise FileNotFoundError("Failed to download VictoriaMetrics.")
        print("Extracting VictoriaMetrics binary...")
        run_command(f"sudo tar -xzf {vm_tar} -C /usr/local/bin")
        prod_binary = "/usr/local/bin/victoria-metrics-prod"
        if os.path.exists(prod_binary) and not os.path.exists(vm_binary):
            print(f"Renaming {prod_binary} to {vm_binary}...")
            run_command(f"sudo mv {prod_binary} {vm_binary}")
        if not os.path.exists(vm_binary):
            raise FileNotFoundError(f"Failed to extract VictoriaMetrics to {vm_binary}.")
        run_command(f"sudo chmod +x {vm_binary}")
        run_command(f"sudo rm -f {vm_tar}")
    update_file_if_changed("victoria_metrics.yml.j2", "/etc/victoria-metrics.yml", config, temp_dir)
    
    if run_command("id victoria-metrics", check=False).returncode == 0:
        print("victoria-metrics user exists, skipping.")
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
        print("Updating VictoriaMetrics service...")
        with open(os.path.join(temp_dir, "vm.service"), 'w') as f:
            f.write(service_content)
        run_command(f"sudo mv {temp_dir}/vm.service {vm_service}")
        run_command("sudo systemctl daemon-reload")
    if is_service_enabled("victoria-metrics"):
        print("VictoriaMetrics service is enabled, skipping.")
    else:
        run_command("sudo systemctl enable victoria-metrics", ignore_errors=True)
    if is_service_running("victoria-metrics"):
        print("VictoriaMetrics is running, skipping start.")
    else:
        print("Starting VictoriaMetrics...")
        run_command("sudo systemctl start victoria-metrics", ignore_errors=True)

def install_node_red(config, temp_dir):
    print("Installing Node-RED...")
    node_red_installed = run_command("which node-red", check=False).returncode == 0
    if not prompt_overwrite("Node-RED", node_red_installed):
        print("Skipping Node-RED installation.")
    else:
        if not is_package_installed("nodejs"):
            run_command("sudo apt install -y nodejs npm")
        run_command("sudo npm install -g --unsafe-perm node-red")
    # Get actual paths to executables
    node_path = run_command("which node", check=False).stdout.strip() or "/usr/bin/node"
    node_red_path = run_command("which node-red", check=False).stdout.strip() or "/usr/local/bin/node-red"
    # Use the user's home directory (calculated above) for Node-RED's working directory.
    node_red_dir = os.path.join(HOME_DIR, ".node-red")
    os.makedirs(node_red_dir, exist_ok=True)
    configs_changed = update_file_if_changed("node_red_settings.js.j2", os.path.join(node_red_dir, "settings.js"), config, temp_dir)
    
    nodered_service = "/etc/systemd/system/nodered.service"
    service_content = f"""[Unit]
Description=Node-RED
After=network.target

[Service]
User={USER}
Environment="NODE_OPTIONS=--max_old_space_size=512"
ExecStart={node_path} {node_red_path} --max-old-space-size=512 -v
WorkingDirectory={node_red_dir}
Restart=on-failure
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
"""
    if file_hash(nodered_service) != hashlib.sha256(service_content.encode()).hexdigest():
        print("Updating Node-RED service...")
        with open(os.path.join(temp_dir, "nodered.service"), 'w') as f:
            f.write(service_content)
        run_command(f"sudo mv {temp_dir}/nodered.service {nodered_service}")
        run_command("sudo systemctl daemon-reload")
    if is_service_enabled("nodered"):
        print("Node-RED service is enabled, skipping.")
    else:
        run_command("sudo systemctl enable nodered", ignore_errors=True)
    if is_service_running("nodered") and not configs_changed:
        print("Node-RED is running, skipping start.")
    else:
        print("Starting Node-RED...")
        run_command("sudo systemctl restart nodered", ignore_errors=True)

def install_dashboard(config, temp_dir):
    print("Installing Dashboard...")
    flask_installed = is_package_installed("python3-flask")
    if not prompt_overwrite("Dashboard dependencies", flask_installed):
        print("Skipping dashboard dependencies installation.")
    else:
        # Now installing all required packages: flask, socketio, paho-mqtt, requests, eventlet, and psutil.
        run_command("sudo apt update && sudo apt install -y python3-flask python3-socketio python3-paho-mqtt python3-requests python3-eventlet python3-psutil")
    dashboard_file = os.path.join(INSTALL_DIR, "flask_app.py")
    if update_file_if_changed("flask_app.py", dashboard_file, config, temp_dir):
        run_command(f"sudo chown {USER}:{USER} {dashboard_file}")
        run_command(f"sudo chmod 644 {dashboard_file}")
    # Fix Jinja2 template if needed.
    index_html = os.path.join(INSTALL_DIR, "static", "index.html")
    if os.path.exists(index_html):
        with open(index_html, 'r') as f:
            content = f.read()
        if 'tojson(pretty=true)' in content:
            print("Fixing Jinja2 template in index.html...")
            content = content.replace('tojson(pretty=true)', 'tojson | safe')
            temp_file = os.path.join(temp_dir, "index.html")
            with open(temp_file, 'w') as f:
                f.write(content)
            run_command(f"sudo mv {temp_file} {index_html}")
            run_command(f"sudo chown {USER}:{USER} {index_html}")
            run_command(f"sudo chmod 644 {index_html}")
    dashboard_service = "/etc/systemd/system/sysohub-dashboard.service"
    service_content = f"""[Unit]
Description=sysohub Dashboard
After=network.target

[Service]
User={USER}
ExecStart=/usr/bin/python3 {dashboard_file}
Restart=always

[Install]
WantedBy=multi-user.target
"""
    if file_hash(dashboard_service) != hashlib.sha256(service_content.encode()).hexdigest():
        print("Updating Dashboard service...")
        with open(os.path.join(temp_dir, "dashboard.service"), 'w') as f:
            f.write(service_content)
        run_command(f"sudo mv {temp_dir}/dashboard.service {dashboard_service}")
        run_command("sudo systemctl daemon-reload")
    if is_service_enabled("sysohub-dashboard"):
        print("Dashboard service is enabled, skipping.")
    else:
        run_command("sudo systemctl enable sysohub-dashboard", ignore_errors=True)
    if is_service_running("sysohub-dashboard"):
        print("Dashboard is running, skipping start.")
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
    run_command("sudo apt install -y python3-flask python3-socketio python3-paho-mqtt python3-requests python3-eventlet python3-psutil")
    run_command("sudo systemctl restart mosquitto victoria-metrics nodered sysohub-dashboard", ignore_errors=True)

def status():
    print("Service status:")
    for service in ["hostapd", "dnsmasq", "avahi-daemon", "mosquitto", "victoria-metrics", "nodered", "sysohub-dashboard"]:
        run_command(f"systemctl status {service} --no-pager", check=False)

def main():
    check_root()
    parser = argparse.ArgumentParser(description="sysohub IoT Lite Setup")
    parser.add_argument("command", choices=["setup", "backup", "update", "status"])
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
