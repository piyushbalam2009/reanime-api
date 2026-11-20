#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
ARTIFACT_DIR="${ARTIFACT_DIR:-curl-results}"
mkdir -p "$ARTIFACT_DIR"

request() {
  local name="$1"
  local path="$2"
  local expected="${3:-200}"
  local body="$ARTIFACT_DIR/$name.json"
  local code

  echo
  echo ">>> GET $BASE_URL$path"
  code="$(curl --silent --show-error --location --max-time 45 \
    --retry 2 --retry-delay 2 \
    -H 'Accept: application/json' \
    -o "$body" -w '%{http_code}' "$BASE_URL$path")"

  echo "HTTP $code"
  cat "$body"
  echo

  if [[ "$code" != "$expected" ]]; then
    echo "::error::${name}: expected HTTP $expected, received $code"
    return 1
  fi
}

# Local API liveness and route contract.
request root "/" 200
request health "/api/health" 200

# Exercise the complete public endpoint chain against the live ReAnime provider.
request bleach_search "/api/search?q=Bleach&limit=10&offset=0" 200

SLUG="$(node --input-type=module - "$ARTIFACT_DIR/bleach_search.json" <<'NODE'
import fs from "node:fs";
const root = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const seen = new Set();
function find(value, depth = 0) {
  if (!value || typeof value !== "object" || depth > 8 || seen.has(value)) return null;
  seen.add(value);
  if (Array.isArray(value)) {
    for (const item of value) { const found = find(item, depth + 1); if (found) return found; }
    return null;
  }
  for (const key of ["slug", "animeSlug", "anime_slug"]) {
    const candidate = value[key];
    if (typeof candidate === "string" && /^[a-zA-Z0-9-]{2,180}$/.test(candidate)) return candidate;
  }
  for (const key of ["data", "results", "anime", "animes", "items"]) {
    const found = find(value[key], depth + 1);
    if (found) return found;
  }
  return null;
}
const slug = find(root);
if (slug) process.stdout.write(slug);
NODE
)"

if [[ -z "$SLUG" ]]; then
  echo "::error::Search succeeded but no anime slug was found. See $ARTIFACT_DIR/bleach_search.json"
  exit 1
fi

echo "Using Bleach search result slug: $SLUG"
request bleach_anime "/api/anime/$SLUG" 200
request bleach_episodes "/api/episodes/$SLUG" 200
request bleach_servers "/api/servers/$SLUG/1" 200
request bleach_sub "/api/watch/$SLUG/1/sub" 200
request bleach_dub "/api/watch/$SLUG/1/dub" 200

echo
echo "All curl endpoint checks passed."
