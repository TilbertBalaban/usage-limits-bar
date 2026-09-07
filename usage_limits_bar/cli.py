import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

from . import VERSION
from .limits import (
    CLAUDE,
    PROVIDER_NAMES,
    PROVIDERS,
    CredentialsNotFound,
    TokenRejected,
    UsageRateLimited,
    get_limits,
    limit_line,
)

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.usage-limits-bar.plist"


def cmd_status() -> int:
    successes = 0
    for provider in PROVIDERS:
        try:
            limits = get_limits(provider)
        except CredentialsNotFound:
            command = "claude" if provider == CLAUDE else "codex login"
            print("No %s credentials found — run `%s` and sign in first." % (
                PROVIDER_NAMES[provider], command))
            continue
        except TokenRejected:
            command = "Claude Code" if provider == CLAUDE else "`codex login`"
            print("%s token was rejected — sign in with %s again." % (
                PROVIDER_NAMES[provider], command))
            continue
        except UsageRateLimited:
            print("%s usage API is rate-limited — try again in a minute." %
                  PROVIDER_NAMES[provider])
            continue
        successes += 1
        if not limits:
            print("No %s limits reported for this account." % PROVIDER_NAMES[provider])
        for limit in limits:
            print(limit_line(limit))
    return 0 if successes else 1


def cmd_autostart(state: str) -> int:
    if state == "off":
        if LAUNCH_AGENT.exists():
            subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True)
            LAUNCH_AGENT.unlink()
        print("Autostart disabled.")
        return 0
    executable = Path(sys.argv[0]).resolve()
    plist = {
        "Label": "com.usage-limits-bar",
        "ProgramArguments": [str(executable)],
        "RunAtLoad": True,
    }
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENT.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True)
    subprocess.run(["launchctl", "load", str(LAUNCH_AGENT)], capture_output=True)
    print("Autostart enabled (%s)." % LAUNCH_AGENT)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="usage-limits-bar",
        description="Claude and Codex limits and reset times in the macOS menu bar.",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="print current limits to the terminal")
    autostart = sub.add_parser("autostart", help="start automatically at login")
    autostart.add_argument("state", choices=["on", "off"])
    args = parser.parse_args()

    if args.command == "status":
        return cmd_status()
    if args.command == "autostart":
        return cmd_autostart(args.state)

    from .menubar import main as run_menubar
    run_menubar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
