# Akita Emergency Response Plugin (AERP)

AERP is a Meshtastic plugin designed to enhance emergency response capabilities. It provides features for activating and broadcasting emergency signals, logging emergency events, and handling incoming emergency messages.

## Features

-   **Emergency Activation:** Activates an emergency broadcast with optional message and GPS location.
-   **Emergency Deactivation:** Stops the emergency broadcast.
-   **Emergency Logging:** Logs all received emergency messages to a JSON file.
-   **Automatic Broadcast:** Will repeatedly send emergency messages until deactivated.
-   **Respects TX Delay:** The plugin will respect the TX delay of the LoRa configuration.
-   **Configurable Log File:** Allows users to specify the log file name via command-line arguments.
-   **Robust Error Handling:** Includes error handling for file I/O and network operations.

## Installation

1.  Place `aerp.py` in your Meshtastic plugins directory.
2.  Run Meshtastic with the plugin enabled.

## Usage

-   Use `aerp.activate_emergency()` to activate an emergency.
-   Use `aerp.deactivate_emergency()` to deactivate an emergency.
-   Emergency logs are stored in the specified log file (default: `emergency_log.json`).

## Command-Line Arguments

-   `--log`: Specifies the log file name (default: `emergency_log.json`).

## Dependencies

-   Meshtastic Python API

## Akita Engineering
