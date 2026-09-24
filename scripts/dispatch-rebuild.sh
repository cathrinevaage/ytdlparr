#!/bin/sh
# Cron this wherever you like. When PyPI has a yt-dlp release newer
# than the last one built, it checks that the release can still list
# formats for SMOKE_URL - a programme on a site you depend on, checked
# from a network that can reach it - and only then asks GitHub to
# rebuild the image with that release.
#
# Needs: GH_TOKEN with actions:write, GH_REPO as owner/repo, SMOKE_URL,
# python3. Optional: EXPECT_SUBS=en,de  EXPECT_MIN_HEIGHT=1080
#
#   0 6 * * *  GH_TOKEN=... GH_REPO=owner/ytdlparr SMOKE_URL=... /path/dispatch-rebuild.sh
set -eu

: "${GH_TOKEN:?set GH_TOKEN (actions:write)}"
: "${GH_REPO:?set GH_REPO as owner/repo}"
: "${SMOKE_URL:?set SMOKE_URL to a programme on a site you depend on}"
STATE="${STATE:-$HOME/.cache/ytdlparr-dispatch}"

latest=$(curl -fsSL https://pypi.org/pypi/yt-dlp/json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])')
last=$(cat "$STATE" 2>/dev/null || true)

if [ "$latest" = "$last" ]; then
  echo "yt-dlp $latest already built"
  exit 0
fi

echo "yt-dlp $latest is new (last built: ${last:-none}); checking $SMOKE_URL"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
python3 -m venv "$workdir/venv"
"$workdir/venv/bin/pip" install --quiet "yt-dlp==$latest"

if ! EXPECT_SUBS="${EXPECT_SUBS:-}" EXPECT_MIN_HEIGHT="${EXPECT_MIN_HEIGHT:-0}" \
     "$workdir/venv/bin/python" - "$SMOKE_URL" <<'PY'
import os, sys, yt_dlp
with yt_dlp.YoutubeDL({"quiet": True, "simulate": True}) as ydl:
    info = ydl.extract_info(sys.argv[1], download=False)
formats = info.get("formats") or []
subs = info.get("subtitles") or {}
assert formats, "no formats"
minimum = int(os.environ["EXPECT_MIN_HEIGHT"])
if minimum:
    assert any((f.get("height") or 0) >= minimum for f in formats), f"nothing at {minimum}p"
for language in filter(None, os.environ["EXPECT_SUBS"].split(",")):
    assert language.strip() in subs, f"no {language.strip()} subtitles"
print(f"ok: {info.get('title')} - {len(formats)} formats, subs {sorted(subs)}")
PY
then
  echo "yt-dlp $latest fails against $SMOKE_URL; not dispatching" >&2
  exit 1
fi

curl -fsS -X POST \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$GH_REPO/actions/workflows/build.yml/dispatches" \
  -d "{\"ref\":\"main\",\"inputs\":{\"ytdlp_version\":\"$latest\"}}"

mkdir -p "$(dirname "$STATE")"
echo "$latest" > "$STATE"
echo "dispatched rebuild for yt-dlp $latest"
