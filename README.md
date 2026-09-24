# ytdlparr

A download client for Sonarr and Radarr that fetches with
[yt-dlp](https://github.com/yt-dlp/yt-dlp). It speaks SABnzbd's API,
so the *arrs treat it as a usenet client; what it actually receives is
a job spec inside an NZB-shaped file, produced by an indexer such as
[nrkarr](https://github.com/cathrinevaage/nrkarr).

One client serves any number of indexers. It knows nothing about any
particular site.

## How a job runs

```
Sonarr    addfile  (the faux NZB, category tv-example, priority)
ytdlparr  spec = <meta type="ytdlpspec">  ->  queued
          options = categories["*"] < categories["tv-example"] < spec
          yt-dlp  -> <incomplete>/<job id>/   fetch, subs, mux    (local disk)
          wait for the move window
          move    -> <complete>/<category dir>/<name>/           (library)
Sonarr    history: Completed, storage=/downloads/complete/tv/<name>
```

yt-dlp is embedded, not shelled out: its progress and postprocessor
hooks drive what Sonarr sees in the queue. ffmpeg is external and is
run through a `nice`/`ionice` wrapper.

## The job spec

What an indexer puts in the NZB. Two shapes are accepted.

### Tracks mode

The indexer names every stream of the output. It says *what* - a URL
and a yt-dlp selector (or a direct subtitle URL) per track, plus the
title, language and flags each stream should carry - and knows the
site. ytdlparr does *how* - fetch each track, mux in order, tag - and
knows no site.

```json
{
  "url": "https://example.com/watch/abc123",
  "name": "Show - S01E01 - Pilot",
  "container": "mkv",

  "video": { "format": "bestvideo[height<=1080]" },

  "audio": [
    { "format": "bestaudio[format_id^=aud3]", "title": "Surround 5.1", "language": "eng",
      "flags": ["default"], "optional": true },
    { "format": "bestaudio[format_id^=aud2]", "title": "Stereo", "language": "eng" },
    { "url": "https://example.com/watch/abc123-described",
      "format": "bestaudio", "title": "Audio description", "language": "eng",
      "flags": ["visual_impaired"], "optional": true }
  ],

  "subtitles": [
    { "url": "https://cdn.example.com/.../s0_index.m3u8", "title": "English", "language": "eng", "flags": ["default"] },
    { "url": "https://cdn.example.com/.../s1_index.m3u8", "title": "English (forced)", "language": "eng", "flags": ["forced"] },
    { "select": "en-sdh", "title": "English SDH", "language": "eng", "flags": ["hearing_impaired"] }
  ],

  "embed": ["chapters", "thumbnail", "metadata"],
  "sidecar": []
}
```

| key | meaning |
|---|---|
| `url`, `name`, `container` | as before; `name` is the folder and file name written, `nzbname` from Sonarr overrides it |
| `video.format` | yt-dlp selector for the video stream (video-only or muxed; if muxed and there are no `audio` entries, its own audio is kept) |
| `audio[]` | one entry per audio stream, in output order |
| `subtitles[]` | one entry per subtitle stream, in output order |
| `url` on a track | another source; defaults to the spec's `url`. ytdlparr fetches only that selector from it |
| `format` | yt-dlp format selector for a video or audio track |
| `select` | yt-dlp subtitle key, fetched as a subtitles-only run |
| `url` on a subtitle | an HLS subtitle playlist or a VTT/SRT file, fetched with ffmpeg |
| `title`, `language` | written to the stream; `language` is an ISO 639-2 tag |
| `flags` | ffmpeg disposition names, verbatim: `default`, `forced`, `hearing_impaired`, `visual_impaired`, `commentary`, `original`, `dub` |
| `optional` | take it if the selector matches, skip silently if not; a required track that matches nothing fails the attempt |
| `embed` | any of `chapters`, `thumbnail`, `metadata`; subtitles are always embedded in tracks mode |
| `sidecar` | `subs` keeps each subtitle as `<name>.<lang>[.forced][.sdh].srt` too; `thumbnail` keeps the image |

Every track is its own yt-dlp (or ffmpeg) run, so an optional track
that matches nothing costs one failed selection and nothing else. The
video run carries the chapters/metadata postprocessors and the
thumbnail; then one ffmpeg pass stream-copies everything into the
container with titles, languages and dispositions, in the order the
lists gave.

### Legacy mode

A spec with `format` and `subs` and none of `video`/`audio`/`subtitles`
runs as a single yt-dlp job with embedding left to yt-dlp:

```json
{ "url": "…", "name": "…", "format": "bestvideo[height<=1080]+bestaudio/best",
  "subs": ["en"], "embed": ["subs", "chapters", "thumbnail", "metadata"], "container": "mkv" }
```

Category options fill in whatever the spec leaves out in legacy mode;
in tracks mode the spec is authoritative and the category contributes
`dir`, `container`, `embed` and `sidecar` only.

Carried as:

```xml
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head>
    <meta type="name">Show - S01E01 - Pilot</meta>
    <meta type="ytdlpspec">{ ...json... }</meta>
  </head>
  <file poster="…" date="…" subject="…"><segments><segment …/></segments></file>
</nzb>
```

Sonarr's NZB validation requires a `<file>` element; the indexer
includes a placeholder. Anything without the `ytdlpspec` meta tag is
refused at `addfile`.

## Configuration

Copy [`config.example.yml`](config.example.yml) to `/config/config.yml`.
Config is operational only - where things go, how many at once, when.
What to download comes from the spec.

### Categories

Sonarr sends a category with every job. Categories are the routing
key when one client serves several indexers, and they follow SAB's
model: a `*` block, named blocks overriding it, the spec overriding
those.

```yaml
categories:
  "*":
    dir: ""
    format: "bestvideo+bestaudio/best"
    container: mkv
    subs: [all]
    embed: [subs, chapters, thumbnail, metadata]
  tv-example:
    dir: tv
    format: "bestvideo[height<=1080]+bestaudio/best"
    subs: [en]
```

`dir` is relative to `paths.complete`. `get_config` advertises every
named category to Sonarr.

### Paths and the move window

```yaml
paths:
  incomplete: /downloads/incomplete   # fast local disk
  complete: /downloads/complete       # final library storage
schedule:
  timezone: UTC                       # defaults to $TZ when set
  download:
    - { days: [mon,tue,wed,thu,fri,sat,sun], from: "00:00", to: "24:00" }
  move:
    - { days: [mon,tue,wed,thu,fri], from: "09:00", to: "23:00" }
    - { days: [sat,sun], from: "10:00", to: "23:00" }
```

Fetching and muxing happen in `incomplete`; the finished folder is
then moved to `complete`. The `move` window governs that second step,
because writing to spinning disks is the noisy part. A job finished at
03:00 waits until the window opens; Sonarr sees it as queued, the same
as during a long SAB unpack.

Jobs Sonarr sends with priority Force ignore the windows and a paused
queue.

### Everything else

| key | what |
|---|---|
| `limits.max_concurrent` | download workers |
| `limits.rate_limit` | yt-dlp `-r`, e.g. `5M` |
| `limits.retries` / `retry_backoff` | job-level retries, backoff doubling |
| `limits.nice` / `ionice` | applied to ffmpeg |
| `limits.pause_downloads_during_postprocess` | don't start a fetch while muxing |
| `disk.min_free_*` / `on_low_space` | `fail` the job or `pause` the queue |
| `history.keep_jobs` | history retention |
| `cleanup` | globs swept from the finished folder |
| `permissions.chmod` / `chown` | empty means don't force |
| `cookies` | `host: /path/to/cookies.txt`, per site |
| `notifications.apprise_urls` / `on` | Apprise targets and which events |

Cookies live on the client keyed by host, never in the spec: one place
to refresh, and the indexer never touches a secret.

### Environment overrides

Any scalar or list setting can be set from the environment as
`YTDLPARR_<SECTION>_<KEY>`, which wins over the file. The value is
typed from the setting it replaces; lists split on commas.

```
YTDLPARR_SERVER_API_KEY=...
YTDLPARR_PATHS_COMPLETE=/data/downloads/ytdlparr
YTDLPARR_LIMITS_MAX_CONCURRENT=4
YTDLPARR_SCHEDULE_TIMEZONE=Europe/Berlin     # otherwise $TZ, otherwise UTC
```

Tables and lists of tables - `categories`, `cookies`, the `schedule`
windows - are file-only. Unknown names are ignored rather than
creating a setting nothing reads.

## Setup

### Compose

```yaml
services:
  ytdlparr:
    image: ghcr.io/cathrinevaage/ytdlparr:latest
    container_name: ytdlparr
    user: ${PUID}:${PGID}
    environment:
      - TZ=${TZ}
      - YTDLPARR_SERVER_API_KEY=${YTDLPARR_API_KEY}
      - YTDLPARR_PATHS_COMPLETE=/data/downloads/ytdlparr
      - YTDLPARR_PATHS_STATE=/config/jobs.json
    ports:
      - "9120:9120"
    volumes:
      - ./ytdlparr:/config
      - /path/to/library:/data
    restart: unless-stopped
```

- `user:` is how the container runs as your media user; the image has
  no PUID/PGID handling. `/config` must be writable by that uid.
- `paths.complete` must be visible to Sonarr at the same path, or
  mapped with a remote path mapping - exactly as with SAB. Mounting
  the same library volume Sonarr mounts is the simplest way.
- `paths.incomplete` defaults to `/downloads/incomplete` inside the
  container and is left in the container's own layer, which is on the
  host's disk. Anything mid-download or waiting for its move window is
  lost when the container is recreated - and watchtower recreates it
  on every image update; on restart the worker requeues those jobs.
  Mount a host directory there if you'd rather keep them.
- The config file is `/config/config.yml`. It is needed for anything
  that is a table - categories, schedule windows, cookies - because
  those cannot come from the environment. A minimal one:

  ```yaml
  categories:
    tv-example:
      dir: tv
      format: "bestvideo[height<=1080]+bestaudio/best"
      subs: [en]
  ```

### Sonarr / Radarr

Download clients are configured in Sonarr and Radarr directly.
Prowlarr does not sync them - its own download-client list serves
grabs made from Prowlarr's UI, nothing else.

Settings → Download Clients → Add → SABnzbd:

| field | value |
|---|---|
| Host / Port | `ytdlparr` / `9120` |
| API Key | `YTDLPARR_API_KEY` |
| Category | one of your named categories, e.g. `tv-example` |
| Client Priority | lower than your real usenet client (higher number) |

The category test asks `get_config` for the list and matches by name.
ytdlparr advertises exactly what `config.yml` declares - it does not
create categories on the fly the way SAB does - so "Category does not
exist" means the file is missing, unreadable, or does not name it.
What is advertised right now:

```sh
docker compose exec ytdlparr sh -c 'wget -qO- "http://127.0.0.1:9120/api?mode=get_config&apikey=$YTDLPARR_SERVER_API_KEY&output=json"' | jq -c '[.config.categories[].name]'
```

(`127.0.0.1`, not `localhost`: on an IPv6-enabled compose network
`localhost` resolves to `::1` first and the app listens on IPv4.)

Releases reach ytdlparr because of a **custom format** that scores the
indexer's release group in every quality profile - that lives with
the indexer, see [nrkarr's README](https://github.com/cathrinevaage/nrkarr#custom-format-and-score).

### Checking it

The queue and history exactly as Sonarr reads them:

```sh
docker compose exec ytdlparr sh -c 'wget -qO- "http://127.0.0.1:9120/api?mode=queue&apikey=$YTDLPARR_SERVER_API_KEY&output=json"' | jq -c '.queue.slots[] | {status, percentage, mb, timeleft, filename}'
docker compose exec ytdlparr sh -c 'wget -qO- "http://127.0.0.1:9120/api?mode=history&apikey=$YTDLPARR_SERVER_API_KEY&output=json"' | jq -c '.history.slots[] | {status, storage, fail_message, name}'
```

A healthy job in the log:

```
INFO ytdlparr.app: queued Show - S01E01 - Pilot [tv-example]
INFO ytdlparr.worker: downloading Show - S01E01 - Pilot
INFO ytdlparr.worker: moving Show - S01E01 - Pilot
```

Between `downloading` and `moving` the job shows as *Extracting* in
Sonarr while ffmpeg muxes; then it waits for the move window if one
is configured, and then it appears in history with its `storage`
path. A failed attempt logs the reason from yt-dlp and the retry
delay; after `limits.retries` the job is *Failed* with that reason as
its `fail_message`.

### Routing: only job specs must reach ytdlparr

To Sonarr and Radarr, ytdlparr is just another usenet client. That is
the point of the SABnzbd façade - and it is also the trap. **Nothing
in Sonarr or Radarr knows that ytdlparr can only handle job specs and
your real client can only handle real NZBs.** You tell them, per
indexer, or they will mix the two.

When an indexer's **Download Client** is left on "Any", grabs from it
are spread across *every* enabled usenet client - clients of equal
priority are used round-robin. With ytdlparr and SABnzbd both enabled
that means:

- a real NZB from your usenet indexer arrives at ytdlparr, which
  refuses it at `addfile` (`not a job spec`). Sonarr records a failed
  grab for a perfectly good release, and after enough of those it
  blocklists it;
- a job spec from nrkarr arrives at SABnzbd, which accepts a file with
  no articles and fails it.

Two rules, one per direction:

| | how |
|---|---|
| spec-producing indexers (nrkarr, …) → ytdlparr | **pin** each one's Download Client to ytdlparr. Nothing else can do this: priority cannot know that only one client understands the spec. |
| real usenet indexers → your real client | **priority**: give the real client a higher Client Priority (lower number) than ytdlparr. Sonarr picks from the top-priority group and round-robins only within it, so unpinned indexers always land on the real client. Pinning each one works too, but is not required. |

The one edge in the priority route: after repeated failures Sonarr
temporarily blocks a client, and grabs then fall to the next priority
- ytdlparr - which refuses them until the block lifts.

ytdlparr's side of this is to fail loudly: anything without a
`ytdlpspec` meta tag is rejected at `addfile`, never queued, so a
misrouted NZB shows up as a refused grab in Sonarr's log rather than a
silent stall.

## What Sonarr sees

| SAB field | source |
|---|---|
| `status`, `nzo_id`, `storage`, `fail_message` | real |
| `percentage`, `mb`, `mbleft`, `timeleft`, `kbpersec` | from yt-dlp's estimate; a guess until the download completes |
| `version 4.2.0`, sorting flags, `pre_check` | fixed; Sonarr checks they exist |

Queue states map to `Queued`, `Downloading`, `Extracting` (muxing) and
`Moving`; history to `Completed` or `Failed` with the reason.

## Keeping yt-dlp current

yt-dlp's extractors break and get fixed upstream constantly, and the
version is baked into the image. The workflow in
[`.github/workflows/build.yml`](.github/workflows/build.yml) resolves
the newest release on PyPI, smoke-tests that it installs and extracts,
runs the tests, and pushes `:latest` plus a `ytdlp-<version>` tag.

It is `workflow_dispatch` only, because GitHub disables `schedule:`
workflows after 60 days of repository inactivity. Trigger it from a
cron of your own with
[`scripts/dispatch-rebuild.sh`](scripts/dispatch-rebuild.sh):

```
0 6 * * *  GH_TOKEN=<actions:write> GH_REPO=<owner>/ytdlparr SMOKE_URL=<url> /path/dispatch-rebuild.sh
```

It dispatches only when PyPI has a version it has not built before,
and only after the new release can still list formats for
`SMOKE_URL` - a programme on whichever site you depend on, checked
from the network you run the client on, since GitHub's runners may
not be able to reach it. `EXPECT_SUBS` (comma-separated languages)
and `EXPECT_MIN_HEIGHT` tighten that check when set.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install flask pyyaml yt-dlp apprise
.venv/bin/python -m unittest discover -s tests -t .
YTDLPARR_CONFIG=config.yml .venv/bin/python -m ytdlparr
```

The tests swap the fetcher out and never touch the network.
