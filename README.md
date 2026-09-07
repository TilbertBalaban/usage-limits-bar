# usage-limits-bar

[![PyPI](https://img.shields.io/pypi/v/usage-limits-bar?style=flat-square)](https://pypi.org/project/usage-limits-bar/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

**Claude Code and Codex usage limits, always visible in the macOS menu bar.**

See how close each signed-in account is to its session and weekly limits, and when those limits reset, before a coding session is interrupted.

## What it looks like

Each signed-in provider gets its own menu-bar item with compact ring gauges and a countdown to its active limit reset. Claude uses green and purple rings; Codex uses blue and teal. A provider that is not signed in is not shown. Warning states stay consistent across providers: orange at 80% and red with `!` at 100%.

| Signed-in providers | Menu bar | Dropdown |
|---|---|---|
| Claude only | One Claude item: green session ring, purple weekly ring, reset countdown | Claude session donut, weekly outer arc, Claude limits, sponsor / usage / refresh |
| Codex only | One Codex item: blue session ring, teal weekly ring, reset countdown | Codex session donut, weekly outer arc, Codex limits, sponsor / usage / refresh |
| Claude and Codex | Two independent items, one in each provider’s colors | Each item opens only its own provider’s limits and controls |

Click an item for the full picture. Every provider dropdown uses the same native layout: a large session donut, a thin weekly outer arc, and one row per reported limit with its reset time. Claude errors stay in Claude’s item; Codex errors stay in Codex’s item.

- Data for Claude and Codex refreshes independently every minute, so one unavailable provider does not hide the other.
- Every provider dropdown has three header buttons: sponsor, provider usage, and refresh. The gauges spin while fetching.
- The last successful response for each provider is cached in `~/Library/Caches/usage-limits-bar/` and shown immediately after a restart.
- The app checks once a day for a newer version and adds an **Update available** menu item when one exists.

## Install

Requires macOS 11+ and Python 3.9+. Pick whichever tool you already use:

| Method | Install | Update |
|---|---|---|
| **Homebrew** | `brew install tilbertbalaban/tap/usage-limits-bar` | `brew upgrade usage-limits-bar` |
| **uv** | `uv tool install usage-limits-bar` | `uv tool upgrade usage-limits-bar` |
| **pipx** | `pipx install usage-limits-bar` | `pipx upgrade usage-limits-bar` |
| **pip** | `pip install usage-limits-bar` | `pip install -U usage-limits-bar` |
| **From source** | `git clone https://github.com/TilbertBalaban/usage-limits-bar && cd usage-limits-bar && uv tool install .` | `git pull && uv tool install . --reinstall` |

Then:

```sh
usage-limits-bar                # launch the menu bar app and return immediately
usage-limits-bar autostart on   # start automatically at login
usage-limits-bar autostart off  # remove the login item
usage-limits-bar status         # print all available limits
```

After updating, quit the app from its menu and start it again. If autostart was configured under an earlier installation, disable it there once before enabling it with the new command.

## Provider setup

### Claude Code

Sign in with Claude Code by running `claude` and using `/login`. The app reads the OAuth token from the macOS Keychain service `Claude Code-credentials`, falling back to `~/.claude/.credentials.json`, then polls `api.anthropic.com/api/oauth/usage`.

Claude usage reporting works with Pro and Max plans.

### Codex and ChatGPT

Run `codex login` and choose ChatGPT sign-in. The app reads the OAuth session from `~/.codex/auth.json`, or `$CODEX_HOME/auth.json` when `CODEX_HOME` is set, then polls `chatgpt.com/backend-api/wham/usage`. `CODEX_ACCESS_TOKEN` and the optional `CODEX_ACCOUNT_ID` environment variables are also supported.

Codex limits are the usage included with the signed-in ChatGPT plan. API-key billing does not expose these ChatGPT plan windows.

Credentials are sent only to their matching provider: Claude tokens to `api.anthropic.com`, and Codex tokens to `chatgpt.com`. Tokens are never logged or copied into the cache.

## Support

If this app saves you from surprise rate limits, you can support development through the `$` button or at [base.monobank.ua/tilbertbalaban](https://base.monobank.ua/tilbertbalaban).

## Development

```sh
git clone https://github.com/TilbertBalaban/usage-limits-bar
cd usage-limits-bar
python3 -m unittest discover -s tests -v
python3 -m usage_limits_bar.cli status
```

The data layer ([usage_limits_bar/limits.py](usage_limits_bar/limits.py)) is standard-library only and unit-tested. The native menu shell ([usage_limits_bar/menubar.py](usage_limits_bar/menubar.py)) depends on PyObjC/AppKit.

## Troubleshooting

- **`?` in the menu bar** — open the menu to see which provider needs attention, then sign in with `claude` or `codex login`.
- **Usage API rate-limited** — the affected provider keeps showing its last cached data while the app backs off for a few minutes.
- **Codex shows no limits** — confirm Codex is signed in with ChatGPT, not only an OpenAI API key.
- **Requests stop below 100%** — provider APIs may round utilization or report it with a short delay; treat an orange ring as a cue to wrap up.
