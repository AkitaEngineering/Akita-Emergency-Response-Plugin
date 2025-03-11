import meshtastic
import time
import json
import threading
import logging
import argparse
import math
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class AERP:
    def __init__(self, interface, config_file="aerp_config.json"):
        self.interface = interface
        self.config_file = config_file
        self.emergency_thread = None
        self.load_config()
        self.emergency_active = False
        self.user_id = interface.meshtastic.getMyNodeInfo()['num']
        self.acknowledgements = {}  # Store acknowledgements
        self.ack_timeout = 60 # seconds

    def load_config(self):
        try:
            if not os.path.exists(self.config_file):
                with open(self.config_file, "w") as f:
                    json.dump({
                        "interval": 5,
                        "emergency_port": 3,
                        "emergency_message": "HELP! Emergency situation detected.",
                        "alert_radius": 500
                    }, f, indent=4)
            with open(self.config_file, "r") as f:
                self.config = json.load(f)
            if not all(key in self.config for key in ["interval", "emergency_port", "emergency_message", "alert_radius"]):
                raise ValueError("Config file missing required keys.")
            if not isinstance(self.config["interval"], int) or not isinstance(self.config["emergency_port"], int) or not isinstance(self.config["alert_radius"], int):
                raise ValueError("Config values must be integers.")
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logging.error(f"AERP: Error loading config: {e}")
            self.config = {
                "interval": 5,
                "emergency_port": 3,
                "emergency_message": "HELP! Emergency situation detected.",
                "alert_radius": 500
            }

    def start_emergency(self):
        if not self.emergency_active:
            self.emergency_active = True
            self.emergency_thread = threading.Thread(target=self._send_emergency_broadcast)
            self.emergency_thread.start()
            logging.info("AERP: Emergency broadcast started.")

    def stop_emergency(self):
        if self.emergency_active:
            self.emergency_active = False
            if self.emergency_thread:
                self.emergency_thread.join(timeout=2)
            logging.info("AERP: Emergency broadcast stopped.")

    def _send_emergency_broadcast(self):
        while self.emergency_active:
            try:
                gps = self.interface.meshtastic.getGps()
                battery = self.interface.meshtastic.getBatteryLevel()
                message = {
                    "type": "emergency",
                    "user_id": self.user_id,
                    "message": self.config.get("emergency_message", "Emergency!"),
                    "gps": gps,
                    "battery": battery,
                    "timestamp": time.time(),
                }
                self.interface.sendData(message, portNum=self.config.get("emergency_port", meshtastic.constants.DATA_APP))
                time.sleep(self.config.get("interval", 10))
            except Exception as e:
                logging.error(f"AERP: Error in emergency broadcast: {e}")

    def handle_incoming(self, packet, interface):
        if packet['decoded']['portNum'] == self.config.get("emergency_port", meshtastic.constants.DATA_APP):
            decoded = packet['decoded']['payload']
            if decoded.get("type") == "emergency":
                logging.warning(f"AERP: Emergency message received from {packet['from']}: {decoded}")
                # Send acknowledgement
                ack_message = {
                    "type": "ack",
                    "original_user_id": decoded["user_id"],
                    "timestamp": time.time(),
                }
                interface.sendData(ack_message, destAddr=packet['from'], portNum=self.config.get("emergency_port", meshtastic.constants.DATA_APP))
                self.check_alert_radius(packet)
            elif decoded.get("type") == "ack":
                if decoded.get("original_user_id") == self.user_id:
                    self.acknowledgements[packet['from']] = time.time()
                    logging.info(f"AERP: Acknowledgement received from {packet['from']}")
                    self.remove_stale_acks()

    def remove_stale_acks(self):
        current_time = time.time()
        stale_acks = [node_id for node_id, timestamp in self.acknowledgements.items() if current_time - timestamp > self.ack_timeout]
        for node_id in stale_acks:
            del self.acknowledgements[node_id]

    def handle_user_input(self):
        while True:
            user_input = input("AERP: Enter 'start' or 'stop' to control emergency broadcast (or 'exit' to quit): ").lower()
            if user_input == "start":
                self.start_emergency()
            elif user_input == "stop":
                self.stop_emergency()
            elif user_input == "exit":
                self.stop_emergency()
                break
            else:
                print("AERP: Invalid input.")

    def onConnection(self, interface, connected):
        if connected:
            logging.info("AERP: Meshtastic connected.")
        else:
            logging.info("AERP: Meshtastic disconnected.")
            self.stop_emergency()

    def check_alert_radius(self, packet):
        if packet['decoded']['portNum'] == meshtastic.constants.DATA_APP:
            decoded = packet['decoded']['payload']
            if decoded.get("gps"):
                my_gps = self.interface.meshtastic.getGps()
                if my_gps:
                    distance = self.calculate_distance(my_gps, decoded["gps"])
                    if distance <= self.config.get("alert_radius", 1000):  # 1000 meters default.
                        logging.warning(f"AERP: Device {packet['from']} within alert radius: {decoded}")

    def calculate_distance(self, gps1, gps2):
        try:
            lat1, lon1 = gps1["latitude"], gps1["longitude"]
            lat2, lon2 = gps2["latitude"], gps2["longitude"]
            R = 6371000  # Radius of the Earth in meters
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = math.sin(dlat / 2) * math.sin(dlat / 2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) * math.sin(dlon / 2)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            distance = R * c
            return distance
        except Exception as e:
            logging.error(f"AERP: Error calculating distance: {e}")
            return float('inf')

def onReceive(packet, interface):
    aerp.handle_incoming(packet, interface)

def onConnection(interface, connected):
    aerp.onConnection(interface, connected)

def main():
    parser = argparse.ArgumentParser(description="Akita Emergency Response Plugin")
    parser.add_argument("--config", default="aerp_config.json", help="AERP config file")
    args = parser.parse_args()

    interface = meshtastic.SerialInterface()
    global aerp
    aerp = AERP(interface, args.config)
    interface.addReceiveCallback(onReceive)
    interface.addConnectionCallback(onConnection)

    aerp.handle_user_input()

if __name__ == '__main__':
    main()
