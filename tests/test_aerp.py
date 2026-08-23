import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aerp import cli
from aerp.config import ConfigManager
from aerp.constants import (
    CONFIG_ACK_TIMEOUT,
    CONFIG_ENABLED,
    CONFIG_INTERVAL,
    CONFIG_MESSAGE,
    CONFIG_PORT,
    CONFIG_RADIUS,
    MAX_MESHTASTIC_PAYLOAD_BYTES,
)
from aerp.plugin import AERP
from aerp.utils import (
    build_gps_payload,
    calculate_distance,
    extract_battery_level,
    format_node_id,
    get_location_from_packet,
)


class FakeConfig:
    def __init__(self, port=256, interval=60, message="SOS"):
        self.config = {
            CONFIG_INTERVAL: interval,
            CONFIG_PORT: port,
            CONFIG_MESSAGE: message,
            CONFIG_RADIUS: 1000,
            CONFIG_ACK_TIMEOUT: 300,
            CONFIG_ENABLED: False,
        }

    def get(self, key, default=None):
        return self.config.get(key, default)


class FakeInterface:
    def __init__(self, my_node_num=123, node_info=None, send_error=None):
        self.myInfo = SimpleNamespace(my_node_num=my_node_num)
        self.node_info = node_info or {
            "position": {
                "latitude": 40.0,
                "longitude": -111.0,
                "altitude": 1500,
                "time": 1_700_000_000,
            },
            "deviceMetrics": {"batteryLevel": 87},
        }
        self.send_error = send_error
        self.sent = []

    def getMyNodeInfo(self):
        return self.node_info

    def sendData(self, data, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sent.append((data, kwargs))
        return object()


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

    def emergency_packet(self, emergency_id="em-1", sender=456, **overrides):
        payload = {
            "type": "AERP_EMERGENCY",
            "emergency_id": emergency_id,
            "message": "Help",
            "gps": {"latitude": 41.0, "longitude": -112.0},
            "battery": 55,
            "timestamp": 1,
        }
        payload.update(overrides)
        return {
            "from": sender,
            "decoded": {
                "portnum": "PRIVATE_APP",
                "payload": json.dumps(payload).encode("utf-8"),
            },
        }

    def test_send_clear_message_sends_json_bytes(self):
        interface = FakeInterface()
        plugin = self.create_plugin(interface=interface)
        self.assertTrue(plugin.send_clear_message("clear-1"))
        payload_bytes, kwargs = interface.sent[0]
        self.assertLessEqual(len(payload_bytes), MAX_MESHTASTIC_PAYLOAD_BYTES)
        self.assertEqual(kwargs["portNum"], 256)
        self.assertEqual(kwargs["destinationId"], "^all")
        payload = json.loads(payload_bytes.decode("utf-8"))
        self.assertEqual(payload["type"], "AERP_CLEAR")
        self.assertEqual(payload["emergency_id"], "clear-1")
        self.assertEqual(len(interface.sent), 3)

    def test_start_sends_a_valid_single_packet_before_returning(self):
        interface = FakeInterface(
            my_node_num=0xFFFFFFFF,
            node_info={
                "position": {
                    "latitude": -89.1234567,
                    "longitude": -179.1234567,
                    "altitude": 12345,
                    "time": 1_700_000_000,
                },
                "deviceMetrics": {"batteryLevel": 101},
            },
        )
        plugin = self.create_plugin(
            interface=interface,
            config=FakeConfig(message="x" * 80),
        )
        self.assertTrue(plugin.start_emergency())
        payload_bytes, _ = interface.sent[0]
        payload = json.loads(payload_bytes)
        self.assertLessEqual(len(payload_bytes), MAX_MESHTASTIC_PAYLOAD_BYTES)
        self.assertEqual(payload["type"], "AERP_EMERGENCY")
        self.assertEqual(payload["gps"]["latitude"], -89.1234567)
        self.assertNotIn("user_node_num", payload)
        plugin.close(send_clear=False)

    def test_start_rolls_back_when_first_radio_send_fails(self):
        plugin = self.create_plugin(
            interface=FakeInterface(send_error=RuntimeError("radio offline"))
        )
        self.assertFalse(plugin.start_emergency())
        self.assertFalse(plugin.emergency_active)
        self.assertIsNone(plugin.last_emergency_id)

    def test_failed_new_session_preserves_last_successful_id(self):
        interface = FakeInterface()
        plugin = self.create_plugin(interface=interface)
        self.assertTrue(plugin.start_emergency())
        previous_id = plugin.last_sent_emergency_id
        plugin.stop_emergency(send_clear=False)
        interface.send_error = RuntimeError("radio offline")

        self.assertFalse(plugin.start_emergency())

        self.assertEqual(plugin.last_sent_emergency_id, previous_id)

    def test_stop_interrupts_interval_wait(self):
        plugin = self.create_plugin(config=FakeConfig(interval=60))
        self.assertTrue(plugin.start_emergency())
        started = time.monotonic()
        self.assertTrue(plugin.stop_emergency(send_clear=False))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(plugin.emergency_active)

    def test_handle_incoming_accepts_private_app_alias_and_sends_ack(self):
        interface = FakeInterface()
        plugin = self.create_plugin(interface=interface)
        with mock.patch.object(plugin, "check_alert_radius") as radius_check:
            plugin.handle_incoming(self.emergency_packet(), interface)
        self.assertIn(456, plugin.active_emergency_info)
        ack_payload_bytes, ack_kwargs = interface.sent[-1]
        ack_payload = json.loads(ack_payload_bytes.decode("utf-8"))
        self.assertEqual(ack_payload["type"], "AERP_ACK")
        self.assertEqual(ack_kwargs["destinationId"], "!000001c8")
        self.assertTrue(ack_kwargs["wantAck"])
        radius_check.assert_called_once()

    def test_position_packet_without_raw_payload_is_still_processed(self):
        plugin = self.create_plugin()
        packet = {
            "from": 456,
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {"latitude": 40.001, "longitude": -111.001},
            },
        }
        with mock.patch.object(plugin, "check_alert_radius") as check:
            plugin.handle_incoming(packet, plugin.interface)
        check.assert_called_once()

    def test_mismatched_clear_does_not_remove_current_emergency(self):
        plugin = self.create_plugin()
        plugin.handle_incoming(self.emergency_packet("current"), plugin.interface)
        clear = {
            "from": 456,
            "decoded": {
                "portnum": 256,
                "payload": json.dumps(
                    {"type": "AERP_CLEAR", "emergency_id": "older"}
                ).encode(),
            },
        }
        plugin.handle_incoming(clear, plugin.interface)
        self.assertEqual(
            plugin.active_emergency_info[456]["message_id"], "current"
        )

    def test_delayed_emergency_after_matching_clear_is_ignored(self):
        interface = FakeInterface()
        plugin = self.create_plugin(interface=interface)
        packet = self.emergency_packet("resolved")
        plugin.handle_incoming(packet, interface)
        clear = {
            "from": 456,
            "decoded": {
                "portnum": 256,
                "payload": json.dumps(
                    {"type": "AERP_CLEAR", "emergency_id": "resolved"}
                ).encode(),
            },
        }
        plugin.handle_incoming(clear, interface)
        plugin.handle_incoming(packet, interface)
        self.assertNotIn(456, plugin.active_emergency_info)

    def test_older_incident_cannot_replace_newer_incident_from_same_node(self):
        plugin = self.create_plugin()
        plugin.handle_incoming(
            self.emergency_packet("newer", timestamp=200), plugin.interface
        )
        plugin.handle_incoming(
            self.emergency_packet("older", timestamp=100), plugin.interface
        )

        self.assertEqual(plugin.active_emergency_info[456]["message_id"], "newer")

    def test_ack_uses_local_receive_time_not_untrusted_sender_clock(self):
        plugin = self.create_plugin()
        plugin.acknowledgements["mine"] = {}
        packet = {
            "from": 456,
            "decoded": {
                "portnum": 256,
                "payload": json.dumps(
                    {
                        "type": "AERP_ACK",
                        "emergency_id": "mine",
                        "timestamp": 0,
                    }
                ).encode(),
            },
        }
        before = time.time()
        plugin.handle_incoming(packet, plugin.interface)
        after = time.time()
        self.assertGreaterEqual(plugin.acknowledgements["mine"][456], before)
        self.assertLessEqual(plugin.acknowledgements["mine"][456], after)

    def test_invalid_sender_is_ignored(self):
        plugin = self.create_plugin()
        packet = self.emergency_packet(sender=None)
        plugin.handle_incoming(packet, plugin.interface)
        self.assertEqual(plugin.active_emergency_info, {})
        self.assertEqual(plugin.interface.sent, [])

    def test_get_status_tolerates_invalid_remote_timestamp(self):
        plugin = self.create_plugin()
        plugin.active_emergency_info[456] = {
            "message_id": "id",
            "message": "Help",
            "gps": {},
            "battery": None,
            "timestamp": 10**30,
            "received_at": time.time(),
            "last_seen": time.time(),
        }
        status = plugin.get_status()
        self.assertEqual(
            status["active_received_emergencies"]["!000001c8"]["sent_at"],
            "Unknown",
        )

    def test_close_stops_real_cleanup_worker_promptly(self):
        plugin = AERP(FakeInterface(), FakeConfig())
        plugin.close(send_clear=False)
        self.assertFalse(plugin._cleanup_thread.is_alive())

    def test_cleanup_removes_stale_local_and_remote_state(self):
        class OneCycleEvent:
            def __init__(self):
                self.finished = False

            def is_set(self):
                return self.finished

            def wait(self, _timeout):
                self.finished = True
                return False

        plugin = self.create_plugin()
        plugin.acknowledgements["old-local"] = {456: 0}
        plugin._emergency_created_at["old-local"] = 0
        plugin.active_emergency_info[456] = {
            "message_id": "old-remote",
            "last_seen": 0,
        }
        plugin._cleared_emergencies[(456, "resolved")] = 0
        plugin._shutdown_event = OneCycleEvent()

        with mock.patch("aerp.plugin.time.time", return_value=1000):
            plugin._background_cleanup()

        self.assertNotIn("old-local", plugin.acknowledgements)
        self.assertNotIn(456, plugin.active_emergency_info)
        self.assertEqual(plugin._cleared_emergencies, {})

    def test_proximity_alert_logs_only_on_boundary_crossing(self):
        plugin = self.create_plugin()
        with self.assertLogs("aerp.plugin", level="INFO") as captured:
            plugin.check_alert_radius({}, 40.0, -111.0, 456, "!000001c8")
            plugin.check_alert_radius({}, 40.0, -111.0, 456, "!000001c8")
            plugin.check_alert_radius({}, 41.0, -111.0, 456, "!000001c8")

        alert_lines = [line for line in captured.output if "PROXIMITY ALERT" in line]
        self.assertEqual(len(alert_lines), 1)
        self.assertNotIn(456, plugin._proximity_inside)

    def test_oversized_outbound_payload_is_rejected_before_radio_api(self):
        plugin = self.create_plugin()

        queued = plugin._send_aerp_payload(
            {"message": "x" * 500}, 256, "test payload"
        )

        self.assertFalse(queued)
        self.assertEqual(plugin.interface.sent, [])

    def test_disconnect_stops_broadcast_and_resets_node_identity(self):
        plugin = self.create_plugin()
        self.assertTrue(plugin.start_emergency())

        plugin.on_connection_change(plugin.interface, False)

        self.assertFalse(plugin.emergency_active)
        self.assertIsNone(plugin.my_node_num)
        self.assertEqual(plugin.my_node_id, "Unknown")


class AERPEndToEndTests(unittest.TestCase):
    def test_two_nodes_complete_emergency_ack_and_clear_flow(self):
        plugins = {}

        class MeshInterface(FakeInterface):
            def sendData(self, data, **kwargs):
                super().sendData(data, **kwargs)
                destination = kwargs.get("destinationId", "^all")
                if destination == "^all":
                    recipients = [
                        plugin
                        for node_num, plugin in plugins.items()
                        if node_num != self.myInfo.my_node_num
                    ]
                else:
                    destination_num = int(str(destination).lstrip("!"), 16)
                    recipients = [plugins[destination_num]] if destination_num in plugins else []

                for recipient in recipients:
                    recipient.handle_incoming(
                        {
                            "from": self.myInfo.my_node_num,
                            "decoded": {
                                "portnum": kwargs["portNum"],
                                "payload": data,
                            },
                        },
                        recipient.interface,
                    )
                return object()

        sender_interface = MeshInterface(my_node_num=100)
        receiver_interface = MeshInterface(my_node_num=200)
        config = FakeConfig(port=300)
        with mock.patch.object(AERP, "_background_cleanup", return_value=None):
            sender = AERP(sender_interface, config)
            receiver = AERP(receiver_interface, config)
        plugins[100] = sender
        plugins[200] = receiver

        self.assertTrue(sender.start_emergency())
        emergency_id = sender.last_emergency_id
        self.assertIn(100, receiver.active_emergency_info)
        self.assertIn(200, sender.acknowledgements[emergency_id])

        self.assertTrue(sender.stop_emergency(send_clear=True))

        self.assertNotIn(100, receiver.active_emergency_info)
        sender_types = [json.loads(data)["type"] for data, _ in sender_interface.sent]
        receiver_types = [json.loads(data)["type"] for data, _ in receiver_interface.sent]
        self.assertEqual(sender_types.count("AERP_EMERGENCY"), 1)
        self.assertEqual(sender_types.count("AERP_CLEAR"), 3)
        self.assertEqual(receiver_types, ["AERP_ACK"])
        sender.close(send_clear=False)
        receiver.close(send_clear=False)


class ConfigurationTests(unittest.TestCase):
    def write_config(self, path, **overrides):
        config = {
            CONFIG_INTERVAL: 60,
            CONFIG_PORT: 256,
            CONFIG_MESSAGE: "SOS",
            CONFIG_RADIUS: 1000,
            CONFIG_ACK_TIMEOUT: 300,
            CONFIG_ENABLED: False,
        }
        config.update(overrides)
        path.write_text(json.dumps(config), encoding="utf-8")

    def test_missing_config_is_created_as_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "aerp.json"
            manager = ConfigManager(str(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), manager.config)

    def test_boolean_is_not_accepted_as_integer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aerp.json"
            self.write_config(path, interval=True, emergency_port=False)
            manager = ConfigManager(str(path))
            self.assertEqual(manager.get(CONFIG_INTERVAL), 60)
            self.assertEqual(manager.get(CONFIG_PORT), 256)

    def test_oversized_message_falls_back_to_safe_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aerp.json"
            self.write_config(path, emergency_message="x" * 81)
            manager = ConfigManager(str(path))
            self.assertNotEqual(manager.get(CONFIG_MESSAGE), "x" * 81)

    def test_control_characters_in_message_fall_back_to_safe_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aerp.json"
            self.write_config(path, emergency_message="SOS\nforged log line")
            manager = ConfigManager(str(path))
            self.assertEqual(manager.get(CONFIG_MESSAGE), "SOS! Emergency situation detected.")

    def test_non_private_port_falls_back_to_private_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aerp.json"
            self.write_config(path, emergency_port=0)
            manager = ConfigManager(str(path))
            self.assertEqual(manager.get(CONFIG_PORT), 256)


class UtilityTests(unittest.TestCase):
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
        self.assertEqual(get_location_from_packet(packet), (12.3456789, 98.7654321))

    def test_non_finite_coordinates_are_rejected(self):
        self.assertEqual(
            build_gps_payload({"latitude": math.nan, "longitude": 1}), {}
        )
        self.assertEqual(calculate_distance(math.inf, 0, 0, 0), float("inf"))

    def test_invalid_battery_values_are_rejected(self):
        self.assertIsNone(extract_battery_level({"batteryLevel": True}))
        self.assertIsNone(extract_battery_level({"batteryLevel": 102}))
        self.assertEqual(extract_battery_level({"battery_level": 101}), 101)

    def test_node_format_rejects_out_of_range_values(self):
        self.assertEqual(format_node_id(-1), "Unknown")
        self.assertEqual(format_node_id(2**32), "Unknown")
        self.assertEqual(format_node_id(0), "!00000000")


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

    def test_serial_connection_error_returns_none(self):
        with mock.patch.object(
            cli.meshtastic.serial_interface,
            "SerialInterface",
            side_effect=RuntimeError("port busy"),
        ):
            self.assertIsNone(cli.setup_meshtastic_interface(device_path="/dev/fake"))


if __name__ == "__main__":
    unittest.main()
