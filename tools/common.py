"""Project-local paths and loopback HTTP; no dotenv, keys, or external services."""

import json
import urllib.request
import urllib.error
from urllib.parse import urlparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / ".demo"


def http(url, method="GET", data=None, timeout=15):
    parsed = urlparse(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Only a local HTTP service is supported by this showcase.")
    raw = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=raw, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode(), response.url
    except urllib.error.HTTPError as error:
        return error.code, {}, "Local service returned an error", url
    except (urllib.error.URLError, TimeoutError):
        return 0, {}, "Local service is unavailable", url
