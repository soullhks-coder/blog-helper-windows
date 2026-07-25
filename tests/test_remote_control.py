import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
                pairing_password="",
            )
            store.save(config)

            loaded = store.load()

            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.gateway_url, "https://ai.lhksoul.com")
            self.assertEqual(loaded.device_id, "pc-001")
            self.assertEqual(loaded.device_name, "거실 윈도우")
            self.assertEqual(loaded.agent_token, "secret-token")
            self.assertEqual(loaded.pairing_password, "")
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["device_id"], "pc-001")
            self.assertEqual(payload["schema_version"], 2)

    def test_legacy_windows_config_is_migrated_to_pairing_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RemoteAgentConfigStore(Path(directory))
            store.path.write_text(
                json.dumps(
                    {
                        "enabled": False,
                        "gateway_url": "https://ai.lhksoul.com",
                        "device_id": "old-windows-id",
                        "device_name": "엄마 노트북",
                        "agent_token": "",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            loaded = store.load()

            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.device_id, "old-windows-id")
            migrated = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], 2)

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

    @patch("remote_control.urlopen")
    def test_agent_pairs_and_keeps_pc_specific_token(self, mocked_urlopen) -> None:
        response = MagicMock()
        response.read.return_value = b'{"ok":true,"deviceToken":"pc-specific-token"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        mocked_urlopen.return_value = response
        received_tokens: list[str] = []
        config = RemoteAgentConfig(
            enabled=True,
            gateway_url="https://ai.lhksoul.com",
            device_id="windows-001",
            device_name="엄마 윈도우",
            pairing_password="one-time-password",
        )
        agent = RemoteControlAgent(
            config,
            "1.2.3",
            lambda _job: None,
            lambda _status, _message: None,
            received_tokens.append,
        )

        agent._pair_device()

        self.assertEqual(agent.config.agent_token, "pc-specific-token")
        self.assertEqual(agent.config.pairing_password, "")
        self.assertEqual(received_tokens, ["pc-specific-token"])
        request = mocked_urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["deviceId"], "windows-001")
        self.assertEqual(body["password"], "one-time-password")


if __name__ == "__main__":
    unittest.main()
