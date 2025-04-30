# aerp/utils.py
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
Utility functions for the Akita Emergency Response Plugin (AERP).
Includes distance calculation, location extraction, and formatting helpers.
"""

import math
import logging
import meshtastic.util # For POSITION_APP constant if needed, though direct check is fine

from .constants import EARTH_RADIUS_METERS

# Get a logger specific to this module
logger = logging.getLogger(__name__)

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two points
    on the Earth (specified in decimal degrees) using the Haversine formula.

    Args:
        lat1 (float): Latitude of point 1.
        lon1 (float): Longitude of point 1.
        lat2 (float): Latitude of point 2.
        lon2 (float): Longitude of point 2.

    Returns:
        float: Distance in meters. Returns float('inf') if input is invalid
               or calculation fails.
    """
    # Validate input types and values
    if None in [lat1, lon1, lat2, lon2]:
        logger.debug("Cannot calculate distance with missing GPS coordinates.")
        return float('inf')
    if not all(isinstance(coord, (int, float)) for coord in [lat1, lon1, lat2, lon2]):
         logger.warning(f"Invalid coordinate types for distance calculation: {lat1}, {lon1}, {lat2}, {lon2}")
         return float('inf')
    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90 and -180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        logger.warning(f"Coordinates out of valid range for distance calculation: ({lat1},{lon1}), ({lat2},{lon2})")
        return float('inf')

    try:
        # Convert decimal degrees to radians
        rad_lat1, rad_lon1, rad_lat2, rad_lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

        # Haversine formula
        dlon = rad_lon2 - rad_lon1
        dlat = rad_lat2 - rad_lat1
        a = math.sin(dlat / 2)**2 + math.cos(rad_lat1) * math.cos(rad_lat2) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        distance = EARTH_RADIUS_METERS * c
        return distance
    except (TypeError, ValueError) as e:
        # Catch potential math errors with invalid inputs that slipped through checks
        logger.error(f"Mathematical error calculating distance between ({lat1},{lon1}) and ({lat2},{lon2}): {e}")
        return float('inf')
    except Exception as e:
         # Catch any other unexpected errors
         logger.exception(f"Unexpected error calculating distance: {e}")
         return float('inf')


def get_location_from_packet(packet):
    """
    Extracts latitude and longitude from a Meshtastic packet's decoded payload.
    Handles standard POSITION_APP packets and checks for 'gps' dictionary
    within custom payloads (like AERP's own emergency message).

    Args:
        packet (dict): The Meshtastic packet dictionary.

    Returns:
        tuple: (latitude, longitude) as floats if found, otherwise (None, None).
               Coordinates are in decimal degrees.
    """
    if not isinstance(packet, dict):
        logger.debug("Invalid packet format passed to get_location_from_packet.")
        return None, None

    decoded_part = packet.get('decoded', {})
    if not isinstance(decoded_part, dict):
         logger.debug("Packet missing 'decoded' dictionary.")
         return None, None

    payload = decoded_part.get('payload')
    portnum = decoded_part.get('portNum') # Can be int or string depending on source

    # --- Check Standard Position App Packet ---
    # meshtastic.util.PortNum.POSITION_APP == 1
    if portnum == 1 or str(portnum) == 'POSITION_APP':
        if isinstance(payload, dict):
            # Meshtastic position payloads use integer representations (degrees * 1e7)
            if 'latitudeI' in payload and 'longitudeI' in payload:
                try:
                    lat = float(payload['latitudeI']) / 1e7
                    lon = float(payload['longitudeI']) / 1e7
                    # Basic validation for range
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        return lat, lon
                    else:
                        logger.debug(f"Position packet coordinates out of range: lat={lat}, lon={lon}")
                except (TypeError, ValueError) as e:
                    logger.warning(f"Error converting position packet coordinates: {e}, payload: {payload}")
            # Older versions might have used float directly? Less common now.
            elif 'latitude' in payload and 'longitude' in payload:
                 try:
                     lat = float(payload['latitude'])
                     lon = float(payload['longitude'])
                     if -90 <= lat <= 90 and -180 <= lon <= 180:
                         return lat, lon
                 except (TypeError, ValueError):
                     pass # Ignore if conversion fails

    # --- Check for Embedded 'gps' Dictionary (e.g., in AERP messages) ---
    if isinstance(payload, dict) and 'gps' in payload and isinstance(payload['gps'], dict):
        gps_data = payload['gps']
        # Prefer precise integer format if available
        if 'latitudeI' in gps_data and 'longitudeI' in gps_data:
            try:
                lat = float(gps_data['latitudeI']) / 1e7
                lon = float(gps_data['longitudeI']) / 1e7
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon
            except (TypeError, ValueError):
                pass # Ignore conversion errors
        # Fallback to float format
        elif 'latitude' in gps_data and 'longitude' in gps_data:
            try:
                lat = float(gps_data['latitude'])
                lon = float(gps_data['longitude'])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon
            except (TypeError, ValueError):
                 pass # Ignore conversion errors

    # If no location found after checking both possibilities
    # logger.debug(f"Could not extract valid location from packet ID: {packet.get('id', 'N/A')}, Port: {portnum}")
    return None, None

def format_node_id(node_num):
    """
    Formats a Meshtastic node number (integer) into the common hexadecimal
    representation used in the network (e.g., '!aabbccdd').

    Args:
        node_num (int): The node number integer.

    Returns:
        str: The formatted node ID string (e.g., "!aabbccdd"), or "Unknown"
             if the input is None or invalid.
    """
    if node_num is None:
        return "Unknown"
    try:
        # Ensure it's treated as an integer, then format as 8-digit hex
        return f"!{int(node_num):08x}"
    except (ValueError, TypeError):
        logger.warning(f"Could not format invalid node number: {node_num}")
        return "Unknown"

