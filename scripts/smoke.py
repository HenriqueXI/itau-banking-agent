#!/usr/bin/env python3
"""Smoke test for the composed stack (NFR-6): /health must return 200 with
every component ok. Stdlib only."""

import json
import sys
import time
import urllib.error
import urllib.request

HEALTH_URL = "http://localhost:8000/health"
ATTEMPTS = 30
DELAY_SECONDS = 2


def main() -> int:
    last_error = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=5) as response:
                body = json.loads(response.read())
                if response.status == 200 and body.get("status") == "ok":
                    print(f"smoke: OK — components: {body['components']}")
                    return 0
                last_error = f"status={response.status} body={body}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = str(exc)
        print(f"smoke: attempt {attempt}/{ATTEMPTS} not healthy yet ({last_error})")
        time.sleep(DELAY_SECONDS)

    print(f"smoke: FAILED — {last_error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
