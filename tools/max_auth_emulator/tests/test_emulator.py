from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from auth.max_validation import validate_max_init_data  # noqa: E402
from platform_core.errors import DomainError  # noqa: E402
from tools.max_auth_emulator.signer import build_launch_url, generate_init_data  # noqa: E402


TOKEN = "emulator-test-token"


class MaxAuthEmulatorTests(unittest.TestCase):
    def test_generated_init_data_is_accepted_by_backend_validator(self):
        raw = generate_init_data(
            TOKEN,
            987654321,
            query_id="test-query",
            first_name="Иван",
            last_name="Иванов",
            username="ivan",
        )

        user = validate_max_init_data(raw, TOKEN)

        self.assertEqual(user, {
            "id": 987654321,
            "first_name": "Иван",
            "last_name": "Иванов",
            "username": "ivan",
        })

    def test_launch_url_round_trip_preserves_raw_init_data(self):
        raw = generate_init_data(TOKEN, 42, query_id="test-query")

        launch_url = build_launch_url("https://example.org/institution?source=test", raw)
        parsed = urlsplit(launch_url)
        fragment = parse_qs(parsed.fragment, strict_parsing=True)

        self.assertEqual(parsed.path, "/institution")
        self.assertEqual(parsed.query, "source=test")
        self.assertEqual(fragment["WebAppData"], [raw])
        self.assertEqual(fragment["WebAppPlatform"], ["web"])

    def test_modified_user_id_is_rejected(self):
        raw = generate_init_data(TOKEN, 123456789, query_id="test-query")
        pairs = dict(parse_qsl(raw, strict_parsing=True))
        pairs["user"] = pairs["user"].replace("123456789", "123456788")

        with self.assertRaises(DomainError):
            validate_max_init_data(urlencode(pairs), TOKEN)

    def test_expired_init_data_is_rejected(self):
        raw = generate_init_data(TOKEN, 1, auth_date=int(time.time()) - 301)

        with self.assertRaises(DomainError):
            validate_max_init_data(raw, TOKEN)

    def test_non_positive_user_id_is_rejected_by_generator(self):
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                generate_init_data(TOKEN, value)


if __name__ == "__main__":
    unittest.main()
