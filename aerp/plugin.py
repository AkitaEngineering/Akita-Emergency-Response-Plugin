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
import base64
import binascii
import math
from datetime import datetime # For formatting timestamps
from typing import Any, Dict, Optional, Set, Tuple

from meshtastic.protobuf import portnums_pb2
from .constants import (
    MSG_TYPE_EMERGENCY, MSG_TYPE_ACK, MSG_TYPE_CLEAR,
    CONFIG_INTERVAL, CONFIG_PORT, CONFIG_MESSAGE, CONFIG_RADIUS, CONFIG_ACK_TIMEOUT,
    MAX_EMERGENCY_ID_CHARS, MAX_MESHTASTIC_PAYLOAD_BYTES, MAX_NODE_NUM,
    MAX_RECEIVED_MESSAGE_CHARS, CLEAR_BROADCAST_REPETITIONS,
    CLEAR_BROADCAST_SPACING_SECONDS,
)
from .utils import (
    build_gps_payload,
    calculate_distance,
    extract_coordinates,
    extract_battery_level,
    get_location_from_packet,
    format_node_id,
)
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
        self._emergency_thread: Optional[threading.Thread] = None
        self._emergency_stop_event: Optional[threading.Event] = None
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        # Kept as an alias for compatibility with integrations that inspected
        # the previous private attribute.
        self._emergency_lock = self._state_lock
        self._shutdown_event = threading.Event()
        self._closed = False
        self.last_emergency_id: Optional[str] = None
        self.last_sent_emergency_id: Optional[str] = None
        self.acknowledgements: Dict[str, Dict[int, float]] = {}
        self._emergency_created_at: Dict[str, float] = {}
        self.active_emergency_info: Dict[int, Dict[str, Any]] = {}
        self._cleared_emergencies: Dict[Tuple[int, str], float] = {}
        self._proximity_inside: Set[int] = set()
        self.my_node_num: Optional[int] = None
        self.my_node_id = "Unknown"

        # Attempt to get initial node info
        self._update_node_info()
        if self.my_node_num is None:
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
                node_num = self._normalize_node_num(self.interface.myInfo.my_node_num)
                if node_num is None:
                    raise ValueError("interface returned an invalid local node number")
                self.my_node_num = node_num
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

    def _get_local_node_record(self):
        """Return the local node record from Meshtastic's node database if available."""
        if not self.interface or not hasattr(self.interface, 'getMyNodeInfo'):
            return None

        try:
            node_info = self.interface.getMyNodeInfo()
            if isinstance(node_info, dict):
                return node_info
        except Exception as e:
            logger.debug(f"Could not load local node record from interface: {e}")

        return None

    def _get_local_position_payload(self):
        """Build the normalized GPS payload for this node from the node DB when available."""
        node_info = self._get_local_node_record()
        if isinstance(node_info, dict):
            gps_payload = build_gps_payload(node_info.get('position'))
            if gps_payload:
                return gps_payload

        legacy_position = getattr(getattr(self.interface, 'myInfo', None), 'position', None)
        return build_gps_payload(legacy_position)

    def _get_local_battery_level(self):
        """Return battery level from the local node DB record when telemetry is available."""
        node_info = self._get_local_node_record()
        if isinstance(node_info, dict):
            battery_level = extract_battery_level(node_info.get('deviceMetrics'))
            if battery_level is not None:
                return battery_level
            battery_level = extract_battery_level(node_info.get('device_metrics'))
            if battery_level is not None:
                return battery_level

        legacy_metrics = getattr(getattr(self.interface, 'myInfo', None), 'device_metrics', None)
        return extract_battery_level(legacy_metrics)

    def _configured_port_alias(self):
        """Return Meshtastic's enum alias for the configured port, if one exists."""
        try:
            return portnums_pb2.PortNum.Name(self.config.get(CONFIG_PORT))
        except (TypeError, ValueError):
            return None

    def _is_aerp_port(self, packet_port):
        """Check whether a received packet port matches the configured AERP port."""
        configured_port = self.config.get(CONFIG_PORT)
        if packet_port == configured_port or str(packet_port) == str(configured_port):
            return True
        configured_alias = self._configured_port_alias()
        return configured_alias is not None and packet_port == configured_alias

    def _decode_payload(self, payload, from_node_id_fmt, port_num):
        """Decode a Meshtastic data payload into a JSON dictionary when possible."""
        if isinstance(payload, dict):
            return payload

        if isinstance(payload, bytes):
            raw_payload = payload
        elif isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                try:
                    raw_payload = base64.b64decode(payload, validate=True)
                except (binascii.Error, ValueError):
                    logger.debug(
                        f"Payload from {from_node_id_fmt} on port {port_num} is not JSON and not base64-encoded bytes."
                    )
                    return None
        else:
            return None

        try:
            return json.loads(raw_payload.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.debug(f"Payload from {from_node_id_fmt} on port {port_num} is not valid UTF-8 JSON.")
            return None

    @staticmethod
    def _encode_payload(message_payload):
        return json.dumps(
            message_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def _payload_fits(cls, message_payload):
        try:
            return len(cls._encode_payload(message_payload)) <= MAX_MESHTASTIC_PAYLOAD_BYTES
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _normalize_node_num(value):
        try:
            if isinstance(value, bool):
                return None
            if isinstance(value, float) and not value.is_integer():
                return None
            normalized = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not 0 <= normalized <= MAX_NODE_NUM:
            return None
        return normalized

    @staticmethod
    def _valid_identifier(value):
        return (
            isinstance(value, str)
            and 0 < len(value) <= MAX_EMERGENCY_ID_CHARS
            and value == value.strip()
            and value.isprintable()
        )

    @staticmethod
    def _valid_timestamp(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )

    def _send_aerp_payload(self, message_payload, port_num, description, destination_id="^all", want_ack=False):
        """Send a JSON-encoded AERP payload using the Meshtastic sendData API."""
        if not self.interface:
            logger.error(f"Cannot send {description}: Meshtastic interface is unavailable.")
            return False

        try:
            encoded_payload = self._encode_payload(message_payload)
            if len(encoded_payload) > MAX_MESHTASTIC_PAYLOAD_BYTES:
                logger.error(
                    f"Cannot send {description}: encoded payload is {len(encoded_payload)} bytes; "
                    f"Meshtastic permits {MAX_MESHTASTIC_PAYLOAD_BYTES}."
                )
                return False
            self.interface.sendData(
                encoded_payload,
                destinationId=destination_id,
                portNum=port_num,
                wantAck=want_ack,
            )
            return True
        except (TypeError, ValueError) as e:
            logger.error(f"Cannot encode {description}: {e}")
        except AttributeError as e:
            logger.error(f"Meshtastic interface error sending {description}: {e}. Is it connected?")
        except Exception as e:
            logger.exception(f"Failed to send {description}: {e}")
        return False

    def start_emergency(self):
        """Start an emergency session and queue its first broadcast."""
        with self._lifecycle_lock:
            return self._start_emergency()

    def _start_emergency(self):
        """
        Initiates the emergency broadcast state for this node.

        Generates a new unique ID for this emergency session and starts the
        broadcast thread.

        Returns:
            bool: True if the emergency state was successfully started, False otherwise.
        """
        with self._state_lock:
            if self._closed:
                logger.error("Cannot start emergency: AERP has been closed.")
                return False
            if self.emergency_active:
                logger.warning("Emergency broadcast is already active.")
                return False

            # Ensure we have node info before starting
            if self.my_node_num is None:
                logger.error("Cannot start emergency: My node ID is unknown. Ensure device is connected.")
                # Attempt to update info again just in case
                if not self._update_node_info():
                     return False

            # Generate a unique ID for this specific emergency event
            previous_sent_emergency_id = self.last_sent_emergency_id
            self.last_emergency_id = str(uuid.uuid4())
            self.emergency_active = True
            # Initialize the acknowledgement dictionary for this new emergency ID
            self.acknowledgements[self.last_emergency_id] = {}
            self._emergency_created_at[self.last_emergency_id] = time.time()
            self.last_sent_emergency_id = self.last_emergency_id
            stop_event = threading.Event()
            self._emergency_stop_event = stop_event

            logger.warning(f"--- EMERGENCY BROADCAST STARTED (ID: {self.last_emergency_id}) ---")
            logger.info(f"Broadcasting on port {self.config.get(CONFIG_PORT)} every {self.config.get(CONFIG_INTERVAL)} seconds.")

            current_emergency_id = self.last_emergency_id

        with self._state_lock:
            if not self.emergency_active or self.last_emergency_id != current_emergency_id:
                return False
            # Serialize the first EMERGENCY against STOP/CLEAR so radio calls
            # cannot be queued in the wrong order.
            if not self._send_emergency_once(current_emergency_id):
                self.emergency_active = False
                self.last_emergency_id = None
                self.last_sent_emergency_id = previous_sent_emergency_id
                self.acknowledgements.pop(current_emergency_id, None)
                self._emergency_created_at.pop(current_emergency_id, None)
                stop_event.set()
                logger.error("Emergency broadcast was not started because the first transmission failed.")
                return False
            self._emergency_thread = threading.Thread(
                target=self._send_emergency_broadcast_loop,
                args=(current_emergency_id, stop_event),
                daemon=True,
                name="AERPBroadcastThread",
            )
            self._emergency_thread.start()
        return True

    def stop_emergency(self, send_clear=True):
        """Stop the current session, optionally attempting an ALL CLEAR."""
        with self._lifecycle_lock:
            return self._stop_emergency(send_clear=send_clear)

    def _stop_emergency(self, send_clear=True):
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

        with self._state_lock:
            if self.emergency_active:
                was_active = True
                self.emergency_active = False # Signal the thread to stop
                emergency_id_to_clear = self.last_emergency_id # Store ID before clearing
                self.last_emergency_id = None # Clear the active ID immediately
                stop_event = self._emergency_stop_event
                if stop_event:
                    stop_event.set()
                logger.warning(f"--- EMERGENCY BROADCAST STOPPING (Last ID: {emergency_id_to_clear}) ---")
            else:
                logger.info("Emergency broadcast is not currently active.")
                return False # Nothing to stop

        # Wait for the broadcast thread to finish its current loop and exit
        if self._emergency_thread and self._emergency_thread.is_alive():
            logger.debug("Waiting for broadcast thread to finish...")
            self._emergency_thread.join(timeout=2)
            if self._emergency_thread.is_alive():
                logger.warning("Emergency broadcast thread did not stop gracefully within timeout.")
            else:
                 logger.debug("Broadcast thread finished.")
            if not self._emergency_thread.is_alive():
                self._emergency_thread = None

        # Send the clear message if requested and possible
        if was_active and send_clear and emergency_id_to_clear:
            if self.my_node_num is not None:
                 if not self.send_clear_message(emergency_id_to_clear):
                     logger.error("Emergency stopped, but the ALL CLEAR transmission failed.")
            else:
                 logger.warning("Cannot send CLEAR message: Node info unknown (device likely disconnected).")
        elif was_active and send_clear and not emergency_id_to_clear:
             logger.warning("Wanted to send CLEAR, but no emergency ID was recorded.")


        return was_active

    def send_clear_message(self, emergency_id):
        """
        Sends an 'All Clear' message for a specific emergency ID.

        This informs other nodes that the situation related to that ID is resolved.

        Args:
            emergency_id (str): The unique ID of the emergency session to clear.
        """
        if self.my_node_num is None:
            logger.error("Cannot send clear message: My node ID is unknown.")
            return False
        if not self._valid_identifier(emergency_id):
            logger.error("Cannot send clear message: No emergency ID provided.")
            return False

        message_payload = {
            "type": MSG_TYPE_CLEAR,
            "emergency_id": emergency_id,      # The ID being cleared
            "timestamp": int(time.time())      # Time the clear was sent
        }
        port_num = self.config.get(CONFIG_PORT)

        logger.info(f"Sending ALL CLEAR for emergency ID {emergency_id} on port {port_num}")
        logger.debug(f"Clear Payload: {message_payload}")

        all_queued = True
        for attempt in range(CLEAR_BROADCAST_REPETITIONS):
            queued = self._send_aerp_payload(
                message_payload,
                port_num,
                description="CLEAR message",
                want_ack=False,
            )
            all_queued = all_queued and queued
            if attempt + 1 < CLEAR_BROADCAST_REPETITIONS:
                time.sleep(CLEAR_BROADCAST_SPACING_SECONDS)
        return all_queued

    def _build_emergency_payload(self, emergency_id):
        """Build a one-packet emergency payload in safety-priority order."""
        gps_info = self._get_local_position_payload()
        normalized_gps = {}
        if gps_info:
            normalized_gps = {
                "latitude": round(gps_info["latitude"], 7),
                "longitude": round(gps_info["longitude"], 7),
            }

        payload = {
            "type": MSG_TYPE_EMERGENCY,
            "emergency_id": emergency_id,
            "message": self.config.get(CONFIG_MESSAGE),
            "gps": normalized_gps,
        }
        if not self._payload_fits(payload):
            logger.error("Configured emergency message cannot fit with core AERP fields in one packet.")
            return None

        optional_fields = [
            ("timestamp", int(time.time())),
            ("battery", self._get_local_battery_level()),
        ]
        altitude = gps_info.get("altitude") if gps_info else None
        for key, value in optional_fields:
            if value is None:
                continue
            candidate = dict(payload)
            candidate[key] = value
            if self._payload_fits(candidate):
                payload = candidate

        if altitude is not None:
            candidate = dict(payload)
            candidate["gps"] = dict(payload["gps"], altitude=altitude)
            if self._payload_fits(candidate):
                payload = candidate
        return payload

    def _send_emergency_once(self, emergency_id):
        payload = self._build_emergency_payload(emergency_id)
        if payload is None:
            return False
        port_num = self.config.get(CONFIG_PORT)
        logger.info(f"Sending emergency broadcast (ID: {emergency_id}) on port {port_num}")
        logger.debug(f"Emergency Payload: {payload}")
        return self._send_aerp_payload(
            payload,
            port_num,
            description="EMERGENCY message",
            want_ack=False,
        )

    def _send_emergency_broadcast_loop(self, current_emergency_id, stop_event):
        """
        Internal method run in a dedicated thread.
        Periodically gathers data (GPS, battery) and sends the emergency broadcast message
        as long as `self.emergency_active` is True.
        """
        interval = self.config.get(CONFIG_INTERVAL)
        logger.debug(f"Broadcast thread started for emergency ID: {current_emergency_id}")
        while not stop_event.wait(interval):
            with self._state_lock:
                if not self.emergency_active or self.last_emergency_id != current_emergency_id:
                    break
                # Keep STOP/CLEAR ordered after any broadcast already in flight.
                self._send_emergency_once(current_emergency_id)

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
            if not isinstance(packet, dict) or not isinstance(packet.get('decoded'), dict):
                return

            decoded_part = packet['decoded']
            port_num = decoded_part.get('portnum', decoded_part.get('portNum')) # Can be int or string ('PRIVATE_APP', 'TEXT_MESSAGE_APP', etc.)
            payload = decoded_part.get('payload')
            from_node_num = self._normalize_node_num(packet.get('from'))
            if from_node_num is None:
                logger.debug("Ignoring packet with a missing or invalid sender node number.")
                return

            # Ignore packets sent by ourselves
            if from_node_num == self.my_node_num:
                return

            from_node_id_fmt = format_node_id(from_node_num) # Formatted ID for logging

            # --- Attempt to Decode Payload if Bytes ---
            # Meshtastic library sometimes provides payload as bytes, try decoding as JSON
            decoded_payload = self._decode_payload(payload, from_node_id_fmt, port_num) if payload is not None else None

            # --- Route based on Port Number and Message Type ---
            target_port = self.config.get(CONFIG_PORT)

            # 1. Check if it's on the AERP Port
            if self._is_aerp_port(port_num):
                if isinstance(decoded_payload, dict):
                    message_type = decoded_payload.get("type")
                    if message_type == MSG_TYPE_EMERGENCY:
                        self._handle_emergency_message(packet, decoded_payload, from_node_num, from_node_id_fmt)
                    elif message_type == MSG_TYPE_ACK:
                        self._handle_ack_message(packet, decoded_payload, from_node_num, from_node_id_fmt)
                    elif message_type == MSG_TYPE_CLEAR:
                        self._handle_clear_message(packet, decoded_payload, from_node_num, from_node_id_fmt)
                    else:
                        logger.debug(f"Received message with unknown type '{message_type}' on AERP port {target_port} from {from_node_id_fmt}.")
                else:
                    # Received something on AERP port, but it's not a recognized AERP JSON structure
                    payload_size = len(payload) if isinstance(payload, (bytes, str)) else "unknown"
                    logger.info(
                        f"Received non-AERP data on AERP port {target_port} from "
                        f"{from_node_id_fmt} (payload size: {payload_size})."
                    )

            # 2. Check *any* packet for Position Data (for Proximity Alert)
            # This allows alerts even if nodes aren't running AERP but are sending standard position updates.
            lat, lon = get_location_from_packet(packet) # Util function checks POSITION_APP and embedded 'gps'
            if (lat is None or lon is None) and isinstance(decoded_payload, dict):
                lat, lon = extract_coordinates(decoded_payload.get("gps"))
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
        if not self._valid_identifier(emergency_id):
            logger.warning(f"Ignoring malformed emergency from {from_node_id_fmt}: invalid emergency ID.")
            return

        message_text = payload.get("message", "[No message text]")
        if not isinstance(message_text, str):
            logger.warning(f"Ignoring malformed emergency from {from_node_id_fmt}: message is not text.")
            return
        if len(message_text) > MAX_RECEIVED_MESSAGE_CHARS:
            message_text = message_text[:MAX_RECEIVED_MESSAGE_CHARS]
            logger.warning(f"Truncated oversized emergency text received from {from_node_id_fmt}.")
        message_text = "".join(character if character.isprintable() else " " for character in message_text)

        gps_info = build_gps_payload(payload.get("gps"))
        battery_level = extract_battery_level({"batteryLevel": payload.get("battery")})
        received_at = time.time()
        sender_timestamp = payload.get("timestamp")
        if not self._valid_timestamp(sender_timestamp):
            sender_timestamp = None

        with self._state_lock:
            cleared_at = self._cleared_emergencies.get((from_node_num, emergency_id))
            current_info = self.active_emergency_info.get(from_node_num)
        if cleared_at is not None:
            logger.info(
                f"Ignoring delayed emergency {emergency_id} from {from_node_id_fmt}; "
                "an ALL CLEAR was already received."
            )
            return
        if current_info and current_info.get("message_id") != emergency_id:
            current_sender_timestamp = current_info.get("timestamp")
            if (
                sender_timestamp is not None
                and self._valid_timestamp(current_sender_timestamp)
                and sender_timestamp < current_sender_timestamp
            ):
                logger.info(
                    f"Ignoring out-of-order emergency {emergency_id} from {from_node_id_fmt}; "
                    f"newer incident {current_info.get('message_id')} is already active."
                )
                return

        # Log the emergency prominently
        logger.warning(f"*** EMERGENCY MESSAGE RECEIVED from {from_node_id_fmt} (ID: {emergency_id}) ***")
        logger.warning(f"    Message: {message_text}")
        if gps_info:
             logger.warning(f"    GPS: Lat {gps_info['latitude']:.5f}, Lon {gps_info['longitude']:.5f} (Alt: {gps_info.get('altitude', 'N/A')})")
        else:
             logger.warning(f"    GPS: Not Available or Invalid Format")
        logger.warning(f"    Battery: {battery_level if battery_level is not None else 'N/A'}%")
        try:
            # Format timestamp for readability
            display_timestamp = sender_timestamp if sender_timestamp is not None else received_at
            log_time = datetime.fromtimestamp(display_timestamp).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
            logger.warning(f"    Timestamp: {log_time}")
        except Exception:
             logger.warning(f"    Timestamp: {sender_timestamp} (Could not format)")


        # Store info about this active emergency (overwrite if already present for this node)
        with self._state_lock:
            self.active_emergency_info[from_node_num] = {
                "message_id": emergency_id,
                "timestamp": sender_timestamp,
                "message": message_text,
                "gps": gps_info,
                "battery": battery_level,
                "received_at": received_at,
                "last_seen": received_at,
            }

        # Send acknowledgement back to the sender
        if emergency_id:
            self.send_acknowledgement(from_node_num, emergency_id)
        else:
            logger.warning("Received emergency message without an ID, cannot acknowledge specifically.")

    def _handle_ack_message(self, packet, payload, from_node_num, from_node_id_fmt):
        """Handles a received AERP_ACK message."""
        original_emergency_id = payload.get("emergency_id")
        if not self._valid_identifier(original_emergency_id):
            logger.debug(f"Ignoring malformed ACK from {from_node_id_fmt}.")
            return
        ack_received_at = time.time()
        # The node number of the device sending the ACK is 'from_node_num'
        ack_sender_node_num = from_node_num # Clarify variable name

        # Check if this ACK is for an emergency *we* initiated
        with self._state_lock:
            known_emergency = original_emergency_id in self.acknowledgements
            already_received = (
                known_emergency
                and ack_sender_node_num in self.acknowledgements[original_emergency_id]
            )
            if known_emergency:
                self.acknowledgements[original_emergency_id][ack_sender_node_num] = ack_received_at

        if known_emergency:
            # Record the ACK with the sender's node ID and timestamp
            # Check if we already have an ACK from this node for this ID
            if not already_received:
                logger.info(f"Acknowledgement RECEIVED for My Emergency ID {original_emergency_id} from Node {from_node_id_fmt}")
            else:
                # We received another ACK from the same node for the same emergency
                # Update the timestamp (they might have restarted or resent)
                logger.debug(f"Acknowledgement REFRESHED for My Emergency ID {original_emergency_id} from Node {from_node_id_fmt}")

        else:
            # This could be an ACK for another node's emergency, or an old/invalid ID.
            logger.debug(f"Received ACK from {from_node_id_fmt} for emergency {original_emergency_id}, which is not mine or is unknown/stale.")

    def _handle_clear_message(self, packet, payload, from_node_num, from_node_id_fmt):
        """Handles a received AERP_CLEAR message."""
        emergency_id = payload.get("emergency_id")
        if not self._valid_identifier(emergency_id):
            logger.warning(f"Ignoring malformed ALL CLEAR from {from_node_id_fmt}: invalid emergency ID.")
            return
        received_at = time.time()

        logger.info(f"--- ALL CLEAR RECEIVED from {from_node_id_fmt} for Emergency ID: {emergency_id} ---")

        # Remove the emergency info we were tracking for this sender node
        with self._state_lock:
            self._cleared_emergencies[(from_node_num, emergency_id)] = received_at
            current_info = self.active_emergency_info.get(from_node_num)
            stored_id = current_info.get("message_id") if current_info else None
            if stored_id == emergency_id:
                del self.active_emergency_info[from_node_num]

        if current_info:
            if stored_id == emergency_id:
                logger.debug(f"Removing tracked emergency info for node {from_node_id_fmt} matching CLEAR ID.")
            else:
                 logger.warning(
                     f"Received CLEAR from {from_node_id_fmt} for ID {emergency_id}, "
                     f"but current tracked ID is {stored_id}; retaining the current emergency."
                 )
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
        if self.my_node_num is None:
            logger.error("Cannot send acknowledgement: My node ID is unknown.")
            return False
        destination_node_num = self._normalize_node_num(destination_node_num)
        if destination_node_num is None:
            logger.error("Cannot send acknowledgement: Destination node number is missing.")
            return False
        if not self._valid_identifier(emergency_id):
             logger.error("Cannot send acknowledgement: Emergency ID is missing.")
             return False

        ack_payload = {
            "type": MSG_TYPE_ACK,
            "emergency_id": emergency_id,      # The ID being acknowledged
            "timestamp": int(time.time())      # Time the ACK is being sent
        }
        port_num = self.config.get(CONFIG_PORT)
        dest_node_id_fmt = format_node_id(destination_node_num)

        logger.info(f"Sending ACK to {dest_node_id_fmt} for Emergency ID {emergency_id} on port {port_num}")
        logger.debug(f"ACK Payload: {ack_payload}")

        destination_id_str = f"!{destination_node_num:08x}"
        return self._send_aerp_payload(
            ack_payload,
            port_num,
            description=f"ACK message to {dest_node_id_fmt}",
            destination_id=destination_id_str,
            want_ack=True,
        )


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

        my_position = self._get_local_position_payload()
        my_lat = my_position.get('latitude') if my_position else None
        my_lon = my_position.get('longitude') if my_position else None
        if my_lat is None or my_lon is None:
            logger.debug("Cannot check alert radius: My current location is unknown.")
            return

        # Calculate distance using the utility function
        distance = calculate_distance(my_lat, my_lon, lat, lon)

        if distance == float('inf'):
             logger.debug(f"Distance calculation failed for node {from_node_id_fmt}.")
             return # Calculation failed

        logger.debug(f"Calculated distance to node {from_node_id_fmt}: {distance:.2f}m")

        # Check if within radius and log alert
        if distance <= alert_radius:
            with self._state_lock:
                newly_inside = from_node_num not in self._proximity_inside
                self._proximity_inside.add(from_node_num)
            if newly_inside:
                logger.warning(f"*** PROXIMITY ALERT: Node {from_node_id_fmt} is within alert radius ({distance:.1f}m <= {alert_radius}m) ***")
        else:
            with self._state_lock:
                was_inside = from_node_num in self._proximity_inside
                self._proximity_inside.discard(from_node_num)
            if was_inside:
                logger.info(
                    f"Node {from_node_id_fmt} left the alert radius "
                    f"({distance:.1f}m > {alert_radius}m)."
                )


    # --- Background Cleanup ---

    def _background_cleanup(self):
        """
        Internal method run in a dedicated thread.
        Periodically cleans up stale data:
        - Acknowledgements for *our* old/inactive emergencies.
        - Information about *received* emergencies that haven't been updated recently.
        """
        logger.info("AERP Cleanup thread starting.")
        while not self._shutdown_event.is_set():
            ack_timeout = self.config.get(CONFIG_ACK_TIMEOUT)
            received_emergency_timeout = max(ack_timeout * 3, 600) # At least 10 minutes

            # --- Wait first before cleaning ---
            # Sleep interval: Check roughly twice per ACK timeout period, but not too frequently.
            sleep_duration = max(30, ack_timeout // 2)
            if self._shutdown_event.wait(sleep_duration):
                break

            logger.debug(f"Running background cleanup (ACK Timeout: {ack_timeout}s, Received Timeout: {received_emergency_timeout}s)")
            current_time = time.time()

            try:
                # --- Clean stale acknowledgements for *our* emergencies ---
                # Use list() to avoid issues modifying dict during iteration
                stale_acks_found = False
                with self._state_lock:
                    acknowledgement_snapshot = {
                        emergency_id: dict(nodes)
                        for emergency_id, nodes in self.acknowledgements.items()
                    }
                    emergency_snapshot = {
                        node_num: dict(info)
                        for node_num, info in self.active_emergency_info.items()
                    }
                    cleared_snapshot = dict(self._cleared_emergencies)
                    emergency_created_snapshot = dict(self._emergency_created_at)
                    active_local_id = self.last_emergency_id

                for emergency_id, nodes in acknowledgement_snapshot.items():
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
                            with self._state_lock:
                                current_timestamp = self.acknowledgements.get(emergency_id, {}).get(node_num)
                                if current_timestamp != nodes[node_num]:
                                    continue
                                del self.acknowledgements[emergency_id][node_num]
                                logger.debug(f" - Removed stale ACK from {format_node_id(node_num)}")

                    created_at = emergency_created_snapshot.get(emergency_id, current_time)
                    if emergency_id != active_local_id and current_time - created_at > ack_timeout:
                        with self._state_lock:
                            if self._emergency_created_at.get(emergency_id) == created_at:
                                self.acknowledgements.pop(emergency_id, None)
                                self._emergency_created_at.pop(emergency_id, None)


                # --- Clean stale *received* emergency info ---
                stale_emergencies_found = False
                nodes_to_remove = []
                for node_num, info in emergency_snapshot.items():
                    # Check based on when we last saw *any* message from them related to this
                    last_seen_time = info.get("last_seen", info.get("timestamp", 0))
                    if not self._valid_timestamp(last_seen_time):
                        last_seen_time = 0
                    if current_time - last_seen_time > received_emergency_timeout:
                        nodes_to_remove.append(node_num)
                        stale_emergencies_found = True

                if nodes_to_remove:
                    logger.debug(f"Cleaning stale received emergency info (timeout: {received_emergency_timeout}s)...")
                    for node_num in nodes_to_remove:
                        with self._state_lock:
                            current_info = self.active_emergency_info.get(node_num)
                            if not current_info or current_info.get("last_seen") != emergency_snapshot[node_num].get("last_seen"):
                                continue
                            del self.active_emergency_info[node_num]
                            logger.info(f"Removed stale tracked emergency info for node {format_node_id(node_num)}")

                for key, cleared_at in cleared_snapshot.items():
                    if current_time - cleared_at > received_emergency_timeout:
                        with self._state_lock:
                            if self._cleared_emergencies.get(key) == cleared_at:
                                del self._cleared_emergencies[key]

                if not stale_acks_found and not stale_emergencies_found:
                    logger.debug("Background cleanup ran, no stale data found.")

            except Exception:
                # Log errors in the cleanup thread but keep the thread running
                logger.exception("Error during AERP background cleanup task.")

        logger.debug("AERP cleanup thread stopped.")


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
        with self._state_lock:
            current_active_id = self.last_emergency_id
            emergency_active = self.emergency_active
            acknowledgement_snapshot = {
                emergency_id: dict(nodes)
                for emergency_id, nodes in self.acknowledgements.items()
            }
            emergency_snapshot = {
                node_num: dict(info)
                for node_num, info in self.active_emergency_info.items()
            }
            my_node_id = self.my_node_id
            config_snapshot = dict(self.config.config)

        formatted_acks: Dict[str, Dict[str, str]] = {}

        # Format acknowledgements for the *currently active* emergency ID if one exists
        if current_active_id and current_active_id in acknowledgement_snapshot:
             formatted_acks[current_active_id] = {
                  format_node_id(n): self._format_timestamp(ts)
                  for n, ts in acknowledgement_snapshot[current_active_id].items()
             }

        formatted_received_emergencies = {}
        for node_num, info in emergency_snapshot.items():
            sent_timestamp = info.get("timestamp")
            received_timestamp = info.get("received_at", info.get("last_seen"))
            formatted_received_emergencies[format_node_id(node_num)] = {
                "emergency_id": info.get("message_id"),
                "message": info.get("message"),
                "gps": info.get("gps"),
                "battery": info.get("battery"),
                "sent_at": self._format_timestamp(sent_timestamp) if sent_timestamp is not None else "Unknown",
                "received_at": self._format_timestamp(received_timestamp),
                "last_seen": self._format_timestamp(info.get("last_seen")),
            }

        status = {
            "my_node_id": my_node_id,
            "emergency_active": emergency_active,
            "last_emergency_id": current_active_id,
            "active_acknowledgements": formatted_acks.get(current_active_id, {}) if current_active_id else {},
            "active_received_emergencies": formatted_received_emergencies,
            "config": config_snapshot,
        }
        return status

    @classmethod
    def _format_timestamp(cls, value):
        if not cls._valid_timestamp(value):
            return "Unknown"
        try:
            return datetime.fromtimestamp(value).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
        except (OSError, OverflowError, ValueError):
            return "Unknown"

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
            if interface is not None:
                self.interface = interface
            self._update_node_info()
        else:
            logger.warning("Meshtastic device disconnected.")
            # Stop emergency broadcast if it was active, but don't try to send clear
            self.stop_emergency(send_clear=False)
            # Reset node info as it's no longer valid
            self.my_node_num = None
            self.my_node_id = "Unknown"
            logger.info("AERP node info reset due to disconnection.")

    def close(self, send_clear=True):
        """Stop workers and release this instance's runtime state.

        The Meshtastic interface is owned by the CLI/GUI and is deliberately
        not closed here.
        """
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self.stop_emergency(send_clear=send_clear)
        self._shutdown_event.set()
        if self._cleanup_thread.is_alive() and threading.current_thread() is not self._cleanup_thread:
            self._cleanup_thread.join(timeout=2)
