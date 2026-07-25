import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_control import RemoteAgentConfig, RemoteAgentConfigStore, RemoteControlAgent


class RemoteAgentConfigTests(unittest.TestCase):
    def test_config_round_trip_preserves_pc_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RemoteAgentConfigStore(Path(directory))
            config = RemoteAgentConfig(
                enabled=True,
                gateway_url="https://ai.lhksoul.com/",
                device_id="pc-001",
                device_name="거실 윈도우",
                agent_token="secret-token",
            )
            store.save(config)

            loaded = store.load()

            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.gateway_url, "https://ai.lhksoul.com")
            self.assertEqual(loaded.device_id, "pc-001")
            self.assertEqual(loaded.device_name, "거실 윈도우")
            self.assertEqual(loaded.agent_token, "secret-token")
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["device_id"], "pc-001")

    @patch("remote_control.platform.release", return_value="15.0")
    @patch("remote_control.platform.system", return_value="Darwin")
    def test_agent_builds_secure_websocket_url(self, _system, _release) -> None:
        config = RemoteAgentConfig(
            enabled=True,
            gateway_url="https://ai.lhksoul.com",
            device_id="mac-001",
            device_name="엄마 맥",
            agent_token="secret-token",
        )
        agent = RemoteControlAgent(config, "1.2.3", lambda _job: None, lambda _status, _message: None)

        socket_url = agent._agent_socket_url()

        self.assertTrue(socket_url.startswith("wss://ai.lhksoul.com/api/agent?"))
        self.assertIn("deviceId=mac-001", socket_url)
        self.assertIn("name=%EC%97%84%EB%A7%88+%EB%A7%A5", socket_url)
        self.assertIn("version=1.2.3", socket_url)


if __name__ == "__main__":
    unittest.main()
