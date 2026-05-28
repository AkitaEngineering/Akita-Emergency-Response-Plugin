import json
import unittest
from types import SimpleNamespace
from unittest import mock

from aerp import cli
from aerp.constants import (
    CONFIG_ACK_TIMEOUT,
    CONFIG_INTERVAL,
    CONFIG_MESSAGE,
    CONFIG_PORT,
    CONFIG_RADIUS,
)
from aerp.plugin import AERP
from aerp.utils import get_location_from_packet


class FakeConfig:
    def __init__(self, port=256):
        self.config = {
            CONFIG_INTERVAL: 60,
            CONFIG_PORT: port,
            CONFIG_MESSAGE: "SOS",
            CONFIG_RADIUS: 1000,
            CONFIG_ACK_TIMEOUT: 300,
        }

    def get(self, key, default=None):
        return self.config.get(key, default)


class FakeInterface:
    def __init__(self, my_node_num=123, node_info=None):
        self.myInfo = SimpleNamespace(my_node_num=my_node_num)
        self.node_info = node_info or {
            "position": {"latitude": 40.0, "longitude": -111.0},
            "deviceMetrics": {"batteryLevel": 87},
        }
        self.sent = []

    def getMyNodeInfo(self):
        return self.node_info

    def sendData(self, data, **kwargs):
        self.sent.append((data, kwargs))


class FakeAERPInstance:
    def __init__(self):
        self.connection_events = []

    def on_connection_change(self, interface, connected):
        self.connection_events.append((interface, connected))


class AERPBehaviorTests(unittest.TestCase):
    def create_plugin(self, interface=None, config=None):
        interface = interface or FakeInterface()
        config = config or FakeConfig()
        with mock.patch.object(AERP, "_background_cleanup", return_value=None):
            return AERP(interface, config)

    def test_send_clear_message_sends_json_bytes(self):
        interface = FakeInterface()
        plugin = self.create_plugin(interface=interface)

        plugin.send_clear_message("clear-1")

        self.assertEqual(len(interface.sent), 1)
        payload_bytes, kwargs = interface.sent[0]
        self.assertEqual(kwargs["portNum"], 256)
        self.assertEqual(kwargs["destinationId"], "^all")
        payload = json.loads(payload_bytes.decode("utf-8"))
        self.assertEqual(payload["type"], "AERP_CLEAR")
        self.assertEqual(payload["emergency_id"], "clear-1")

    def test_handle_incoming_accepts_private_app_alias_and_sends_ack(self):
        interface = FakeInterface()
        plugin = self.create_plugin(interface=interface)

        packet = {
            "from": 456,
            "decoded": {
                "portnum": "PRIVATE_APP",
                "payload": json.dumps(
                    {
                        "type": "AERP_EMERGENCY",
                        "emergency_id": "em-1",
                        "message": "Help",
                        "gps": {"latitude": 41.0, "longitude": -112.0},
                        "battery": 55,
                        "timestamp": 1,
                    }
                ).encode("utf-8"),
            },
        }

        plugin.handle_incoming(packet, interface)

        self.assertIn(456, plugin.active_emergency_info)
        ack_payload_bytes, ack_kwargs = interface.sent[-1]
        ack_payload = json.loads(ack_payload_bytes.decode("utf-8"))
        self.assertEqual(ack_payload["type"], "AERP_ACK")
        self.assertTrue(str(ack_kwargs["destinationId"]).endswith("000001c8"))

    def test_get_location_from_standard_position_packet(self):
        packet = {
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {
                    "latitudeI": 123456789,
                    "longitudeI": 987654321,
                },
            }
        }

        self.assertEqual(
            get_location_from_packet(packet),
            (12.3456789, 98.7654321),
        )

    def test_local_node_context_uses_meshtastic_node_db(self):
        interface = FakeInterface(
            node_info={
                "position": {"latitude": 42.5, "longitude": -71.1, "altitude": 10},
                "deviceMetrics": {"batteryLevel": 64},
            }
        )
        plugin = self.create_plugin(interface=interface)

        self.assertEqual(
            plugin._get_local_position_payload(),
            {"latitude": 42.5, "longitude": -71.1, "altitude": 10},
        )
        self.assertEqual(plugin._get_local_battery_level(), 64)


class AERPCLITests(unittest.TestCase):
    def test_connection_callbacks_map_to_boolean_state(self):
        fake_plugin = FakeAERPInstance()
        fake_interface = object()
        previous_instance = cli.aerp_instance
        cli.aerp_instance = fake_plugin
        try:
            cli.onConnectionEstablished(fake_interface)
            cli.onConnectionLost(fake_interface)
        finally:
            cli.aerp_instance = previous_instance

        self.assertEqual(
            fake_plugin.connection_events,
            [(fake_interface, True), (fake_interface, False)],
        )


if __name__ == "__main__":
    unittest.main()