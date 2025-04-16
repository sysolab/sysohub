from flask import Flask, render_template
import paho.mqtt.client as mqtt
import requests
import subprocess
import yaml
import os

app = Flask(__name__, template_folder='/home/pi/iot-lite/static')

CONFIG_PATH = "/home/pi/iot-lite/config/config.yml"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)['project']

config = load_config()

mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(config['mqtt']['username'], config['mqtt']['password'])
mqtt_client.connect(config['mqtt']['uri'].replace('mqtt://', ''), config['mqtt']['port'])
latest_data = {}

def on_message(client, userdata, msg):
    global latest_data
    latest_data[msg.topic] = msg.payload.decode()

mqtt_client.on_message = on_message
mqtt_client.subscribe(config['mqtt']['topic'])
mqtt_client.loop_start()

@app.route('/')
def index():
    vm_url = f"http://localhost:{config['victoria_metrics']['port']}/api/v1/query?query=up"
    try:
        vm_data = requests.get(vm_url).json()
    except:
        vm_data = {"status": "error"}
    
    services = {}
    for service in ["mosquitto", "victoria-metrics", "nodered", "hostapd", "dnsmasq"]:
        result = subprocess.run(f"systemctl is-active {service}", shell=True, capture_output=True, text=True)
        services[service] = result.stdout.strip() == "active"
    
    return render_template('index.html', data=latest_data, vm_data=vm_data, services=services, config=config)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=config['dashboard']['port'])