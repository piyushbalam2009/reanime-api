#!/usr/bin/env python3
"""Fetch public Re:ANIME catalog data from GitHub Actions without FlareSolverr."""
import json
import os
import sys
from pathlib import Path
import httpx

BASE_URL = os.getenv("REANIME_BASE_URL", "https://reanime.cz").rstrip("/")
MODE = os.getenv("FETCH_MODE", "search")
QUERY = os.getenv("SEARCH_QUERY", "One Piece")
ANIME_ID = os.getenv("ANIME_ID", "")
PAGE = max(1, int(os.getenv("PAGE", "1")))

def main() -> int:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE_URL + "/",
    }
    if MODE == "search":
        path, params = "/api/v1/search", {"q": QUERY, "limit": 20, "offset": (PAGE - 1) * 20}
    elif MODE == "details":
        if not ANIME_ID:
            raise ValueError("ANIME_ID is required for details mode")
        path, params = f"/api/v1/anime/{ANIME_ID}", None
    elif MODE == "episodes":
        if not ANIME_ID:
            raise ValueError("ANIME_ID is required for episodes mode")
        path, params = f"/api/v1/anime/{ANIME_ID}/episodes", {"limit": 2000}
    else:
        raise ValueError(f"Unsupported FETCH_MODE: {MODE}")

    url = BASE_URL + path
    print(f"Requesting {url}; mode={MODE}; FlareSolverr=disabled")
    with httpx.Client(timeout=25, follow_redirects=True, headers=headers) as client:
        response = client.get(url, params=params)

    print(f"HTTP {response.status_code}; content-type={response.headers.get('content-type', 'unknown')}")
    if response.status_code in (403, 429):
        print("GitHub Actions traffic was denied or rate-limited. No challenge bypass will be attempted.")
        print(response.text[:1200])
        return 2
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        print("Upstream response was not JSON:")
        print(response.text[:1200])
        return 3

    output = {"source": BASE_URL, "mode": MODE, "request": {"path": path, "params": params}, "data": payload}
    target = Path("artifacts/reanime-result.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    if os.getenv("GITHUB_STEP_SUMMARY"):
        if isinstance(payload, dict):
            keys = ", ".join(payload.keys())
            records = payload.get("results", payload.get("data", []))
            count = len(records) if isinstance(records, list) else "n/a"
        elif isinstance(payload, list):
            keys, count = "array", len(payload)
        else:
            keys, count = type(payload).__name__, "n/a"
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(
            "## Re:ANIME fetch result\n\n"
            f"- Mode: {MODE}\n- HTTP: {response.status_code}\n"
            f"- Top-level fields: {keys}\n- Result count: {count}\n"
            "- FlareSolverr: not used\n\n"
            "Full JSON response is attached as the reiname-data artifact.\n",
            encoding="utf-8",
        )
    print(json.dumps(output, ensure_ascii=False, indent=2)[:12000])
    print(f"Saved complete response to {target}")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
