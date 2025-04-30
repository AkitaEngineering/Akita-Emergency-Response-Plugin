# aerp/cli.py
# Copyright (C) 2025 Akita Engineering / www.akitaengineering.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Command Line Interface (CLI) for the Akita Emergency Response Plugin (AERP).

Provides user interaction to start/stop emergency broadcasts, check status,
and manage the plugin's operation. Connects to a Meshtastic device and
handles network events.
"""

import meshtastic
import meshtastic.serial_interface # Import specific interface type
import meshtastic.tcp_interface   # Import TCP interface type
from pubsub import pub # For subscribing to Meshtastic events
import time
import logging
import argparse
import sys
import os
import json # For printing status nicely
from datetime import datetime # For formatting status timestamps

# --- Logging Setup ---
# Configure logging early, before importing other AERP modules that might log.
# Basic configuration, can be customized further (e.g., file logging).
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format)
# Get the root logger for the CLI part
logger = logging.getLogger(__name__) # Use __name__ for module-specific logging

# --- AERP Module Imports ---
# Import necessary components from the AERP package
try:
    from .plugin import AERP
    from .config import ConfigManager
    from .constants import CONFIG_ENABLED, CONFIG_PORT, CONFIG_INTERVAL
    from .utils import format_node_id
except ImportError as e:
     # Handle cases where the script might be run directly without proper installation
     logger.exception(f"ImportError: Failed to import AERP modules. Ensure the package structure is correct or run using 'python -m aerp.cli'. Error: {e}")
     sys.exit(1)


# --- Global State ---
# Global variable to hold the AERP plugin instance.
# This is necessary because the Meshtastic pubsub callbacks are simple functions
# and need access to the running AERP instance.
# A class-based CLI structure could avoid globals but adds complexity.
aerp_instance: AERP | None = None
meshtastic_interface = None # Global reference to the interface for cleanup

# --- Meshtastic Event Callbacks ---

def onReceive(packet, interface):
    """
    Callback wrapper for Meshtastic 'meshtastic.receive' pubsub events.
    Routes the received packet to the AERP instance for handling.

    Args:
        packet (dict): The packet data dictionary from meshtastic-python.
        interface: The Meshtastic interface instance that received the packet.
                   (Passed by pubsub, may not always be needed if using global).
    """
    # logger.debug(f"CLI onReceive: Packet received: {packet}") # Very verbose
    if aerp_instance:
        try:
            aerp_instance.handle_incoming(packet, interface)
        except Exception as e:
            # Prevent callback errors from crashing the CLI
            logger.exception(f"Error in AERP handle_incoming callback: {e}")
    else:
        logger.warning("CLI onReceive: Received packet but AERP instance is not initialized.")

def onConnection(interface, connected):
    """
    Callback wrapper for Meshtastic 'meshtastic.connection' pubsub events.
    Notifies the AERP instance about connection status changes.

    Args:
        interface: The Meshtastic interface instance.
        connected (bool): True if connected, False if disconnected.
    """
    logger.debug(f"CLI onConnection: Connection status changed: {'Connected' if connected else 'Disconnected'}")
    if aerp_instance:
        try:
            aerp_instance.on_connection_change(interface, connected)
        except Exception as e:
            # Prevent callback errors from crashing the CLI
            logger.exception(f"Error in AERP on_connection_change callback: {e}")
    else:
        logger.warning("CLI onConnection: Status changed but AERP instance is not initialized.")

# --- Meshtastic Interface Setup ---

def setup_meshtastic_interface(device_path=None, host=None, no_serial=False):
    """
    Sets up and returns the appropriate Meshtastic interface (Serial or TCP).

    Args:
        device_path (str, optional): Path to the serial device (e.g., /dev/ttyUSB0, COM3).
        host (str, optional): Hostname or IP address for TCP interface.
        no_serial (bool): If True, explicitly disable serial connection attempts.

    Returns:
        Meshtastic Interface object or None if connection fails.
    """
    global meshtastic_interface # Allow modification of the global interface reference

    if host:
        logger.info(f"Attempting to connect to Meshtastic device via TCP: {host}")
        try:
            meshtastic_interface = meshtastic.tcp_interface.TCPInterface(hostname=host)
            logger.info(f"Successfully connected via TCP to {host}")
            return meshtastic_interface
        except Exception as e:
            logger.error(f"Failed to connect via TCP to {host}: {e}")
            return None # Indicate failure
    elif no_serial:
         logger.info("Serial connection disabled by --no-serial argument.")
         return None
    else:
        # Try Serial connection (default)
        logger.info("Attempting to connect to Meshtastic device via Serial...")
        try:
            if device_path:
                logger.info(f"Using specified serial device: {device_path}")
                meshtastic_interface = meshtastic.serial_interface.SerialInterface(devPath=device_path, debugOut=sys.stderr if logger.level == logging.DEBUG else None)
            else:
                logger.info("Auto-detecting serial device...")
                meshtastic_interface = meshtastic.serial_interface.SerialInterface(debugOut=sys.stderr if logger.level == logging.DEBUG else None)

            # Wait briefly for the connection to establish and node info to potentially populate
            logger.info("Waiting for node information...")
            time.sleep(3) # Adjust as needed
            if not meshtastic_interface.myInfo:
                 logger.warning("Connected via serial, but node information not yet available. Plugin might need manual start.")
            else:
                 logger.info(f"Serial connection established. My Node Info: {meshtastic_interface.myInfo.my_node_num:#010x}")

            return meshtastic_interface
        except meshtastic.MeshtasticError as e:
            logger.error(f"Meshtastic serial connection error: {e}")
            logger.error("Ensure device is connected, powered on, and drivers are installed.")
            logger.error("Common paths: /dev/ttyUSB0, /dev/ttyACM0 (Linux), COM3, COM4 (Windows).")
            logger.error("Try specifying the path with --device /path/to/device")
            return None # Indicate failure
        except Exception as e:
            # Catch other unexpected errors during serial connection
            logger.exception(f"Unexpected error connecting via serial: {e}")
            return None # Indicate failure

# --- CLI Command Handling ---

def print_status(status_dict):
    """Formats and prints the status dictionary to the console."""
    print("\n--- AERP Status ---")
    print(f"  My Node ID:       {status_dict.get('my_node_id', 'Unknown')}")
    print(f"  Emergency Active: {status_dict.get('emergency_active', False)}")
    active_id = status_dict.get('last_emergency_id')
    print(f"  Last Emergency ID:{active_id if active_id else '(None Active)'}")

    print("  Acknowledgements (for last active emergency):")
    acks = status_dict.get('active_acknowledgements', {})
    if active_id and acks:
        for node_id, timestamp in acks.items():
            print(f"    - {node_id} (at {timestamp})")
    elif active_id:
        print("    (None received for this ID yet)")
    else:
        print("    (No emergency active to receive ACKs for)")

    print("  Active Received Emergencies (from others):")
    received = status_dict.get('active_received_emergencies', {})
    if received:
        for node_id, info in received.items():
            gps_str = "No GPS"
            if info.get('gps') and 'latitude' in info['gps'] and 'longitude' in info['gps']:
                 gps_str = f"Lat {info['gps']['latitude']:.5f}, Lon {info['gps']['longitude']:.5f}"
            batt_str = f"{info.get('battery', 'N/A')}%" if info.get('battery') is not None else "N/A"
            print(f"    - From: {node_id}")
            print(f"        ID: {info.get('emergency_id', 'N/A')}")
            print(f"        Msg: '{info.get('message', '')}'")
            print(f"        GPS: {gps_str}")
            print(f"        Battery: {batt_str}")
            print(f"        Received At: {info.get('received_at', 'N/A')}")
            print(f"        Last Seen: {info.get('last_seen', 'N/A')}")
    else:
        print("    (None)")

    # Optionally print config - can be verbose
    # print("  Current Configuration:")
    # print(json.dumps(status_dict.get('config', {}), indent=4))
    print("-------------------\n")


def run_cli_loop():
    """Runs the main interactive command loop for the user."""
    global aerp_instance # Access the global instance

    print("\n--- Akita Emergency Response Plugin (AERP) ---")
    print("Type 'help' for commands.")

    while True:
        try:
            user_input = input("AERP> ").strip().lower()

            if not user_input: # Ignore empty input
                continue

            # --- Command Processing ---
            if user_input == "start":
                if aerp_instance:
                    if aerp_instance.start_emergency():
                         print("Emergency broadcast started.")
                    # else: # start_emergency logs warnings/errors
                else:
                    logger.error("AERP instance not ready.")

            elif user_input == "stop":
                if aerp_instance:
                    if aerp_instance.stop_emergency(send_clear=True):
                         print("Emergency broadcast stopped. 'All Clear' sent.")
                    # else: # stop_emergency logs info if not active
                else:
                    logger.error("AERP instance not ready.")

            elif user_input == "clear":
                if aerp_instance:
                    if aerp_instance.emergency_active:
                        logger.warning("Emergency is currently active. Use 'stop' to stop and send clear.")
                    elif aerp_instance.last_emergency_id:
                        # Note: stop_emergency clears last_emergency_id.
                        # This command is mainly useful if stop failed to send clear (e.g., disconnect)
                        # We need a way to reference the *previous* ID if needed.
                        # For now, let's assume it clears the ID stored *before* stop was called.
                        # This needs refinement - maybe store the *last sent* ID separately?
                        # Current implementation: This command might not work as intended after a 'stop'.
                        # Re-purposing: Send a generic clear if no ID? Or require ID?
                        # Decision: Let's make 'clear' less useful for now, 'stop' is primary.
                        # Alternative: 'clear <id>'? Too complex for simple CLI.
                        logger.warning("The 'clear' command after 'stop' might not use the correct ID.")
                        logger.warning("Use 'stop' to ensure the correct 'All Clear' is sent.")
                        # aerp_instance.send_clear_message(aerp_instance.last_emergency_id) # This ID is likely None now
                    else:
                        logger.info("No previous emergency ID recorded to clear.")
                else:
                    logger.error("AERP instance not ready.")

            elif user_input == "status":
                if aerp_instance:
                    status = aerp_instance.get_status()
                    print_status(status)
                else:
                    logger.error("AERP instance not ready.")

            elif user_input == "exit" or user_input == "quit":
                logger.info("Exit command received.")
                break # Exit the loop

            elif user_input == "help":
                print("\nAvailable Commands:")
                print("  start   - Start broadcasting emergency messages.")
                print("  stop    - Stop broadcasting and send 'All Clear'.")
                # print("  clear   - Manually send 'All Clear' (use 'stop' preferably).")
                print("  status  - Show current status, ACKs, and received alerts.")
                print("  help    - Show this help message.")
                print("  exit    - Quit the plugin (stops broadcast first).")
                print("")

            else:
                print(f"Unknown command: '{user_input}'. Type 'help' for options.")

        except EOFError: # Handle Ctrl+D
            logger.info("EOF detected. Exiting...")
            break
        except KeyboardInterrupt: # Handle Ctrl+C
            logger.info("\nCtrl+C detected. Exiting...")
            break
        except Exception as e:
             logger.exception(f"An unexpected error occurred in the CLI loop: {e}")
             # Optionally continue or break based on severity
             # break

# --- Main Execution ---

def main():
    """Main function to parse arguments, initialize, and run the AERP CLI."""
    global aerp_instance # Allow modification of the global instance

    parser = argparse.ArgumentParser(
        description="Akita Emergency Response Plugin (AERP) for Meshtastic.",
        epilog="Example: python -m aerp.cli --device /dev/ttyUSB0 --debug"
    )
    # Connection arguments (mutually exclusive conceptually, though not enforced by argparse group here)
    parser.add_argument("--device", default=None, help="Specify the Meshtastic serial device path (e.g., /dev/ttyUSB0, COM3).")
    parser.add_argument("--host", default=None, help="Specify hostname or IP for TCP connection.")
    parser.add_argument("--no-serial", action="store_true", help="Disable serial connection attempt (use with --host or if no device expected).")

    # Configuration and Logging
    parser.add_argument("--config", default="config/aerp_config.json", help="Path to the AERP JSON configuration file.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging for detailed output.")
    args = parser.parse_args()

    # --- Configure Logging Level ---
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG) # Set root logger level
        # Ensure all handlers also respect the level
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.DEBUG)
            # Optionally re-apply formatter if needed, basicConfig usually handles it
            # handler.setFormatter(logging.Formatter(log_format))
        logger.info("Debug logging enabled.")
    else:
         logging.getLogger().setLevel(logging.INFO)
         for handler in logging.getLogger().handlers:
             handler.setLevel(logging.INFO)

    # --- Initialization ---
    logger.info("--- Akita Emergency Response Plugin Starting ---")

    # 1. Load Configuration
    config_manager = ConfigManager(args.config)
    logger.info(f"Loaded configuration. AERP Port: {config_manager.get(CONFIG_PORT)}, Interval: {config_manager.get(CONFIG_INTERVAL)}s")

    # 2. Connect to Meshtastic Device
    interface = setup_meshtastic_interface(args.device, args.host, args.no_serial)
    if not interface:
        logger.error("Failed to establish Meshtastic connection. Exiting.")
        sys.exit(1)

    # 3. Initialize AERP Core Logic
    # Pass the connected interface and loaded config
    aerp_instance = AERP(interface, config_manager)

    # 4. Register Meshtastic Callbacks using PubSub
    # It's generally recommended to subscribe *after* the interface is up.
    logger.info("Registering Meshtastic pubsub callbacks...")
    try:
        pub.subscribe(onReceive, "meshtastic.receive")
        pub.subscribe(onConnection, "meshtastic.connection")
        # Note: If using older meshtastic versions, these might be needed instead/as well:
        # interface.addReceiveCallback(onReceive)
        # interface.addConnectionCallback(onConnection)
        logger.debug("Callbacks registered.")
    except Exception as e:
         logger.exception(f"Error subscribing to Meshtastic pubsub topics: {e}")
         # Decide if this is fatal or if the app can proceed without event handling
         interface.close()
         sys.exit(1)


    # --- Auto-Start Check ---
    if config_manager.get(CONFIG_ENABLED, False):
        logger.info("Configuration enables auto-start. Attempting to start emergency broadcast...")
        # Need to wait briefly for node info to potentially populate *after* connection and AERP init
        time.sleep(2) # Adjust as needed
        if aerp_instance and aerp_instance.my_node_num:
             aerp_instance.start_emergency()
        elif aerp_instance:
             logger.error("Auto-start failed: Node info not available yet. Start manually using 'start' command.")
        else:
             logger.error("Auto-start failed: AERP instance not initialized correctly.")


    # --- Start Interactive Loop ---
    try:
        run_cli_loop()
    finally:
        # --- Cleanup ---
        logger.info("Shutting down AERP...")
        if aerp_instance:
            logger.debug("Stopping emergency broadcast (if active)...")
            # Stop without sending clear, as we are exiting anyway
            aerp_instance.stop_emergency(send_clear=False)

        # Close the Meshtastic interface gracefully
        if meshtastic_interface: # Use the global reference
            logger.info("Closing Meshtastic interface.")
            try:
                meshtastic_interface.close()
            except Exception as e:
                 logger.error(f"Error closing Meshtastic interface: {e}")

        logger.info("--- AERP Shutdown Complete ---")

if __name__ == '__main__':
    # This allows running the CLI using: python -m aerp.cli
    main()
