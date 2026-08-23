from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: wait_for_http.py URL [TIMEOUT_SECONDS] [EXPECTED_TEXT]", file=sys.stderr)
        return 2

    url = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    expected_text = sys.argv[3] if len(sys.argv) > 3 else ""
    deadline = time.monotonic() + timeout
    last_error = "no response"

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
                if not 200 <= response.status < 500:
                    last_error = f"unexpected HTTP status {response.status}"
                elif expected_text and expected_text not in body:
                    last_error = f"response did not identify {expected_text}"
                else:
                    print(f"ready: {url} ({response.status})")
                    return 0
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.4)

    print(f"timeout: {url}; last error: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
