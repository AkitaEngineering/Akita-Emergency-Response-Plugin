# Akita Emergency Response Plugin (AERP)

The Akita Emergency Response Plugin (AERP) is a Meshtastic plugin designed to provide a robust and reliable emergency communication system. It allows users to send emergency messages with GPS location, battery level, and receive acknowledgements, enhancing safety and coordination in critical situations.

## Features

* **Emergency Broadcast:** Sends emergency messages with GPS location and battery level over the Meshtastic network.
* **Message Acknowledgement:** Receiving nodes can acknowledge emergency messages, providing confirmation of receipt.
* **Configurable Interval:** Adjusts the frequency of emergency broadcasts.
* **Configurable Port:** Allows users to specify the Meshtastic port for emergency messages.
* **Configurable Message:** Customizes the emergency message.
* **User Input Control:** Starts and stops emergency broadcasts via user input.
* **Incoming Message Handling:** Logs received emergency messages and acknowledgements.
* **Connection Handling:** Manages Meshtastic connection events.
* **Threaded Operation:** Runs the emergency broadcast in a separate thread.
* **GPS Data:** Includes GPS information in the emergency message.
* **Battery Monitoring:** Includes battery level in the emergency message.
* **Alert Radius:** Triggers alerts when other Meshtastic devices enter a configurable radius.
* **Distance Calculation:** Uses the Haversine formula for accurate distance calculations.
* **Comprehensive Logging:** Provides detailed logs for debugging and monitoring.

## Installation

1.  **Install Meshtastic:** Ensure you have Meshtastic installed and configured.
2.  **Save the Code:** Save the Python code as `aerp.py` and the JSON configuration as `aerp_config.json` in your Meshtastic plugins directory.
3.  **Run the Plugin:** Run the plugin from the command line: `python aerp.py`.
4.  **Control the Broadcast:** Enter "start" to begin the emergency broadcast, "stop" to end it, and "exit" to quit the plugin.
5.  **Configure:** Modify the `aerp_config.json` file to customize the plugin's behavior.

## Usage

1.  Start the plugin by running `python aerp.py` from your command line.
2.  Use the command line interface to start and stop the emergency broadcasts.
3.  When an emergency message is received, it will be logged in the console.
4.  When another Meshtastic device enters the alert radius, an alert will be logged.
5.  When an acknowledgement is received, it will be logged.

## Dependencies

* Meshtastic Python API
* Python 3.6 or later

## Contributing

Contributions are welcome! If you have any suggestions or bug reports, please feel free to submit an issue or pull request.


## Configuration (aerp_config.json)

* `interval`: The interval (in seconds) between emergency broadcasts.
* `emergency_port`: The Meshtastic port for emergency messages.
* `emergency_message`: The custom emergency message.
* `alert_radius`: The radius (in meters) within which to trigger alerts.

**Example aerp_config.json:**

```json
{
    "interval": 5,
    "emergency_port": 3,
    "emergency_message": "HELP! Emergency situation detected.",
    "alert_radius": 500
}

