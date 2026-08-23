# Akita Emergency Response Plugin (AERP) for Meshtastic

AERP is a host-side Python CLI for sending emergency alerts, acknowledgements, and all-clear messages over a Meshtastic mesh. It is aimed at Search & Rescue (SAR), disaster response, and remote teams that want a simple laptop or single-board-computer workflow attached to a Meshtastic radio.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Website: https://www.akitaengineering.com

## Current Status

- Emergency, ACK, and CLEAR messages are sent as JSON bytes using the current Meshtastic Python API.
- Emergency payloads are size-checked against Meshtastic's 233-byte limit before transmission.
- Incoming alerts work with both raw private-port numbers and Meshtastic's `PRIVATE_APP` alias.
- Proximity alerts recognize standard Meshtastic `decoded.position` packets.
- Local GPS and battery telemetry are read from Meshtastic's node database when available.
- Worker threads stop promptly, and graceful CLI/GUI shutdown attempts an all-clear for an active local incident.

## Why This Is Not an ESP-IDF Port

This repository is an external operator tool, not a Meshtastic firmware module. Converting it to ESP-IDF would mean maintaining custom device firmware or integrating directly into Meshtastic itself.

For most SAR teams, the current host-side approach is the safer choice:

- easier to update and validate before deployment
- easier to log operator actions during incidents
- no custom firmware maintenance burden on every field radio
- works with existing Meshtastic hardware and Python tooling

If you eventually need fully autonomous on-device alerting with no attached host, that should be a separate firmware project rather than a direct conversion of this CLI.

## Requirements

- Python `>=3.9,<3.15` to match the current `meshtastic` package requirement
- a Meshtastic radio connected over serial or reachable over TCP
- dependencies from `requirements.txt`
- (Optional) `customtkinter` for the graphical dashboard.

## Quick Start

1. Clone the repository.

```bash
git clone https://github.com/AkitaEngineering/Akita-Emergency-Response-Plugin.git
cd Akita-Emergency-Response-Plugin
```

2. Create and activate a virtual environment, then install dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

To install AERP itself and expose the `aerp` command instead:

```bash
pip install .
```

3. Copy a config template.

```bash
cp config/aerp_config.example.json config/aerp_config.json
```

For a SAR-oriented starting profile, use:

```bash
cp config/aerp_config.sar.example.json config/aerp_config.json
```

4. Run the CLI.

```bash
python -m aerp.cli --config config/aerp_config.json
```

For TCP-connected devices:

```bash
python -m aerp.cli --host <ip_or_hostname> --config config/aerp_config.json
```

For an explicit serial device path:

```bash
python -m aerp.cli --device /dev/ttyUSB0 --config config/aerp_config.json
```

For detailed troubleshooting output:

```bash
python -m aerp.cli --device /dev/ttyUSB0 --config config/aerp_config.json --debug
```

If the config file does not exist, AERP creates a default JSON config at the path passed to `--config`.

## GUI Dashboard

AERP includes a dark-mode graphical dashboard. Install its optional dependency and launch it with:

```bash
pip install -r requirements-gui.txt
python -m aerp.gui --config config/aerp_config.json
```

An installed checkout can use `pip install ".[gui]"` followed by `aerp-gui`. In the connection field, use a serial path such as `/dev/ttyUSB0` or `COM3`, a hostname/IP address for TCP, or an explicit `serial://`/`tcp://` prefix.

The GUI allows you to explicitly connect to a radio, monitor logs precisely, track incoming incidents, and manage broadcasts visually using a high-contrast Red/Black/Gray interface.

## Connection Modes

- `--device /path/to/serial`: connect to a radio over a specific serial device.
- `--host <ip_or_hostname>`: connect to a Meshtastic TCP endpoint.
- `--no-serial`: skip serial autodetect when you only want TCP.
- `--debug`: enable verbose logging for connection and packet handling.

## CLI Commands

- `start` starts broadcasting emergency messages.
- `stop` stops broadcasting and sends an all-clear for the active emergency.
- `clear` sends an all-clear for the last sent emergency when no broadcast is active.
- `status` prints local state, received ACKs, and active inbound emergencies.
- `help` prints command help.
- `exit` or `quit` stops the CLI.

## Configuration

Edit `config/aerp_config.json` with these keys:

- `interval`: seconds between broadcasts, must be greater than `0`
- `emergency_port`: private Meshtastic application port from `256` to `511`; all participating nodes must match
- `emergency_message`: default alert text
- `alert_radius`: proximity-alert radius in meters; `0` disables it
- `ack_timeout`: seconds before an ACK is considered stale
- `plugin_enabled_by_default`: auto-start emergency broadcasting on launch when `true`

The file must be valid JSON with no comments.

`emergency_message` is limited to 80 JSON-encoded bytes so the message, emergency ID, and GPS coordinates can always fit in one Meshtastic packet. Optional battery, altitude, and sender-time fields are included only when space remains.

## Suggested Operator Workflow

1. Confirm the radio is connected and has the correct Meshtastic channel and team port.
2. Start AERP and run `status` to verify the local node ID is available.
3. Use `start` only for a real drill or incident; AERP begins recurring broadcasts immediately.
4. Watch `status` for acknowledgements and other active emergencies on the mesh.
5. Use `stop` when the incident is over so AERP halts broadcasts and sends `AERP_CLEAR`.
6. On graceful exit, AERP attempts to clear an active local emergency. On power loss or a severed radio connection, peers age the incident out after their received-emergency timeout.

## SAR Deployment Guidance

- Pick a dedicated team port in the private range and standardize it across every node. The SAR example uses `300` to avoid relying on Meshtastic's generic `PRIVATE_APP` default.
- Keep the broadcast interval between `30` and `60` seconds unless you have tested battery impact under realistic field conditions.
- Set `ack_timeout` to at least `3x` the broadcast interval so brief network fades do not make acknowledgements look stale.
- Configure a fixed position on radios that may operate without live GPS. AERP can only include location that Meshtastic already knows about.
- Run the CLI on stable, field-chargeable hardware such as a rugged laptop or Raspberry Pi with a power bank.
- Perform radio-to-radio drills before deployment. LoRa coverage, terrain shadowing, and node placement matter more than application logic.
- Use an encrypted Meshtastic channel with controlled keys. AERP trusts the authenticated mesh packet sender and does not add separate application-level signatures.

## Troubleshooting

- If the CLI cannot determine the local node ID, wait a few seconds after connecting and run `status` again.
- If alerts are not received, verify every participant uses the same `emergency_port` and Meshtastic channel.
- If acknowledgements appear to expire too quickly, increase `ack_timeout` relative to `interval`.
- If GPS is missing from alerts, confirm the radio has either live GPS or a configured fixed position in Meshtastic.
- If serial autodetect fails, retry with `--device /dev/ttyUSB0` or the correct platform-specific device path.

## Validation

Run the built-in regression checks with:

```bash
python -m unittest discover -s tests -v
```

Release validation also includes:

```bash
pip install -r requirements-dev.txt
python -m compileall -q aerp tests
python -m mypy aerp
python -m build
```

## Pre-deployment Gate

Automated checks cannot validate radios, RF conditions, channel keys, GPS acquisition, or field power. Before operational use:

1. Install into a clean virtual environment using the exact artifact intended for deployment.
2. Confirm every radio uses the same encrypted channel and `emergency_port`.
3. Run a two-radio drill for EMERGENCY, directed ACK, repeated broadcast, and matching ALL CLEAR.
4. Test serial/TCP disconnect and process shutdown behavior while an incident is active.
5. Verify GPS, fixed-position fallback, battery telemetry, host clock, and log retention.
6. Record the tested AERP, Python, Meshtastic, radio firmware, and configuration versions.

## License

This project is licensed under the GNU General Public License v3.0 (GPLv3). See `LICENSE` for details.
