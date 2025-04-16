from flask import Flask, render_template
import paho.mqtt.client as mqtt
import json
import socketio
import eventlet
import eventlet.wsgi
from threading import Lock
import psutil
import subprocess

app = Flask(__name__)
sio = socketio.Server(async_mode='eventlet')
app.wsgi_app = socketio.WSGIApp(sio, app.wsgi_app)

# In-memory data store: {key: [{timestamp, value}]}
data_store = {}
data_lock = Lock()
MAX_POINTS = 50

# MQTT settings
MQTT_BROKER = "192.168.4.1"
MQTT_PORT = 1883
MQTT_TOPIC = "v1/devices/me/telemetry"
MQTT_USER = "plantomioX1"
MQTT_PASS = "plantomioX1Pass"

# Service list
SERVICES = ["mosquitto", "victoria-metrics", "nodered", "hostapd", "dnsmasq"]

def get_service_status():
    status = {}
    for service in SERVICES:
        result = subprocess.run(f"systemctl is-active {service}", shell=True, capture_output=True, text=True)
        status[service] = result.stdout.strip() == "active"
    return status

def get_system_stats():
    return {
        "cpu": psutil.cpu_percent(interval=1),
        "memory": psutil.virtual_memory().percent
    }

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT with code {rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        timestamp = payload.get("timestamp", 0)
        with data_lock:
            for key, value in payload.items():
                if key in ["temperature", "distance", "pH", "ORP", "TDS", "EC"]:
                    if key not in data_store:
                        data_store[key] = []
                    data_store[key].append({"timestamp": timestamp, "value": float(value)})
                    if len(data_store[key]) > MAX_POINTS:
                        data_store[key].pop(0)
            # Emit update to clients
            sio.emit('data_update', {
                'data': payload,
                'store': data_store,
                'services': get_service_status(),
                'system': get_system_stats()
            })
    except Exception as e:
        print(f"MQTT error: {e}")

# MQTT client setup
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, MQTT_PORT)
mqtt_client.loop_start()

@app.route('/')
def index():
    with data_lock:
        latest_data = data_store.copy()
        pretty_json = json.dumps(latest_data, indent=2) if latest_data else "{}"
    return render_template('index.html', pretty_json=pretty_json, services=get_service_status(), system=get_system_stats())

if __name__ == '__main__':
    eventlet.wsgi.server(eventlet.listen(('0.0.0.0', 5000)), app)