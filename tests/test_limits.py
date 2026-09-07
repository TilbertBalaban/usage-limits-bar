import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from usage_limits_bar.limits import (
    CLAUDE,
    CODEX,
    CredentialsNotFound,
    Limit,
    bar_title,
    limit_line,
    limits_by_provider,
    load_cache,
    local_reset_time,
    parse_claude_credentials,
    parse_codex_credentials,
    parse_limits,
    primary_limit,
    reset_label,
    save_cache,
    time_until,
)

NOW = datetime(2026, 9, 2, 12, 30, tzinfo=timezone.utc)

CLAUDE_RESPONSE = {
    "limits": [
        {"kind": "session", "group": "session", "percent": 41, "severity": "normal",
         "resets_at": "2026-09-02T15:10:00.026681+00:00", "scope": None, "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 9, "severity": "normal",
         "resets_at": "2026-09-04T22:00:00.026699+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 13, "severity": "normal",
         "resets_at": "2026-09-04T22:00:00.026844+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
         "is_active": False},
    ],
}

CODEX_RESPONSE = {
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 32,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 9600,
            "reset_at": 1788352200,
        },
        "secondary_window": {
            "used_percent": 57,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 260000,
            "reset_at": 1788609600,
        },
    },
}


def make_limit(kind="session", label="5-Hour Limit", percent=41.0, severity="normal",
               resets_at=NOW + timedelta(hours=2, minutes=40), is_active=True,
               provider=CLAUDE):
    return Limit(kind=kind, label=label, percent=percent, severity=severity,
                 resets_at=resets_at, is_active=is_active, provider=provider)


class TestCredentials(unittest.TestCase):
    def test_claude_credentials(self):
        raw = '{"claudeAiOauth": {"accessToken": "sk-ant-oat01-abc"}}'
        self.assertEqual(parse_claude_credentials(raw).token, "sk-ant-oat01-abc")

    def test_codex_credentials(self):
        raw = '{"tokens": {"access_token": "token", "account_id": "account"}}'
        credentials = parse_codex_credentials(raw)
        self.assertEqual(credentials.token, "token")
        self.assertEqual(credentials.account_id, "account")

    def test_missing_tokens(self):
        with self.assertRaises(CredentialsNotFound):
            parse_claude_credentials('{"claudeAiOauth": {}}')
        with self.assertRaises(CredentialsNotFound):
            parse_codex_credentials('{"tokens": {}}')

    def test_invalid_json(self):
        with self.assertRaises(CredentialsNotFound):
            parse_claude_credentials("not json")
        with self.assertRaises(CredentialsNotFound):
            parse_codex_credentials("not json")


class TestParseLimits(unittest.TestCase):
    def test_claude_response(self):
        limits = parse_limits(CLAUDE, CLAUDE_RESPONSE)
        self.assertEqual(len(limits), 3)
        session, weekly, scoped = limits
        self.assertEqual(session.label, "5-Hour Limit")
        self.assertEqual(session.percent, 41)
        self.assertEqual(session.provider, CLAUDE)
        self.assertTrue(session.is_active)
        self.assertEqual(session.resets_at.tzinfo.utcoffset(None), timedelta(0))
        self.assertEqual(weekly.label, "7-Day Limit")
        self.assertEqual(scoped.label, "7-Day (Fable)")

    def test_codex_response(self):
        session, weekly = parse_limits(CODEX, CODEX_RESPONSE)
        self.assertEqual(session.label, "5-Hour Limit")
        self.assertEqual(session.percent, 32)
        self.assertEqual(session.provider, CODEX)
        self.assertEqual(session.resets_at, datetime.fromtimestamp(1788352200, timezone.utc))
        self.assertEqual(weekly.label, "7-Day Limit")
        self.assertEqual(weekly.percent, 57)

    def test_empty_and_missing(self):
        self.assertEqual(parse_limits(CLAUDE, {}), [])
        self.assertEqual(parse_limits(CLAUDE, {"limits": None}), [])
        self.assertEqual(parse_limits(CODEX, {}), [])

    def test_unknown_claude_kind_gets_readable_label(self):
        limits = parse_limits(CLAUDE, {"limits": [{"kind": "weekly_opus", "percent": 5}]})
        self.assertEqual(limits[0].label, "Weekly opus")
        self.assertIsNone(limits[0].resets_at)

    def test_groups_limits_by_provider(self):
        limits = parse_limits(CLAUDE, CLAUDE_RESPONSE) + parse_limits(CODEX, CODEX_RESPONSE)
        grouped = limits_by_provider(limits)
        self.assertEqual(len(grouped[CLAUDE]), 3)
        self.assertEqual(len(grouped[CODEX]), 2)


class TestPrimaryLimit(unittest.TestCase):
    def test_prefers_session(self):
        session = make_limit(percent=20)
        weekly = make_limit(kind="weekly_all", label="7-Day Limit", percent=50)
        self.assertIs(primary_limit([weekly, session]), session)

    def test_worse_limit_wins_when_warning(self):
        session = make_limit(percent=20)
        weekly = make_limit(kind="weekly_all", label="7-Day Limit", percent=85)
        self.assertIs(primary_limit([session, weekly]), weekly)

    def test_no_limits(self):
        self.assertIsNone(primary_limit([]))


class TestTimeFormatting(unittest.TestCase):
    def test_time_until(self):
        self.assertEqual(time_until(NOW + timedelta(hours=2, minutes=40), NOW), "2h 40m")
        self.assertEqual(time_until(NOW + timedelta(minutes=5), NOW), "5m")
        self.assertEqual(time_until(NOW + timedelta(days=2, hours=9, minutes=30), NOW), "2d 9h")
        self.assertEqual(time_until(NOW - timedelta(minutes=1), NOW), "now")
        self.assertEqual(time_until(None, NOW), "?")

    def test_local_reset_time_same_day_has_no_weekday(self):
        self.assertNotIn(" ", local_reset_time(NOW + timedelta(hours=1), NOW))
        self.assertIn(" ", local_reset_time(NOW + timedelta(days=2), NOW))

    def test_reset_label(self):
        same_day = NOW.astimezone().replace(hour=18, minute=10)
        self.assertEqual(reset_label(same_day, NOW), "Today 6:10 PM")
        other_day = datetime(2026, 9, 5, 1, 0, tzinfo=NOW.astimezone().tzinfo)
        self.assertEqual(reset_label(other_day, NOW), "Sep 5 1:00 AM")
        self.assertEqual(reset_label(None, NOW), "?")


class TestBarTitle(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(bar_title([make_limit()], NOW), "41% · 2h 40m")

    def test_warning(self):
        self.assertEqual(bar_title([make_limit(percent=85)], NOW), "⚠️ 85% · 2h 40m")

    def test_exhausted_shows_time_to_reset(self):
        self.assertEqual(bar_title([make_limit(percent=100)], NOW), "⛔ 2h 40m")

    def test_no_data(self):
        self.assertEqual(bar_title([], NOW), "?")


class TestLimitLine(unittest.TestCase):
    def test_contains_provider_label_percent_and_countdown(self):
        line = limit_line(make_limit(), NOW)
        self.assertIn("Claude 5-Hour Limit: 41%", line)
        self.assertIn("in 2h 40m", line)


class TestCache(unittest.TestCase):
    def test_roundtrip_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "usage.json"
            self.assertIsNone(load_cache(CLAUDE, path))
            save_cache(CLAUDE, CLAUDE_RESPONSE, path)
            self.assertEqual(load_cache(CLAUDE, path), CLAUDE_RESPONSE)
            self.assertIsNone(load_cache(CLAUDE, path, max_age=-1))
            path.write_text("garbage")
            self.assertIsNone(load_cache(CLAUDE, path))


if __name__ == "__main__":
    unittest.main()
