import json
import unittest

from usage_limits_bar.limits import CLAUDE, CODEX, Credentials
from usage_limits_bar.pairing import pairing_payload


class TestPairingPayload(unittest.TestCase):
    def test_claude_payload(self):
        payload = json.loads(pairing_payload(
            CLAUDE, Credentials("sk-ant-oat01-example")))
        self.assertEqual(payload, {
            "version": 1,
            "provider": "claude",
            "credentials": {"token": "sk-ant-oat01-example"},
        })

    def test_codex_payload_includes_account(self):
        payload = json.loads(pairing_payload(
            CODEX, Credentials("header.payload.signature", "account-example")))
        self.assertEqual(payload["credentials"]["accountId"], "account-example")


if __name__ == "__main__":
    unittest.main()
