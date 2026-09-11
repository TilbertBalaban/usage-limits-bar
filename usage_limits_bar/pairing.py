import json
import os

from .limits import Credentials

MOBILE_DOWNLOAD_URL = os.environ.get(
    "USAGE_LIMITS_MOBILE_DOWNLOAD_URL",
    "https://github.com/TilbertBalaban/usage-limits-mobile",
)


def pairing_payload(provider: str, credentials: Credentials) -> str:
    values = {"token": credentials.token}
    if credentials.account_id:
        values["accountId"] = credentials.account_id
    return json.dumps(
        {"version": 1, "provider": provider, "credentials": values},
        separators=(",", ":"),
    )
