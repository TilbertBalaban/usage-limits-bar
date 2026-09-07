"""Read local provider credentials and fetch account usage limits."""

import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

CLAUDE = "claude"
CODEX = "codex"
PROVIDERS = (CLAUDE, CODEX)
PROVIDER_NAMES = {CLAUDE: "Claude", CODEX: "Codex"}

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"
CLAUDE_CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
CODEX_CREDENTIALS_FILE = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
CACHE_DIR = Path.home() / "Library" / "Caches" / "usage-limits-bar"
CACHE_MAX_AGE = 3600
REQUEST_TIMEOUT = 15
WARN_PERCENT = 80


class ProviderError(Exception):
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(provider)


class CredentialsNotFound(ProviderError):
    pass


class TokenRejected(ProviderError):
    pass


class UsageRateLimited(ProviderError):
    pass


@dataclass(frozen=True)
class Credentials:
    token: str
    account_id: Optional[str] = None


@dataclass
class Limit:
    kind: str
    label: str
    percent: float
    severity: str
    resets_at: Optional[datetime]
    is_active: bool
    provider: str = CLAUDE

    @property
    def exhausted(self) -> bool:
        return self.percent >= 100

    @property
    def warning(self) -> bool:
        return self.percent >= WARN_PERCENT or self.severity not in ("normal", "")


def _read_keychain(service: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def _read_file(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except OSError:
        return None


def get_credentials(provider: str) -> Credentials:
    if provider == CLAUDE:
        raw = _read_keychain(CLAUDE_KEYCHAIN_SERVICE) or _read_file(CLAUDE_CREDENTIALS_FILE)
        if not raw:
            raise CredentialsNotFound(provider)
        return parse_claude_credentials(raw)
    if provider == CODEX:
        environment_token = os.environ.get("CODEX_ACCESS_TOKEN")
        if environment_token:
            return Credentials(environment_token, os.environ.get("CODEX_ACCOUNT_ID"))
        raw = _read_file(CODEX_CREDENTIALS_FILE)
        if not raw:
            raise CredentialsNotFound(provider)
        return parse_codex_credentials(raw)
    raise ValueError("Unknown provider: %s" % provider)


def parse_claude_credentials(raw: str) -> Credentials:
    try:
        token = json.loads(raw).get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, AttributeError):
        raise CredentialsNotFound(CLAUDE)
    if not token:
        raise CredentialsNotFound(CLAUDE)
    return Credentials(token)


def parse_codex_credentials(raw: str) -> Credentials:
    try:
        tokens = json.loads(raw).get("tokens") or {}
        token = tokens.get("access_token")
        account_id = tokens.get("account_id")
    except (json.JSONDecodeError, AttributeError):
        raise CredentialsNotFound(CODEX)
    if not token:
        raise CredentialsNotFound(CODEX)
    return Credentials(token, account_id)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca"):
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    if os.path.exists("/etc/ssl/cert.pem"):
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


def fetch_usage(provider: str, credentials: Credentials) -> dict:
    if provider == CLAUDE:
        url = CLAUDE_USAGE_URL
        headers = {
            "Authorization": "Bearer " + credentials.token,
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            "User-Agent": "usage-limits-bar",
        }
    elif provider == CODEX:
        url = CODEX_USAGE_URL
        headers = {
            "Authorization": "Bearer " + credentials.token,
            "User-Agent": "usage-limits-bar",
        }
        if credentials.account_id:
            headers["ChatGPT-Account-Id"] = credentials.account_id
    else:
        raise ValueError("Unknown provider: %s" % provider)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise TokenRejected(provider) from error
        if error.code == 429:
            raise UsageRateLimited(provider) from error
        raise


def cache_file(provider: str) -> Path:
    return CACHE_DIR / (provider + ".json")


def save_cache(provider: str, data: dict, path: Optional[Path] = None) -> None:
    path = path or cache_file(provider)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"saved_at": time.time(), "data": data}))
    except OSError:
        pass


def load_cache(
    provider: str,
    path: Optional[Path] = None,
    max_age: float = CACHE_MAX_AGE,
) -> Optional[dict]:
    path = path or cache_file(provider)
    try:
        payload = json.loads(path.read_text())
        if time.time() - float(payload["saved_at"]) > max_age:
            return None
        return payload["data"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def get_limits(provider: str) -> List[Limit]:
    return parse_limits(provider, fetch_usage(provider, get_credentials(provider)))


def parse_limits(provider: str, data: dict) -> List[Limit]:
    if provider == CLAUDE:
        return _parse_claude_limits(data)
    if provider == CODEX:
        return _parse_codex_limits(data)
    raise ValueError("Unknown provider: %s" % provider)


def _parse_claude_limits(data: dict) -> List[Limit]:
    limits = []
    for item in data.get("limits") or []:
        limits.append(Limit(
            provider=CLAUDE,
            kind=item.get("kind") or "",
            label=_claude_label(item),
            percent=float(item.get("percent") or 0),
            severity=item.get("severity") or "normal",
            resets_at=_parse_iso(item.get("resets_at")),
            is_active=bool(item.get("is_active")),
        ))
    return limits


def _parse_codex_limits(data: dict) -> List[Limit]:
    rate_limit = data.get("rate_limit") or {}
    limits = []
    for kind, key in (("session", "primary_window"), ("weekly_all", "secondary_window")):
        window = rate_limit.get(key)
        if not isinstance(window, dict):
            continue
        limits.append(Limit(
            provider=CODEX,
            kind=kind,
            label=_window_label(window.get("limit_window_seconds"), kind),
            percent=float(window.get("used_percent") or 0),
            severity="normal",
            resets_at=_parse_timestamp(window.get("reset_at")),
            is_active=kind == "session",
        ))
    return limits


def _claude_label(item: dict) -> str:
    kind = item.get("kind") or ""
    if kind == "session":
        return "5-Hour Limit"
    if kind == "weekly_all":
        return "7-Day Limit"
    if kind == "weekly_scoped":
        model = ((item.get("scope") or {}).get("model") or {}).get("display_name")
        return "7-Day (%s)" % model if model else "7-Day (scoped)"
    return kind.replace("_", " ").capitalize() or "Limit"


def _window_label(seconds: object, kind: str) -> str:
    try:
        total_seconds = int(seconds)
    except (TypeError, ValueError):
        return "Session Limit" if kind == "session" else "Weekly Limit"
    if total_seconds % 86400 == 0:
        return "%d-Day Limit" % (total_seconds // 86400)
    if total_seconds % 3600 == 0:
        return "%d-Hour Limit" % (total_seconds // 3600)
    return "Session Limit" if kind == "session" else "Weekly Limit"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_timestamp(value: object) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def limits_by_provider(limits: List[Limit]) -> Dict[str, List[Limit]]:
    return {
        provider: [limit for limit in limits if limit.provider == provider]
        for provider in PROVIDERS
    }


def primary_limit(limits: List[Limit]) -> Optional[Limit]:
    if not limits:
        return None
    session = next((limit for limit in limits if limit.kind == "session"), None)
    worst = max(limits, key=lambda limit: limit.percent)
    if worst.warning and (session is None or worst.percent > session.percent):
        return worst
    return session or worst


def time_until(resets_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    if resets_at is None:
        return "?"
    now = now or datetime.now(timezone.utc)
    seconds = (resets_at - now).total_seconds()
    if seconds <= 0:
        return "now"
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % max(minutes, 1)


def local_reset_time(resets_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    if resets_at is None:
        return "?"
    now = (now or datetime.now(timezone.utc)).astimezone()
    local = resets_at.astimezone()
    if local.date() == now.date():
        return local.strftime("%H:%M")
    return local.strftime("%a %H:%M")


def reset_label(resets_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    if resets_at is None:
        return "?"
    now_local = (now or datetime.now(timezone.utc)).astimezone()
    local = resets_at.astimezone()
    time_part = local.strftime("%-I:%M %p")
    if local.date() == now_local.date():
        return "Today " + time_part
    return local.strftime("%b %-d ") + time_part


def bar_title(limits: List[Limit], now: Optional[datetime] = None) -> str:
    primary = primary_limit(limits)
    if primary is None:
        return "?"
    if primary.exhausted:
        return "⛔ %s" % time_until(primary.resets_at, now)
    prefix = "⚠️ " if any(limit.warning for limit in limits) else ""
    return "%s%d%% · %s" % (
        prefix,
        round(primary.percent),
        time_until(primary.resets_at, now),
    )


def limit_line(limit: Limit, now: Optional[datetime] = None) -> str:
    return "%s %s: %d%% — resets %s (in %s)" % (
        PROVIDER_NAMES[limit.provider],
        limit.label,
        round(limit.percent),
        local_reset_time(limit.resets_at, now),
        time_until(limit.resets_at, now),
    )
