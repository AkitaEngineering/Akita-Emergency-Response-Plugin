# Akita Emergency Response Plugin (AERP) for Meshtastic

AERP is a standalone Python application designed to enhance safety, communication, and coordination using the Meshtastic mesh network, particularly valuable in **Search and Rescue (SAR)** operations, disaster response, or any situation where reliable off-grid communication is critical. It provides an intuitive way for users to broadcast emergency status, share vital information, confirm message receipt, and maintain situational awareness.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
## Key Features for Emergency & SAR Use

* **Simple Emergency Activation:** Start broadcasting critical alerts with a single `start` command.
* **Vital Information Broadcast:** Automatically sends periodic messages containing:
    * Configurable emergency text (e.g., "SOS", "INJURED", "LOST").
    * **GPS Location:** Shares precise coordinates (if available) for locating personnel.
    * **Battery Level:** Indicates device power status, crucial for long operations.
    * **Unique Emergency ID:** Allows specific tracking of an emergency event and its acknowledgements.
* **Acknowledgement System:**
    * Receiving nodes automatically **confirm receipt** (ACK) of emergency messages.
    * The initiating node can easily check **who has received** the alert using the `status` command, improving confidence in message delivery.
* **"All Clear" Signal:** Notify the team when the emergency is resolved using the `stop` command (which automatically sends a clear signal).
* **Proximity Alerts:** Get notified when other Meshtastic users (e.g., team members, lost person) come within a defined range, aiding in location and rendezvous. Uses standard position packets for efficiency.
* **Intuitive Command Line:** Simple, clear commands (`start`, `stop`, `status`, `help`, `exit`) minimize cognitive load in stressful situations.
* **Robust & Resilient:**
    * Handles configuration errors gracefully with defaults.
    * Manages Meshtastic device connections/disconnections.
    * Runs broadcasts in the background.
    * Cleans up old data automatically.
* **Clear Status View:** The `status` command provides a quick overview of your status, who has acknowledged *your* alert, and any active alerts *you've received* from others.
* **Detailed Logging:** Records events for post-incident analysis or debugging (includes optional `--debug` mode).
* **Flexible Connection:** Supports both Serial (USB) and TCP connections to Meshtastic nodes.

## Use Cases

* **Search and Rescue (SAR):**
    * Lost hiker activates `start` to broadcast location and status.
    * SAR team members run AERP to receive alerts and track acknowledgements.
    * Proximity alerts notify teams when they are near the lost person or each other.
    * `status` command helps coordinate efforts by showing who has received the alert.
    * `stop` command signals when the person is found and sends the "All Clear".
* **Natural Disasters:** Coordinate rescue or welfare checks when cellular networks are down.
* **Remote Group Activities:** Enhance safety for hiking, boating, or off-roading groups in areas without cell service.
* **Personal Safety:** Provide a simple way to signal distress with location data.

## Project Structure
```
akita-emergency-response-plugin/
├── aerp/                     # Main package directory
│   ├── init.py
│   ├── cli.py                # Command Line Interface logic
│   ├── config.py             # Configuration management
│   ├── constants.py          # Message types, defaults
│   ├── plugin.py             # Core AERP class logic
│   └── utils.py              # Helper functions (distance calc, etc.)
├── config/
│   └── aerp_config.example.json # Example configuration
├── tests/                    # Unit/Integration tests (Optional)
├── .gitignore
├── LICENSE                   # GPLv3 License file
├── README.md                 # This file
└── requirements.txt          # Dependencies
```

## Dependencies

* Python 3.7+ (Recommended)
* [meshtastic-python](https://github.com/meshtastic/python) library (`pip install meshtastic`)
* [pypubsub](https://pypi.org/project/PyPubSub/) (`pip install pypubsub`) (Often installed as a dependency of `meshtastic`)

## Installation

1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/your-username/akita-emergency-response-plugin.git](https://github.com/your-username/akita-emergency-response-plugin.git)
    cd akita-emergency-response-plugin
    ```
    *(Replace `your-username` with the actual repository location)*

2.  **Install Dependencies:**
    ```bash
    # It's highly recommended to use a Python virtual environment
    # python -m venv env
    # source env/bin/activate  # On Linux/macOS
    # .\env\Scripts\activate   # On Windows

    pip install -r requirements.txt
    ```

3.  **Configure:**
    * Copy the example configuration:
        ```bash
        cp config/aerp_config.example.json config/aerp_config.json
        ```
    * **Edit `config/aerp_config.json`**: Customize settings like the Meshtastic port, default emergency message, broadcast frequency (`interval`), and `alert_radius`. See the "Configuration" section below for details. *Ensure the chosen `emergency_port` doesn't conflict with other Meshtastic apps.*

## Usage

1.  **Connect Meshtastic Device:** Ensure your Meshtastic node is powered on and connected via USB (for serial) or accessible via network (for TCP).

2.  **Run the Plugin:**
    From the root directory (`akita-emergency-response-plugin/`):
    ```bash
    # For Serial connection (auto-detect or specify device)
    python -m aerp.cli [--device /path/to/serial] [--config path/to/config.json] [--debug]

    # For TCP connection
    python -m aerp.cli --host <ip_or_hostname> [--config path/to/config.json] [--debug]
    ```
    * **Options:**
        * `--device PATH`: Specify serial device path (e.g., `/dev/ttyUSB0`, `COM3`).
        * `--host HOST`: Specify hostname or IP for TCP connection.
        * `--no-serial`: Disable serial connection attempt (use with `--host`).
        * `--config PATH`: Use a specific config file (default: `config/aerp_config.json`).
        * `--debug`: Enable detailed debug logging.

3.  **Control via CLI:**
    The `AERP>` prompt indicates the plugin is ready. Use these simple commands:

    * `start`: **Activate Emergency Broadcast.** Sends your location, battery, and message periodically.
    * `stop`: **Deactivate Broadcast & Send "All Clear".** Stops sending alerts and notifies others the emergency is over.
    * `status`: **Check Current Situation.** Shows if *you* are broadcasting, who acknowledged *your* alert, and any active alerts *you've received* from others.
    * `help`: **Show available commands.**
    * `exit` / `quit`: **Quit the Plugin.** Stops any active broadcast first.

    *Example Scenario:*
    ```
    AERP> start  # You are lost and activate the alert
    # ... AERP sends messages ...
    AERP> status # Check if anyone has received your alert yet
    # ... SAR team member receives alert ...
    # (On SAR member's AERP): *** EMERGENCY MESSAGE RECEIVED from !YourNodeID ... ***
    # (On your AERP): Acknowledgement RECEIVED for My Emergency ID ... from !SARNodeID
    AERP> status # Now shows SAR member acknowledged
    # ... SAR team finds you ...
    AERP> stop   # Signal you are okay and stop broadcasting (sends All Clear)
    ```

## Configuration (`config/aerp_config.json`)

```json
{
    "interval": 60, // Seconds between emergency broadcasts. Shorter interval = more updates but more battery/channel use.
    "emergency_port": 256, // Meshtastic port number (0-511). IMPORTANT: All users in a group must use the SAME port. Use private range (256-511).
    "emergency_message": "SOS! Emergency situation detected.", // Your default alert message. Keep it concise.
    "alert_radius": 1000, // Radius in meters for proximity alerts. Set to 0 to disable. Useful for finding nearby team members.
    "ack_timeout": 300, // Seconds before an acknowledgement is considered 'stale' in the status display and for internal cleanup.
    "plugin_enabled_by_default": false // Set to true to automatically 'start' when the script runs (Use with caution).
}
```
# How it Works (Simplified)

## Start
- You type `start`.
- AERP creates a unique ID for this emergency and starts sending `AERP_EMERGENCY` packets (containing your info) repeatedly over the mesh network on the configured port.

## Receive
- Another AERP user gets your packet.
- Their AERP logs it as a warning, notes the details (`who`, `where`, `when`, `ID`), and automatically sends an `AERP_ACK` packet back to you, including the unique ID.

## Acknowledge
- Your AERP receives the `AERP_ACK`.
- It records that the other user got your message for that specific emergency ID.
- You can see this using `status`.

## Stop/Clear
- You type `stop`.
- AERP stops broadcasting and sends an `AERP_CLEAR` packet with the emergency ID.
- Others receive this, log it, and know that specific emergency is resolved (they stop tracking it as active).

## Proximity
- AERP listens for any standard location packets from other nodes.
- If a node is within your `alert_radius`, it logs a warning.

## Background
- AERP keeps running, cleaning up old ACK data and received alert info so the `status` stays relevant.

---

# Important Considerations for SAR / Critical Use

## Testing
- Thoroughly test AERP with your specific Meshtastic devices and intended team members before relying on it in a real emergency.
- Understand its behavior and limitations in your environment.

## Battery Life
- Frequent broadcasting, especially with GPS enabled, consumes significant power.
- Consider external battery packs for Meshtastic devices during extended operations.
- Adjust the `interval` setting based on needs vs. power constraints.

## Network Limitations
- Meshtastic (LoRa) is line-of-sight dependent.
- Hills, buildings, and dense foliage can block signals.
- Message delivery is not guaranteed.
- AERP's ACKs help confirm receipt but don't guarantee future messages get through.

## Shared Port
- Everyone in your group/team must configure AERP to use the exact same `emergency_port` number to communicate.

## Human Factors
- While designed to be simple, ensure users are trained on the commands and how to interpret the `status` output, especially under stress.

## Computer Requirement
- This plugin requires a computer (like a laptop or Raspberry Pi) connected to the Meshtastic node via USB or TCP to run the Python script.
- It is **not** firmware running directly on the node itself.

## No Warranty
- This software is provided **"AS IS"** without warranty.
- See the `LICENSE` file for details.
- Do not rely on this as your sole means of emergency communication.
- Always have backup plans and devices (e.g., PLB, satellite messenger).

---

# Contributing

Contributions that enhance usability, reliability, or add relevant features (especially for SAR) are welcome! Follow standard GitHub practices:

1. **Fork the repository.**
2. **Create a feature branch**  
   ```sh
   git checkout -b feature/your-improvement
