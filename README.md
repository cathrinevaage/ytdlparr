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

What an indexer puts in the NZB. Only `url` and `name` are required;
everything else overrides the category's options.

```json
{
  "url": "https://example.com/watch/abc123",
  "name": "Show - S01E01 - Pilot",
  "format": "bestvideo[height<=1080]+bestaudio/best",
  "subs": ["en"],
  "embed": ["subs", "chapters", "thumbnail", "metadata"],
  "sidecar": [],
  "sponsorblock": [],
  "container": "mkv"
}
```

| key | meaning |
|---|---|
| `url` | anything yt-dlp's extractors accept |
| `name` | the folder and file name written; Sonarr's `nzbname` overrides it |
| `format` | yt-dlp format selector |
| `subs` | subtitle languages, or `["all"]` |
| `embed` | any of `subs`, `chapters`, `thumbnail`, `metadata` |
| `sidecar` | of `subs`, `thumbnail`: also keep the file next to the video |
| `sponsorblock` | SponsorBlock categories to cut |
| `container` | merge output format |

Carried as:

```xml
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <head>
    <meta type="name">Show - S01E01 - Pilot</meta>
    <meta type="ytdlpspec">{ ...json... }</meta>
  </head>
</nzb>
```

A real NZB, or anything without that meta tag, is refused at `addfile`.

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
    restart: unless-stopped
    ports:
      - "9120:9120"
    volumes:
      - ./ytdlparr:/config
      - /fast/incomplete:/downloads/incomplete
      - /library/complete:/downloads/complete
```

The `complete` mount must be visible to Sonarr at the same path, or
mapped with a remote path mapping - exactly as with SAB.

### Sonarr / Radarr

Settings → Download Clients → Add → SABnzbd:

| field | value |
|---|---|
| Host / Port | `ytdlparr` / `9120` |
| API Key | `server.api_key` |
| Category | one of your named categories, e.g. `tv-example` |
| Client Priority | lower than your real usenet client (higher number) |

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
