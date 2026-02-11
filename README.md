# Akita Emergency Response Plugin (AERP) for Meshtastic

AERP is a Python application that helps teams share emergency alerts, location, and acknowledgements over a Meshtastic mesh network. It's aimed at Search & Rescue (SAR), disaster response, and remote-group safety use cases.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Website: https://www.akitaengineering.com

## Key Features

- Start/stop emergency broadcasts with a unique emergency ID.
- Broadcast message, GPS (if available), and battery level periodically.
- Automatic acknowledgements (ACK) from receiving nodes.
- Manual or automatic "All Clear" (`stop`) to signal resolution.
- Proximity alerts when other nodes are within a configured radius.
- Simple CLI with `start`, `stop`, `clear`, `status`, and `help`.

## Quick Start

1. Clone the repository:

```bash
git clone https://github.com/AkitaEngineering/Akita-Emergency-Response-Plugin.git
cd Akita-Emergency-Response-Plugin
```

2. Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate    # Windows
# or: source .venv/bin/activate  # macOS / Linux

pip install -r requirements.txt
```

3. Copy the example config and edit as needed:

```bash
copy config\aerp_config.example.json config\aerp_config.json    # Windows
# or: cp config/aerp_config.example.json config/aerp_config.json  # macOS / Linux
```

4. Run the CLI (auto-detect serial by default):

```bash
python -m aerp.cli --config config/aerp_config.json
```

For TCP:

```bash
python -m aerp.cli --host <ip_or_hostname> --config config/aerp_config.json
```

## CLI Commands

- `start` — Start broadcasting emergency messages.
- `stop` — Stop broadcasting and send an "All Clear" for the last emergency.
- `clear` — Manually send an "All Clear" for the most recently sent emergency (useful if `stop` failed).
- `status` — Show current status, acknowledgements, and active received alerts.
- `help` — Show help.
- `exit` / `quit` — Quit the plugin.

## Configuration

Edit `config/aerp_config.json` to configure behavior. Example keys:

- `interval`: seconds between broadcasts (integer > 0)
- `emergency_port`: Meshtastic port number (0–511). All nodes must use the same port.
- `emergency_message`: default emergency text.
- `alert_radius`: proximity alert radius in meters (0 disables alerts).
- `ack_timeout`: seconds before received ACKs are considered stale.
- `plugin_enabled_by_default`: if true, plugin attempts to auto-start on launch.

Note: `config/aerp_config.json` must be valid JSON (no comments). See `config/aerp_config.example.json`.


## Notes & Considerations

- Test thoroughly with your Meshtastic hardware before relying on AERP in real emergencies.
- Frequent broadcasts consume power; balance `interval` with battery constraints.
- Meshtastic/LoRa is line-of-sight dependent; coverage is not guaranteed.
- Ensure all team members use the same `emergency_port`.

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). See the `LICENSE` file for details.

---

If you'd like, I can also add a short `CONTRIBUTING.md`, unit tests, or a GitHub Actions CI workflow to run linting and tests.

<!-- Project structure removed. See package directories `aerp/` and `config/` for layout. -->

## Dependencies

* Python 3.7+ (Recommended)
* [meshtastic-python](https://github.com/meshtastic/python) library (`pip install meshtastic`)
* [pypubsub](https://pypi.org/project/PyPubSub/) (`pip install pypubsub`) (Often installed as a dependency of `meshtastic`)

## Installation

1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/akitaengineering/akita-emergency-response-plugin.git](https://github.com/akitaengineering/akita-emergency-response-plugin.git)
    cd akita-emergency-response-plugin
    ```


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
         # Akita Emergency Response Plugin (AERP) for Meshtastic

        AERP is a Python application that helps teams share emergency alerts, location, and acknowledgements over a Meshtastic mesh network. It's aimed at Search & Rescue (SAR), disaster response, and remote-group safety use cases.

        [![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

        Website: https://www.akitaengineering.com

        ## Key Features

        - Start/stop emergency broadcasts with a unique emergency ID.
        - Broadcast message, GPS (if available), and battery level periodically.
        - Automatic acknowledgements (ACK) from receiving nodes.
        - Manual or automatic "All Clear" (`stop`) to signal resolution.
        - Proximity alerts when other nodes are within a configured radius.
        - Simple CLI with `start`, `stop`, `clear`, `status`, and `help`.

        ## Quick Start

        1. Clone the repository:

        ```bash
        git clone https://github.com/AkitaEngineering/Akita-Emergency-Response-Plugin.git
        cd Akita-Emergency-Response-Plugin
        ```

        2. Create and activate a virtual environment, then install dependencies:

        ```bash
        python -m venv .venv
        .venv\Scripts\activate    # Windows
        # or: source .venv/bin/activate  # macOS / Linux

        pip install -r requirements.txt
        ```

        3. Copy the example config and edit as needed:

        ```bash
        copy config\aerp_config.example.json config\aerp_config.json    # Windows
        # or: cp config/aerp_config.example.json config/aerp_config.json  # macOS / Linux
        ```

        4. Run the CLI (auto-detect serial by default):

        ```bash
        python -m aerp.cli --config config/aerp_config.json
        ```

        For TCP:

        ```bash
        python -m aerp.cli --host <ip_or_hostname> --config config/aerp_config.json
        ```

        ## CLI Commands

        - `start` — Start broadcasting emergency messages.
        - `stop` — Stop broadcasting and send an "All Clear" for the last emergency.
        - `clear` — Manually send an "All Clear" for the most recently sent emergency (useful if `stop` failed).
        - `status` — Show current status, acknowledgements, and active received alerts.
        - `help` — Show help.
        - `exit` / `quit` — Quit the plugin.

        ## Configuration

        Edit `config/aerp_config.json` to configure behavior. Example keys:

        - `interval`: seconds between broadcasts (integer > 0)
        - `emergency_port`: Meshtastic port number (0–511). All nodes must use the same port.
        - `emergency_message`: default emergency text.
        - `alert_radius`: proximity alert radius in meters (0 disables alerts).
        - `ack_timeout`: seconds before received ACKs are considered stale.
        - `plugin_enabled_by_default`: if true, plugin attempts to auto-start on launch.

        Note: `config/aerp_config.json` must be valid JSON (no comments). See `config/aerp_config.example.json`.

        <!-- Project layout removed. Refer to the `aerp/` and `config/` directories. -->

        ## Notes & Considerations

        - Test thoroughly with your Meshtastic hardware before relying on AERP in real emergencies.
        - Frequent broadcasts consume power; balance `interval` with battery constraints.
        - Meshtastic/LoRa is line-of-sight dependent; coverage is not guaranteed.
        - Ensure all team members use the same `emergency_port`.

        ## License

        This project is licensed under the GNU General Public License v3.0 (GPLv3). See the `LICENSE` file for details.

        ---

        If you'd like, I can also add a short `CONTRIBUTING.md`, unit tests, or a GitHub Actions CI workflow to run linting and tests.
