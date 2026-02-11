#!/usr/bin/env python3
"""Check OpenRouter API key rate limits and credits remaining."""

import json
import os
import sys

from urllib.request import Request, urlopen


def main():
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Error: OPENROUTER_API_KEY env var not set")
        sys.exit(1)

    req = Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
