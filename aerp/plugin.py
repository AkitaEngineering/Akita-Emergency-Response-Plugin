# aerp/plugin.py
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
Core logic for the Akita Emergency Response Plugin (AERP).

This module contains the AERP class which manages the emergency state,
message broadcasting, acknowledgement handling, proximity alerts, and
interaction with the Meshtastic network.
"""

import time
import threading
import logging
import uuid
import json # Needed for sending JSON payloads
from datetime import datetime # For formatting timestamps

# Import pubsub explicitly if direct subscription is needed, though meshtastic usually handles it
# from pubsub import pub

import meshtastic.util # For PortNum constants if needed
from .constants import (
    MSG_TYPE_EMERGENCY, MSG_TYPE_ACK, MSG_TYPE_CLEAR,
    CONFIG_INTERVAL, CONFIG_PORT, CONFIG_MESSAGE, CONFIG_RADIUS, CONFIG_ACK_TIMEOUT
)
from .utils import calculate_distance, get_location_from_packet, format_node_id
from .config import ConfigManager # Type hinting

# Get a logger specific to this module
logger = logging.getLogger(__name__)

class AERP:
    """
    Akita Emergency Response Plugin Core Logic.

    Manages emergency state, message broadcasting, acknowledgements,
    proximity alerts, and interaction with the Meshtastic interface.

    Attributes:
        interface: The Meshtastic interface object.
        config (ConfigManager): The configuration manager instance.
        emergency_active (bool): True if the emergency broadcast is currently active.
        last_emergency_id (str | None): The UUID of the most recent emergency session started by this node.
        acknowledgements (dict): Stores acknowledgements received for emergencies initiated by this node.
                                  Format: { emergency_id: {acked_node_num: timestamp} }
        active_emergency_info (dict): Stores info about *active* emergencies received from *other* nodes.
                                      Format: { sender_node_num: {message_id, timestamp, message, gps, battery} }
        my_node_num (int | None): The Meshtastic node number of this device.
        my_node_id (str): The formatted node ID string (e.g., "!aabbccdd") of this device.
    """
    def __init__(self, interface, config_manager: ConfigManager):
        """
        Initializes the AERP plugin instance.

        Args:
            interface: The initialized Meshtastic interface object.
            config_manager (ConfigManager): The configuration manager instance.
        """
        self.interface = interface
        self.config = config_manager
        self.emergency_active = False
        self._emergency_thread = None # Internal thread reference
        self._emergency_lock = threading.Lock() # Protects access to emergency_active and related state
        self.last_emergency_id = None
        self.last_sent_emergency_id = None
        self.acknowledgements = {}
        self.active_emergency_info = {}
        self.my_node_num = None
        self.my_node_id = "Unknown"

        # Attempt to get initial node info
        self._update_node_info()
        if not self.my_node_num:
             logger.warning("Could not get initial node info. Will retry on connection.")

        # Start background task for cleaning up stale data
        # Use daemon=True so this thread doesn't prevent program exit
        self._cleanup_thread = threading.Thread(target=self._background_cleanup, daemon=True, name="AERPCleanupThread")
        self._cleanup_thread.start()
        logger.debug("AERP background cleanup thread started.")

    def _update_node_info(self):
        """Attempts to update my_node_num and my_node_id from the interface."""
        try:
            # Access myInfo directly from the interface object
            if self.interface and hasattr(self.interface, 'myInfo') and self.interface.myInfo:
                self.my_node_num = self.interface.myInfo.my_node_num
                self.my_node_id = format_node_id(self.my_node_num)
                logger.info(f"AERP Initialized/Updated for node {self.my_node_id} ({self.my_node_num})")
                return True
            else:
                # This case might happen if the interface is connected but info isn't populated yet
                logger.debug("Node info not yet available from interface.myInfo.")
                return False
        except AttributeError as e:
            logger.error(f"Error accessing node info from interface: {e}. Is the device connected and responsive?")
            self.my_node_num = None
            self.my_node_id = "Unknown"
            return False
        except Exception as e:
            logger.exception(f"Unexpected error getting node info: {e}")
            self.my_node_num = None
            self.my_node_id = "Unknown"
            return False

    def start_emergency(self):
        """
        Initiates the emergency broadcast state for this node.

        Generates a new unique ID for this emergency session and starts the
        broadcast thread.

        Returns:
            bool: True if the emergency state was successfully started, False otherwise.
        """
        with self._emergency_lock:
            if self.emergency_active:
                logger.warning("Emergency broadcast is already active.")
                return False

            # Ensure we have node info before starting
            if not self.my_node_num:
                logger.error("Cannot start emergency: My node ID is unknown. Ensure device is connected.")
                # Attempt to update info again just in case
                if not self._update_node_info():
                     return False

            # Generate a unique ID for this specific emergency event
            self.last_emergency_id = str(uuid.uuid4())
            self.emergency_active = True
            # Initialize the acknowledgement dictionary for this new emergency ID
            self.acknowledgements[self.last_emergency_id] = {}
            self.last_sent_emergency_id = self.last_emergency_id

            logger.warning(f"--- EMERGENCY BROADCAST STARTED (ID: {self.last_emergency_id}) ---")
            logger.info(f"Broadcasting on port {self.config.get(CONFIG_PORT)} every {self.config.get(CONFIG_INTERVAL)} seconds.")

            # Start the broadcast thread
            if self._emergency_thread is None or not self._emergency_thread.is_alive():
                self._emergency_thread = threading.Thread(target=self._send_emergency_broadcast_loop, name="AERPBroadcastThread")
                self._emergency_thread.start()
            else:
                 logger.warning("Emergency thread already running? This shouldn't happen.")

            return True

    def stop_emergency(self, send_clear=True):
        """
        Stops the active emergency broadcast state for this node.

        Optionally sends an 'All Clear' message for the stopped session.

        Args:
            send_clear (bool): If True, attempt to send an AERP_CLEAR message
                               for the stopped emergency ID. Defaults to True.

        Returns:
            bool: True if an emergency was active and is now stopped, False otherwise.
        """
        was_active = False
        emergency_id_to_clear = None

        with self._emergency_lock:
            if self.emergency_active:
                was_active = True
                self.emergency_active = False # Signal the thread to stop
                emergency_id_to_clear = self.last_emergency_id # Store ID before clearing
                self.last_emergency_id = None # Clear the active ID immediately
                logger.warning(f"--- EMERGENCY BROADCAST STOPPING (Last ID: {emergency_id_to_clear}) ---")
            else:
                logger.info("Emergency broadcast is not currently active.")
                return False # Nothing to stop

        # Wait for the broadcast thread to finish its current loop and exit
        if self._emergency_thread and self._emergency_thread.is_alive():
            logger.debug("Waiting for broadcast thread to finish...")
            # Calculate a reasonable timeout based on interval
            timeout_duration = self.config.get(CONFIG_INTERVAL, 10) + 2 # Interval + buffer
            self._emergency_thread.join(timeout=timeout_duration)
            if self._emergency_thread.is_alive():
                logger.warning("Emergency broadcast thread did not stop gracefully within timeout.")
            else:
                 logger.debug("Broadcast thread finished.")
            self._emergency_thread = None

        # Send the clear message if requested and possible
        if was_active and send_clear and emergency_id_to_clear:
            if self.my_node_num: # Check if we can send
                 self.send_clear_message(emergency_id_to_clear)
            else:
                 logger.warning("Cannot send CLEAR message: Node info unknown (device likely disconnected).")
        elif was_active and send_clear and not emergency_id_to_clear:
             logger.warning("Wanted to send CLEAR, but no emergency ID was recorded.")


        # Optional: Clear acknowledgements for the stopped session? Or keep for review?
        # Decision: Keep them for review via status command until they time out naturally.
        # if emergency_id_to_clear in self.acknowledgements:
        #     logger.debug(f"Clearing acknowledgements for stopped emergency ID {emergency_id_to_clear}")
        #     del self.acknowledgements[emergency_id_to_clear]

        return was_active

    def send_clear_message(self, emergency_id):
        """
        Sends an 'All Clear' message for a specific emergency ID.

        This informs other nodes that the situation related to that ID is resolved.

        Args:
            emergency_id (str): The unique ID of the emergency session to clear.
        """
        if not self.my_node_num:
            logger.error("Cannot send clear message: My node ID is unknown.")
            return
        if not emergency_id:
            logger.error("Cannot send clear message: No emergency ID provided.")
            return

        message_payload = {
            "type": MSG_TYPE_CLEAR,
            "user_node_num": self.my_node_num, # Include sender node number
            "emergency_id": emergency_id,      # The ID being cleared
            "timestamp": time.time()           # Time the clear was sent
        }
        port_num = self.config.get(CONFIG_PORT)

        logger.info(f"Sending ALL CLEAR for emergency ID {emergency_id} on port {port_num}")
        logger.debug(f"Clear Payload: {message_payload}")

        try:
            # Send as a broadcast message on the designated AERP port
            self.interface.sendData(
                payload=message_payload,
                portNum=port_num,
                wantAck=False # Clear messages are typically fire-and-forget
                # destinationId="^all" # Default broadcast behavior
            )
        except TypeError as e:
             logger.error(f"TypeError sending CLEAR message (check meshtastic library version?): {e}")
             # Try sending as bytes (JSON encoded)
             try:
                  self.interface.sendData(
                       payload=json.dumps(message_payload).encode('utf-8'),
                       portNum=port_num,
                       wantAck=False
                  )
             except Exception as inner_e:
                  logger.error(f"Retry sending CLEAR as bytes also failed: {inner_e}")
        except AttributeError as e:
             logger.error(f"Meshtastic interface error sending CLEAR: {e}. Is it connected?")
        except Exception as e:
            # Catch other potential errors during send
            logger.exception(f"Failed to send CLEAR message: {e}")

    def _send_emergency_broadcast_loop(self):
        """
        Internal method run in a dedicated thread.
        Periodically gathers data (GPS, battery) and sends the emergency broadcast message
        as long as `self.emergency_active` is True.
        """
        interval = self.config.get(CONFIG_INTERVAL)
        port_num = self.config.get(CONFIG_PORT)
        emergency_msg_text = self.config.get(CONFIG_MESSAGE)
        current_emergency_id = None # Store the ID for this thread's session

        with self._emergency_lock:
             current_emergency_id = self.last_emergency_id # Get ID safely

        if not current_emergency_id:
             logger.error("Broadcast thread started without a valid emergency ID. Exiting.")
             return

        logger.debug(f"Broadcast thread started for emergency ID: {current_emergency_id}")

        while True:
            # Check if emergency should stop *before* doing work
            with self._emergency_lock:
                if not self.emergency_active or self.last_emergency_id != current_emergency_id:
                    logger.debug(f"Emergency state changed (active={self.emergency_active}, id={self.last_emergency_id}). Broadcast thread exiting.")
                    break # Exit loop if emergency stopped or ID changed

            # --- Gather Data ---
            gps_info = {} # Default to empty dict
            battery_level = None # Default to None

            try:
                # Attempt to get current position from node info (less blocking)
                # Ensure interface and myInfo are valid
                if self.interface and hasattr(self.interface, 'myInfo') and self.interface.myInfo:
                    my_pos = self.interface.myInfo.position
                    if my_pos and isinstance(my_pos, dict):
                         # Check for standard integer lat/lon first
                         if 'latitudeI' in my_pos and 'longitudeI' in my_pos:
                              lat = my_pos['latitudeI'] / 1e7
                              lon = my_pos['longitudeI'] / 1e7
                              # Add other available fields if they exist
                              gps_info = {
                                   "latitude": lat,
                                   "longitude": lon,
                                   "altitude": my_pos.get('altitude'),
                                   "time": my_pos.get('time'), # GPS timestamp
                              }
                         # Fallback check for float lat/lon (less common now)
                         elif 'latitude' in my_pos and 'longitude' in my_pos:
                              gps_info = {
                                   "latitude": my_pos['latitude'],
                                   "longitude": my_pos['longitude'],
                                   "altitude": my_pos.get('altitude'),
                                   "time": my_pos.get('time'),
                              }

                if not gps_info: # If position wasn't found in myInfo
                     # Optional: Could try a more direct/blocking call if essential,
                     # but prefer non-blocking info for responsiveness.
                     # gps_data = self.interface.localNode.getGps() # Requires localNode setup
                     logger.warning("Could not get valid GPS position for emergency message.")

            except AttributeError:
                 logger.warning("Could not access interface.myInfo.position. Is node info available?")
            except Exception as e:
                # Catch unexpected errors during GPS fetch
                logger.error(f"Error getting GPS data: {e}")

            try:
                # Attempt to get battery level from node info
                if self.interface and hasattr(self.interface, 'myInfo') and self.interface.myInfo:
                    metrics = self.interface.myInfo.device_metrics
                    if metrics and isinstance(metrics, dict) and 'batteryLevel' in metrics:
                        battery_level = metrics['batteryLevel']

                if battery_level is None:
                     # Optional: Direct call fallback
                     # battery_level = self.interface.localNode.getDeviceMetrics().batteryLevel
                     logger.warning("Could not get battery level for emergency message.")

            except AttributeError:
                 logger.warning("Could not access interface.myInfo.device_metrics. Is node info available?")
            except Exception as e:
                logger.error(f"Error getting battery level: {e}")


            # --- Construct and Send Payload ---
            message_payload = {
                "type": MSG_TYPE_EMERGENCY,
                "user_node_num": self.my_node_num, # Include sender node number for identification
                "emergency_id": current_emergency_id, # Include the unique ID for this session
                "message": emergency_msg_text,
                "gps": gps_info, # Send collected GPS data (or empty dict)
                "battery": battery_level, # Send collected battery level (or None)
                "timestamp": time.time() # System time of sending
            }

            logger.info(f"Sending emergency broadcast (ID: {current_emergency_id}) on port {port_num}")
            logger.debug(f"Emergency Payload: {message_payload}")

            try:
                # Send the data using the Meshtastic interface
                self.interface.sendData(
                    payload=message_payload,
                    portNum=port_num,
                    # wantAck=False # Default, ACKs are handled by the plugin logic
                    # channelIndex=0 # Specify channel if needed
                )
            except TypeError as e:
                 logger.error(f"TypeError sending EMERGENCY message: {e}")
                 # Try sending as bytes
                 try:
                      self.interface.sendData(payload=json.dumps(message_payload).encode('utf-8'), portNum=port_num)
                 except Exception as inner_e:
                      logger.error(f"Retry sending EMERGENCY as bytes also failed: {inner_e}")
            except AttributeError as e:
                 logger.error(f"Meshtastic interface error sending EMERGENCY: {e}. Is it connected?")
            except Exception as e:
                logger.exception(f"Failed to send emergency broadcast: {e}")

            # --- Wait for Interval ---
            # Check the active flag *again* immediately before sleeping
            # to handle rapid start/stop commands gracefully.
            with self._emergency_lock:
                if not self.emergency_active or self.last_emergency_id != current_emergency_id:
                    logger.debug("Emergency state changed during send cycle. Exiting loop.")
                    break
            # Wait for the configured interval before the next broadcast
            time.sleep(interval)

        logger.info(f"Emergency broadcast thread finished for ID: {current_emergency_id}.")


    # --- Incoming Message Handling ---

    def handle_incoming(self, packet, interface):
        """
        Processes incoming packets received from the Meshtastic network.

        This method is intended to be called by the Meshtastic receive callback.
        It decodes the packet, determines the message type, and routes it to
        the appropriate handler (_handle_emergency_message, _handle_ack_message, etc.).
        It also checks packets for location data to trigger proximity alerts.

        Args:
            packet (dict): The packet dictionary received from meshtastic-python.
            interface: The Meshtastic interface instance that received the packet.
                       (Note: Often the same as self.interface, but passed for context).
        """
        try:
            # Basic packet validation
            if not isinstance(packet, dict) or 'decoded' not in packet or 'payload' not in packet['decoded']:
                # logger.debug("Packet missing decoded payload, ignoring.")
                return

            decoded_part = packet['decoded']
            port_num = decoded_part.get('portNum') # Can be int or string ('UNKNOWN_APP', 'TEXT_MESSAGE_APP', etc.)
            payload = decoded_part.get('payload') # Can be bytes, dict (if auto-decoded JSON), string etc.
            from_node_num = packet.get('from')
            to_node_num = packet.get('to') # Useful for checking if message was direct or broadcast

            # Ignore packets sent by ourselves
            if from_node_num == self.my_node_num:
                # logger.debug(f"Ignoring packet from self ({format_node_id(from_node_num)})")
                return

            from_node_id_fmt = format_node_id(from_node_num) # Formatted ID for logging

            # --- Attempt to Decode Payload if Bytes ---
            # Meshtastic library sometimes provides payload as bytes, try decoding as JSON
            decoded_payload = None
            if isinstance(payload, bytes):
                try:
                    decoded_payload = json.loads(payload.decode('utf-8'))
                    logger.debug(f"Successfully decoded JSON payload from bytes from {from_node_id_fmt}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    # Not valid JSON or not UTF-8 text, treat as raw bytes or ignore
                    logger.debug(f"Payload from {from_node_id_fmt} on port {port_num} is bytes but not valid JSON/UTF-8.")
                    # We might still want to process based on portnum below, even if payload isn't JSON
            elif isinstance(payload, dict):
                 decoded_payload = payload # Already a dictionary
            # Add handling for other payload types (like plain text) if needed

            # --- Route based on Port Number and Message Type ---
            target_port = self.config.get(CONFIG_PORT)

            # 1. Check if it's on the AERP Port
            if port_num == target_port:
                if isinstance(decoded_payload, dict):
                    message_type = decoded_payload.get("type")
                    if message_type == MSG_TYPE_EMERGENCY:
                        self._handle_emergency_message(packet, decoded_payload, from_node_num, from_node_id_fmt)
                    elif message_type == MSG_TYPE_ACK:
                        self._handle_ack_message(packet, decoded_payload, from_node_num, from_node_id_fmt)
                    elif message_type == MSG_TYPE_CLEAR:
                        self._handle_clear_message(packet, decoded_payload, from_node_num, from_node_id_fmt)
                    # Add handlers for other AERP message types here
                    else:
                        logger.debug(f"Received message with unknown type '{message_type}' on AERP port {target_port} from {from_node_id_fmt}.")
                else:
                    # Received something on AERP port, but it's not a recognized AERP JSON structure
                    logger.info(f"Received non-AERP (or non-JSON) data on AERP port {target_port} from {from_node_id_fmt}. Payload: {payload}")

            # 2. Check *any* packet for Position Data (for Proximity Alert)
            # This allows alerts even if nodes aren't running AERP but are sending standard position updates.
            lat, lon = get_location_from_packet(packet) # Util function checks POSITION_APP and embedded 'gps'
            if lat is not None and lon is not None:
                 # We already ignored packets from self earlier
                 self.check_alert_radius(packet, lat, lon, from_node_num, from_node_id_fmt)

        except Exception as e:
            # Catch-all for unexpected errors during packet processing
            packet_id = packet.get('id', 'N/A') if isinstance(packet, dict) else 'InvalidPacket'
            logger.exception(f"Error processing incoming packet ID {packet_id}: {e}")

    def _handle_emergency_message(self, packet, payload, from_node_num, from_node_id_fmt):
        """Handles a received AERP_EMERGENCY message."""
        emergency_id = payload.get("emergency_id")
        message_text = payload.get("message", "[No message text]")
        gps_info = payload.get("gps") # This should be a dict if present
        battery_level = payload.get("battery") # Integer or None
        timestamp = payload.get("timestamp", time.time()) # Use receive time if sender didn't include

        # Log the emergency prominently
        logger.warning(f"*** EMERGENCY MESSAGE RECEIVED from {from_node_id_fmt} (ID: {emergency_id}) ***")
        logger.warning(f"    Message: {message_text}")
        if isinstance(gps_info, dict) and 'latitude' in gps_info and 'longitude' in gps_info:
             logger.warning(f"    GPS: Lat {gps_info['latitude']:.5f}, Lon {gps_info['longitude']:.5f} (Alt: {gps_info.get('altitude', 'N/A')})")
        else:
             logger.warning(f"    GPS: Not Available or Invalid Format")
        logger.warning(f"    Battery: {battery_level if battery_level is not None else 'N/A'}%")
        try:
            # Format timestamp for readability
            log_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S %Z')
            logger.warning(f"    Timestamp: {log_time}")
        except Exception:
             logger.warning(f"    Timestamp: {timestamp} (Could not format)")


        # Store info about this active emergency (overwrite if already present for this node)
        self.active_emergency_info[from_node_num] = {
            "message_id": emergency_id,
            "timestamp": timestamp, # Store the original timestamp
            "message": message_text,
            "gps": gps_info,
            "battery": battery_level,
            "last_seen": time.time() # Track when we last heard from them
        }

        # Send acknowledgement back to the sender
        if emergency_id:
            self.send_acknowledgement(from_node_num, emergency_id)
        else:
            logger.warning("Received emergency message without an ID, cannot acknowledge specifically.")

    def _handle_ack_message(self, packet, payload, from_node_num, from_node_id_fmt):
        """Handles a received AERP_ACK message."""
        original_emergency_id = payload.get("emergency_id")
        ack_timestamp = payload.get("timestamp", time.time()) # Sender's timestamp of ACK
        # The node number of the device sending the ACK is 'from_node_num'
        ack_sender_node_num = from_node_num # Clarify variable name

        # Check if this ACK is for an emergency *we* initiated
        if original_emergency_id and original_emergency_id in self.acknowledgements:
            # Record the ACK with the sender's node ID and timestamp
            # Check if we already have an ACK from this node for this ID
            if ack_sender_node_num not in self.acknowledgements[original_emergency_id]:
                logger.info(f"Acknowledgement RECEIVED for My Emergency ID {original_emergency_id} from Node {from_node_id_fmt}")
            else:
                # We received another ACK from the same node for the same emergency
                # Update the timestamp (they might have restarted or resent)
                logger.debug(f"Acknowledgement REFRESHED for My Emergency ID {original_emergency_id} from Node {from_node_id_fmt}")

            # Store the timestamp of when the ACK was *sent* by the acknowledging node
            self.acknowledgements[original_emergency_id][ack_sender_node_num] = ack_timestamp
        else:
            # This could be an ACK for another node's emergency, or an old/invalid ID.
            logger.debug(f"Received ACK from {from_node_id_fmt} for emergency {original_emergency_id}, which is not mine or is unknown/stale.")

    def _handle_clear_message(self, packet, payload, from_node_num, from_node_id_fmt):
        """Handles a received AERP_CLEAR message."""
        emergency_id = payload.get("emergency_id")
        timestamp = payload.get("timestamp", time.time()) # Time the clear was sent

        logger.info(f"--- ALL CLEAR RECEIVED from {from_node_id_fmt} for Emergency ID: {emergency_id} ---")

        # Remove the emergency info we were tracking for this sender node
        if from_node_num in self.active_emergency_info:
            # Optional: Check if the emergency_id matches the one we stored for this node
            stored_id = self.active_emergency_info[from_node_num].get("message_id")
            if stored_id == emergency_id:
                logger.debug(f"Removing tracked emergency info for node {from_node_id_fmt} matching CLEAR ID.")
            elif stored_id:
                 logger.warning(f"Received CLEAR from {from_node_id_fmt} with ID {emergency_id}, but tracked ID was {stored_id}. Removing tracked info anyway.")
            else:
                 logger.debug(f"Received CLEAR from {from_node_id_fmt} (ID: {emergency_id}). Removing tracked info (which had no ID).")

            del self.active_emergency_info[from_node_num]
        else:
            # We received a clear, but weren't tracking an active emergency from them.
            logger.info(f"Received CLEAR for node {from_node_id_fmt} (ID: {emergency_id}), but no active emergency was tracked for them.")

        # Do NOT clear acknowledgements here. ACKs relate to emergencies *we* sent.
        # Clearing is only relevant for `active_emergency_info` which tracks emergencies *from others*.

    def send_acknowledgement(self, destination_node_num, emergency_id):
        """
        Sends an acknowledgement (AERP_ACK) message for a specific emergency ID
        directly back to the node that sent the emergency message.

        Args:
            destination_node_num (int): The node number to send the ACK to.
            emergency_id (str): The unique ID of the emergency being acknowledged.
        """
        if not self.my_node_num:
            logger.error("Cannot send acknowledgement: My node ID is unknown.")
            return
        if not destination_node_num:
            logger.error("Cannot send acknowledgement: Destination node number is missing.")
            return
        if not emergency_id:
             logger.error("Cannot send acknowledgement: Emergency ID is missing.")
             return

        ack_payload = {
            "type": MSG_TYPE_ACK,
            "emergency_id": emergency_id,      # The ID being acknowledged
            # "ack_sender_node_num": self.my_node_num, # Implicitly the 'from' field, but can include for clarity if needed
            "timestamp": time.time()           # Time the ACK is being sent
            # Could add optional fields like ACK sender's GPS/Battery here if useful for context
        }
        port_num = self.config.get(CONFIG_PORT)
        dest_node_id_fmt = format_node_id(destination_node_num)

        logger.info(f"Sending ACK to {dest_node_id_fmt} for Emergency ID {emergency_id} on port {port_num}")
        logger.debug(f"ACK Payload: {ack_payload}")

        try:
            # Send directly to the node that sent the emergency
            # Format destination ID string correctly for sendData
            destination_id_str = f"!{destination_node_num:08x}"

            self.interface.sendData(
                payload=ack_payload,
                destinationId=destination_id_str,
                portNum=port_num,
                wantAck=False # ACKs usually don't need their own ACK (prevents ACK loops)
            )
        except TypeError as e:
             logger.error(f"TypeError sending ACK message to {dest_node_id_fmt}: {e}")
             # Try sending as bytes
             try:
                  self.interface.sendData(
                       payload=json.dumps(ack_payload).encode('utf-8'),
                       destinationId=destination_id_str,
                       portNum=port_num,
                       wantAck=False
                  )
             except Exception as inner_e:
                  logger.error(f"Retry sending ACK as bytes also failed: {inner_e}")
        except AttributeError as e:
             logger.error(f"Meshtastic interface error sending ACK: {e}. Is it connected?")
        except Exception as e:
            logger.exception(f"Failed to send ACK to {dest_node_id_fmt}: {e}")


    # --- Proximity Alert ---

    def check_alert_radius(self, packet, lat, lon, from_node_num, from_node_id_fmt):
        """
        Checks if the node identified in the packet, located at (lat, lon),
        is within the configured alert radius of this node. Logs a warning if true.

        Args:
            packet (dict): The received packet (used for context, maybe packet ID).
            lat (float): Latitude of the other node.
            lon (float): Longitude of the other node.
            from_node_num (int): Node number of the other node.
            from_node_id_fmt (str): Formatted node ID string of the other node.
        """
        alert_radius = self.config.get(CONFIG_RADIUS)
        # Only proceed if alert radius is enabled (positive value)
        if alert_radius <= 0:
            return

        my_lat, my_lon = None, None
        try:
            # Get my current position
            if self.interface and hasattr(self.interface, 'myInfo') and self.interface.myInfo:
                my_pos = self.interface.myInfo.position
                if my_pos and isinstance(my_pos, dict):
                    if 'latitudeI' in my_pos and 'longitudeI' in my_pos:
                        my_lat = my_pos['latitudeI'] / 1e7
                        my_lon = my_pos['longitudeI'] / 1e7
                    elif 'latitude' in my_pos and 'longitude' in my_pos: # Fallback
                        my_lat = my_pos['latitude']
                        my_lon = my_pos['longitude']

            if my_lat is None or my_lon is None:
                logger.debug("Cannot check alert radius: My current location is unknown.")
                return

        except AttributeError:
             logger.warning("Could not access my position (interface.myInfo.position) for alert check.")
             return
        except Exception as e:
            logger.error(f"Could not get my own position for alert radius check: {e}")
            return # Cannot check distance without own position

        # Calculate distance using the utility function
        distance = calculate_distance(my_lat, my_lon, lat, lon)

        if distance == float('inf'):
             logger.debug(f"Distance calculation failed for node {from_node_id_fmt}.")
             return # Calculation failed

        logger.debug(f"Calculated distance to node {from_node_id_fmt}: {distance:.2f}m")

        # Check if within radius and log alert
        if distance <= alert_radius:
            # Potential enhancement: Keep track of nodes already alerted recently
            # to avoid spamming logs for nodes lingering on the edge.
            # e.g., self.recently_alerted[from_node_num] = time.time()
            logger.warning(f"*** PROXIMITY ALERT: Node {from_node_id_fmt} is within alert radius ({distance:.1f}m <= {alert_radius}m) ***")
            # Trigger further actions if needed (e.g., sound alarm, display notification via another mechanism)
        # else:
            # Optional: Log when a node moves *out* of the radius if tracking state
            # logger.info(f"Node {from_node_id_fmt} is outside alert radius ({distance:.2f}m > {alert_radius}m)")


    # --- Background Cleanup ---

    def _background_cleanup(self):
        """
        Internal method run in a dedicated thread.
        Periodically cleans up stale data:
        - Acknowledgements for *our* old/inactive emergencies.
        - Information about *received* emergencies that haven't been updated recently.
        """
        logger.info("AERP Cleanup thread starting.")
        while True:
            ack_timeout = self.config.get(CONFIG_ACK_TIMEOUT)
            # Use a potentially longer timeout for inactive received emergencies
            # Consider making this configurable as well?
            received_emergency_timeout = max(ack_timeout * 3, 600) # At least 10 minutes

            # --- Wait first before cleaning ---
            # Sleep interval: Check roughly twice per ACK timeout period, but not too frequently.
            sleep_duration = max(30, ack_timeout // 2)
            time.sleep(sleep_duration)

            logger.debug(f"Running background cleanup (ACK Timeout: {ack_timeout}s, Received Timeout: {received_emergency_timeout}s)")
            current_time = time.time()

            try:
                # --- Clean stale acknowledgements for *our* emergencies ---
                # Use list() to avoid issues modifying dict during iteration
                stale_acks_found = False
                for emergency_id, nodes in list(self.acknowledgements.items()):
                    # Also remove ACK lists for emergencies that are no longer active *and* old
                    # (Requires knowing which IDs are truly old, not just inactive)
                    # Simpler: Just remove stale ACKs within each list based on timestamp.
                    acks_to_remove = []
                    for node_num, timestamp in list(nodes.items()):
                        if current_time - timestamp > ack_timeout:
                            acks_to_remove.append(node_num)
                            stale_acks_found = True

                    if acks_to_remove:
                        logger.debug(f"Cleaning stale ACKs for Emergency ID {emergency_id}...")
                        for node_num in acks_to_remove:
                            if emergency_id in self.acknowledgements and node_num in self.acknowledgements[emergency_id]:
                                del self.acknowledgements[emergency_id][node_num]
                                logger.debug(f" - Removed stale ACK from {format_node_id(node_num)}")
                        # Optional: Remove the emergency_id key itself if no ACKs remain?
                        # if not self.acknowledgements[emergency_id]:
                        #     logger.debug(f"Removing empty ACK list for Emergency ID {emergency_id}")
                        #     del self.acknowledgements[emergency_id]


                # --- Clean stale *received* emergency info ---
                stale_emergencies_found = False
                nodes_to_remove = []
                for node_num, info in list(self.active_emergency_info.items()):
                    # Check based on when we last saw *any* message from them related to this
                    last_seen_time = info.get("last_seen", info.get("timestamp", 0)) # Use 'last_seen' if available
                    if current_time - last_seen_time > received_emergency_timeout:
                        nodes_to_remove.append(node_num)
                        stale_emergencies_found = True

                if nodes_to_remove:
                    logger.debug(f"Cleaning stale received emergency info (timeout: {received_emergency_timeout}s)...")
                    for node_num in nodes_to_remove:
                        if node_num in self.active_emergency_info:
                            del self.active_emergency_info[node_num]
                            logger.info(f"Removed stale tracked emergency info for node {format_node_id(node_num)}")

                if not stale_acks_found and not stale_emergencies_found:
                    logger.debug("Background cleanup ran, no stale data found.")

            except Exception as e:
                # Log errors in the cleanup thread but keep the thread running
                logger.exception("Error during AERP background cleanup task.")

        # This part of the loop should ideally not be reached if daemon=True
        # logger.info("AERP Cleanup thread stopping.")


    # --- Status and Connection Handling ---

    def get_status(self):
        """
        Returns a dictionary containing the current status of the AERP plugin.

        Includes active state, last emergency ID, received acknowledgements,
        and information about active emergencies received from others.

        Returns:
            dict: A dictionary summarizing the plugin's status.
        """
        # Create copies or format data to avoid returning internal mutable state directly
        formatted_acks = {}
        current_active_id = self.last_emergency_id # Get potentially active ID

        # Format acknowledgements for the *currently active* emergency ID if one exists
        if current_active_id and current_active_id in self.acknowledgements:
             formatted_acks[current_active_id] = {
                  format_node_id(n): datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                  for n, ts in self.acknowledgements[current_active_id].items()
             }
        # Optionally include ACKs for past IDs? For now, focus on current.

        formatted_received_emergencies = {}
        for node_num, info in self.active_emergency_info.items():
            formatted_received_emergencies[format_node_id(node_num)] = {
                "emergency_id": info.get("message_id"),
                "message": info.get("message"),
                "gps": info.get("gps"),
                "battery": info.get("battery"),
                "received_at": datetime.fromtimestamp(info.get("timestamp", 0)).strftime('%Y-%m-%d %H:%M:%S'),
                "last_seen": datetime.fromtimestamp(info.get("last_seen", 0)).strftime('%Y-%m-%d %H:%M:%S')
            }

        status = {
            "my_node_id": self.my_node_id,
            "emergency_active": self.emergency_active,
            "last_emergency_id": current_active_id,
            "active_acknowledgements": formatted_acks.get(current_active_id, {}), # Show ACKs for current ID
            "active_received_emergencies": formatted_received_emergencies,
            "config": self.config.config # Show current config (might be verbose)
        }
        return status

    def on_connection_change(self, interface, connected):
        """
        Handles Meshtastic connection status changes.

        Intended to be called by the Meshtastic connection callback.
        Updates node info on connection and stops emergency broadcast on disconnection.

        Args:
            interface: The Meshtastic interface instance.
            connected (bool): True if the device connected, False if disconnected.
        """
        if connected:
            logger.info("Meshtastic device connected.")
            # Attempt to update node info, especially if it was unknown
            time.sleep(2) # Give the interface a moment to populate info after connect event
            self._update_node_info()
        else:
            logger.warning("Meshtastic device disconnected.")
            # Stop emergency broadcast if it was active, but don't try to send clear
            self.stop_emergency(send_clear=False)
            # Reset node info as it's no longer valid
            self.my_node_num = None
            self.my_node_id = "Unknown"
            logger.info("AERP node info reset due to disconnection.")

