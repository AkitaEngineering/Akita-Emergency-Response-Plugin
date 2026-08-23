# aerp/constants.py
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
Constants used throughout the Akita Emergency Response Plugin (AERP).
"""

# --- Message Types ---
# These strings identify the type of AERP message being sent/received.
# Using a prefix helps avoid conflicts with other potential JSON messages.
MSG_TYPE_EMERGENCY = "AERP_EMERGENCY" # Indicates an emergency broadcast
MSG_TYPE_ACK = "AERP_ACK"             # Indicates an acknowledgement of an emergency message
MSG_TYPE_CLEAR = "AERP_CLEAR"         # Indicates the emergency situation is resolved

# --- Default Meshtastic Port ---
# Port number used for AERP communication over Meshtastic.
# IMPORTANT: All nodes in a group must use the same port number.
# Using a port in the private application range (256-511) is recommended
# to avoid conflicts with official Meshtastic ports or other common plugins.
# Port 3 is often used by SerialPlugin, so we choose a different default.
DEFAULT_EMERGENCY_PORT = 256

# --- Default Configuration Values ---
# These values are used if the configuration file is missing or invalid.
# They correspond to the keys in `aerp_config.example.json`.
DEFAULT_INTERVAL = 60               # Default broadcast interval in seconds
DEFAULT_EMERGENCY_MESSAGE = "SOS! Emergency situation detected." # Default text message
DEFAULT_ALERT_RADIUS = 1000         # Default proximity alert radius in meters (0 to disable)
DEFAULT_ACK_TIMEOUT = 300           # Default time in seconds before an ACK is considered stale
DEFAULT_ENABLED_BY_DEFAULT = False  # Default setting for auto-starting on launch

# Meshtastic currently limits decoded data payloads to 233 bytes. AERP keeps
# operator-provided text small enough that an emergency ID and GPS coordinates
# still fit in one packet. `_send_aerp_payload` performs the final authoritative
# encoded-size check before every transmission.
MAX_MESHTASTIC_PAYLOAD_BYTES = 233
MAX_EMERGENCY_MESSAGE_BYTES = 80

# Bounds for data received from the mesh. These prevent a malformed peer from
# growing memory/log output without limit while remaining backward compatible
# with existing AERP UUIDs and operator messages.
MAX_RECEIVED_MESSAGE_CHARS = 512
MAX_EMERGENCY_ID_CHARS = 128
MAX_NODE_NUM = 0xFFFFFFFF
CLEAR_BROADCAST_REPETITIONS = 3
CLEAR_BROADCAST_SPACING_SECONDS = 0.2

# --- Configuration File Keys ---
# These strings are the expected keys within the `aerp_config.json` file.
CONFIG_INTERVAL = "interval"
CONFIG_PORT = "emergency_port"
CONFIG_MESSAGE = "emergency_message"
CONFIG_RADIUS = "alert_radius"
CONFIG_ACK_TIMEOUT = "ack_timeout"
CONFIG_ENABLED = "plugin_enabled_by_default"

# --- GPS Constants ---
EARTH_RADIUS_METERS = 6371000       # Approximate radius of the Earth in meters for distance calculations
