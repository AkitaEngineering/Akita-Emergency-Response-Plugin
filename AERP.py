import meshtastic
import time
import json
import threading
import os
import argparse
from meshtastic.util import get_lora_config

class AERP:
    def __init__(self, interface, log_file="emergency_log.json"):
        self.interface = interface
        self.emergency_active = False
        self.emergency_data = {}
        self.emergency_queue = []
        self.emergency_thread = None
        self.user_id = interface.meshtastic.getMyNodeInfo()['num']
        self.log_file = log_file
        self.lora_config = get_lora_config(interface.meshtastic)

    def activate_emergency(self, message="Emergency! Need assistance.", gps_location=None):
        if self.emergency_active:
            print("Emergency already active.")
            return

        self.emergency_active = True
        self.emergency_data = {
            "type": "emergency",
            "user_id": self.user_id,
            "message": message,
            "gps_location": gps_location,
            "timestamp": time.time()
        }
        self.emergency_queue.append(self.emergency_data)
        self.emergency_thread = threading.Thread(target=self._send_emergency_broadcast)
        self.emergency_thread.start()
        print("Emergency activated.")

    def deactivate_emergency(self):
        if not self.emergency_active:
            print("Emergency not active.")
            return

        self.emergency_active = False
        print("Emergency deactivated.")

    def _send_emergency_broadcast(self):
        while self.emergency_active and self.emergency_queue:
            try:
                data = self.emergency_queue[0]
                self.interface.sendData(data, portNum=meshtastic.constants.DATA_APP)
                time.sleep(self.lora_config.tx_delay)
                self.emergency_queue.pop(0)
            except Exception as e:
                print(f"Error sending emergency broadcast: {e}")

    def handle_incoming(self, packet, interface):
        if packet['decoded']['portNum'] == meshtastic.constants.DATA_APP:
            decoded = packet['decoded']['payload']
            if decoded.get("type") == "emergency":
                print(f"Emergency received: {decoded}")
                self.log_emergency(decoded) #UI integration point.

    def log_emergency(self, data):
        try:
            if not os.path.exists(self.log_file):
                with open(self.log_file, 'w') as f:
                    f.write('[]')

            with open(self.log_file, 'r+') as f:
                file_data = json.load(f)
                file_data.append(data)
                f.seek(0)
                json.dump(file_data, f, indent=4)
        except Exception as e:
            print(f"Error logging emergency data: {e}")

    def onConnection(self, interface, connected):
        if connected:
            print("AERP: Meshtastic connected.")
        else:
            print("AERP: Meshtastic disconnected.")

def onReceive(packet, interface):
    aerp.handle_incoming(packet, interface)

def onConnection(interface, connected):
    aerp.onConnection(interface, connected)

def main():
    parser = argparse.ArgumentParser(description="Akita Emergency Response Plugin")
    parser.add_argument("--log", default="emergency_log.json", help="Log file name")
    args = parser.parse_args()

    interface = meshtastic.SerialInterface()
    global aerp
    aerp = AERP(interface, args.log)
    interface.addReceiveCallback(onReceive)
    interface.addConnectionCallback(onConnection)

if __name__ == '__main__':
    main()
