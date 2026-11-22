# Sonify — Spotify-Like Ad-Free Music Streaming Web App Architecture Specification

> **Document Owner:** `@researcher`  
> **Target Workspace:** `/mnt/e/lam lai tu dau/music`  
> **Status:** Implementation-Ready Specification for `@builder` & `@tester`

---

## 1. Local Environment Audit & Architectural Constraints

Direct inspection of the target Linux (WSL2 Ubuntu 26.04, `x86_64`) runtime yielded the following verified environment inventory:

| Tool / Runtime | Status | Verified Version / Path |
|---|---|---|
| `python3` | **Installed** | `Python 3.14.4` (`/usr/bin/python3`) |
| `sqlite3` (Python stdlib) | **Installed** | `SQLite 3.46.1` (`import sqlite3`) |
| `requests` / `urllib3` / `certifi` | **Installed** | `requests 2.32.5`, `urllib3 2.6.3`, `certifi 2026.1.4` |
| `cryptography` | **Installed** | `46.0.5` (supports DES-ECB via `TripleDES(K * 3)`) |
| `git` / `curl` / `openssl` | **Installed** | `git 2.53.0`, `curl 8.18.0`, `/usr/bin/openssl` |
| `node` / `npm` / `npx` / `pnpm` / `yarn` / `bun` | **NOT Installed** | Absent from Linux `PATH` |
| `pip` / `pip3` / `uv` | **NOT Installed** | `/usr/bin/python3: No module named pip` |
| `yt-dlp` / `ffmpeg` | **NOT Installed** | Absent from Linux `PATH` |

### Key Engineering Decision: Zero-Install, Zero-Build Runtime
Because `node`, `npm`, `pip`, `yt-dlp`, and `ffmpeg` are not present in the Linux environment, the application **MUST** run out-of-the-box with a single command (`python3 app.py`) using **zero `npm install` and zero `pip install` steps**:

1. **Backend:** Pure **Python 3.14** (`http.server.ThreadingHTTPServer`, `sqlite3`, `urllib.request`, `http.client`, `concurrent.futures`, `ssl`, `wave`, `struct`, `math`, `unittest`) with optional acceleration from already-installed `cryptography` (plus a built-in pure-Python DES-ECB fallback so the app also runs unmodified on any Windows/macOS Python 3.10+ machine) and optional runtime auto-detection of `yt-dlp` if a user installs it later.
2. **Frontend:** Modular **Vanilla ES2022+ Modules (`<script type="module">`) + HTML5 + CSS3** served directly by the Python server — zero bundler, zero CDN dependency required for core icons/UI, instant startup.
3. **Database:** Embedded **SQLite 3** (`data/music.db`, WAL mode) auto-initialized on startup.
4. **Testing:** Python stdlib **`unittest`** (`python3 -m unittest discover -s tests`) — zero external test dependencies.

---

## 2. System Architecture Overview

```text
┌────────────────────────────────────────────────────────────────────────────┐
│                     Browser (Vanilla ES Modules SPA)                       │
│  Spotify-like Dark UI │ HTML5 <audio> + MediaSession │ Synced LRC Engine   │
│  Sidebar / Home / Search / Library / Queue / Lyrics Panel / Bottom Player  │
└──────────────────────────┬──────────────────────────┬──────────────────────┘
                           │ REST JSON (/api/*)       │ Audio Stream (HTTP Range)
                           ▼                          ▼
┌────────────────────────────────────────────────────────────────────────────┐
│              Python 3.14 Backend (ThreadingHTTPServer :8080)               │
│  ┌──────────────────────┐ ┌───────────────────────┐ ┌────────────────────┐ │
│  │   Catalog Service    │ │  Stream Proxy Service │ │   Lyrics Service   │ │
│  │  • Deezer Public API │ │  Tier 0: Audius/Saavn │ │  1. LRCLIB /get    │ │
│  │  • Apple RSS (VN/US) │ │  Tier 1: JioSaavn 320 │ │  2. LRCLIB /search │ │
│  │  • iTunes Search     │ │  Tier 2: YT InnerTube │ │  3. YTM InnerTube  │ │
│  │  • JioSaavn API      │ │     /Piped/Invidious  │ │  4. Lyrics.ovh     │ │
│  │  • Audius Discovery  │ │     (+opt yt-dlp)     │ │  5. Local LRC      │ │
│  │  • YTM InnerTube     │ │  Tier 3: Audius Match │ │  + LRC Parser      │ │
│  │  • Offline Fallback  │ │  Tier 4: 30s Preview  │ │  + 7d SQLite Cache │ │
│  │  + 10m TTL Cache     │ │  Tier 5: Offline WAV  │ └────────────────────┘ │
│  └──────────────────────┘ │  + 206 Range Proxying │ ┌────────────────────┐ │
│                           └───────────────────────┘ │  Library Service   │ │
│                                                     │  SQLite (music.db) │ │
│                                                     │  Playlists, Likes, │ │
│                                                     │  History, Settings │ │
│                                                     └────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

### Core Guarantees
- **100% Ad-Free:** Zero audio ads, zero banner/display ads, zero sponsored cards, zero third-party ad/tracker scripts. Every stream response explicitly includes `X-Ad-Free: true`.
- **Up-to-Date Full Catalog (Global + Vietnam):** Real-time charts, new releases, artists, albums, and multi-source live search covering Global Pop, US/UK, K-Pop, V-Pop (Vietnam Apple Charts & iTunes `country=vn`), Indie, Electronic, and Hip-Hop — never a static toy list when online.
- **Resilient Full-Length Playback:** Proxied through `/api/stream/{track_id}` to avoid CORS, expired CDN tokens, and IP/User-Agent mismatches, with full HTTP `Range` (`206 Partial Content`) support for instant seeking and lyric-line click-to-seek.
- **Graceful Offline / Rate-Limit Resilience:** If upstream internet is unavailable (e.g., offline use or sandboxed testing), the backend transparently falls back to cached SQLite data + a curated built-in fallback catalog with real synced `.lrc` lyrics and deterministic seekable stereo WAV audio streams, clearly flagged via `mode: "offline"` / `X-Stream-Mode: offline-synth`.

---

## 3. Music Catalog, Streaming Proxy & Lyrics APIs

### 3.1 Canonical Track ID Scheme & Unified Track Model
Every track uses a deterministic namespaced ID so the backend knows its origin while keeping playlists, likes, history, and stream resolution uniform:

| Prefix | Provider | Example ID | Notes |
|---|---|---|---|
| `dz:` | Deezer Public API | `dz:908604612` | Primary global charts, albums, artists, clean metadata |
| `it:` | Apple Music / iTunes | `it:1440857781` | Vietnam & US live charts, strong V-Pop coverage |
| `js:` | JioSaavn | `js:5WXAlMNt` | Direct full-length 320kbps AAC/MP4 streams |
| `au:` | Audius Decentralized | `au:AMBkk` | Official keyless full-length MP3 streams |
| `yt:` | YouTube Music InnerTube | `yt:4NRXx6U8ABQ` | Broadest global/V-Pop/K-Pop/live/remix coverage |
| `local:` | Built-in Offline Fallback | `local:neon-horizon` | Activated only when all network upstreams fail |

**Unified Track JSON Object:**
```json
{
  "id": "dz:908604612",
  "source": "deezer",
  "title": "Blinding Lights",
  "artist": "The Weeknd",
  "artistId": "dz:4050205",
  "album": "After Hours",
  "albumId": "dz:137217782",
  "duration": 200,
  "coverSmall": "https://cdn-images.dzcdn.net/images/cover/.../250x250.jpg",
  "coverLarge": "https://cdn-images.dzcdn.net/images/cover/.../500x500.jpg",
  "explicit": false,
  "releaseDate": "2020-03-20",
  "previewUrl": "https://cdnt-preview.dzcdn.net/api/1/1/...mp3",
  "streamUrl": "/api/stream/dz:908604612",
  "liked": false
}
```
> **Crucial Persistence Rule:** Whenever any catalog endpoint returns tracks (Home, Search, Artist, Album), the backend **upserts** their normalized metadata into SQLite `tracks_cache` (`extra_json` stores provider-specific fields such as `encrypted_media_url`, `videoId`, or `audius_id`). This guarantees `/api/stream/{track_id}`, Saved Playlists, Liked Songs, and Play History always retain full track metadata across server restarts.

---

### 3.2 Live Catalog Providers (No Paid API Keys Required)

1. **Deezer Public API (`https://api.deezer.com`)** — No auth required; proxied server-side to avoid CORS:
   - Global Chart: `GET /chart?limit=20` (returns `tracks.data`, `albums.data`, `artists.data`)
   - New Releases: `GET /editorial/0/releases?limit=16`
   - Search: `GET /search/track?q={q}&limit=20`, `GET /search/artist?q={q}&limit=10`, `GET /search/album?q={q}&limit=10`
   - Artist: `GET /artist/{id}`, `GET /artist/{id}/top?limit=25`, `GET /artist/{id}/albums?limit=20`
   - Album: `GET /album/{id}`
   - Track Refresh (for fresh signed preview URL): `GET /track/{id}`
   - Artwork mapping: `album.cover_medium` (`coverSmall`), `album.cover_big` or `cover_xl` (`coverLarge`).

2. **Apple Music RSS Charts + iTunes Search/Lookup** — No auth required:
   - Vietnam Top Songs: `GET https://rss.marketingtools.apple.com/api/v2/vn/music/most-played/20/songs.json`
   - US/Global Top Songs: `GET https://rss.marketingtools.apple.com/api/v2/us/music/most-played/20/songs.json`
   - Top Albums / New Releases: `GET https://rss.marketingtools.apple.com/api/v2/us/music/most-played/16/albums.json`
   - Batch Lookup Enrichment: `GET https://itunes.apple.com/lookup?id={comma_ids}&country={vn|us}` (adds `trackTimeMillis`, `collectionName`, `previewUrl`, `releaseDate`).
   - Live Search (Vietnam + Global): `GET https://itunes.apple.com/search?term={q}&media=music&entity=song&country=vn&limit=12` (essential for V-Pop / Vietnamese diacritic queries).
   - High-res Artwork Trick: replace `100x100bb` with `600x600bb` in `artworkUrl100` for `coverLarge` (and `300x300bb` for `coverSmall`).

3. **JioSaavn Direct Web API + Public Mirror** — No auth required; full-length 320kbps AAC/MP4:
   - Primary Direct Endpoint:
     `GET https://www.jiosaavn.com/api.php?__call=search.getResults&_format=json&_marker=0&api_version=4&ctx=web6dot0&q={q}&p=1&n=10`
   - Song Details:
     `GET https://www.jiosaavn.com/api.php?__call=song.getDetails&_format=json&_marker=0&api_version=4&ctx=web6dot0&pids={id}`
   - **DES-ECB URL Decryption (`backend/crypto_des.py`):**
     - `encrypted_media_url` is located in `song["more_info"]["encrypted_media_url"]` (v4) or `song["encrypted_media_url"]`.
     - Base64-decode the string -> decrypt with **DES-ECB**, key `b"38346591"`, PKCS#7 unpad -> UTF-8 URL.
     - Implementation: use `cryptography.hazmat.decrepit.ciphers.algorithms.TripleDES(b"38346591" * 3)` with `modes.ECB()` (since $D_K(E_K(D_K(C))) = D_K(C)$, TripleDES with $K_1=K_2=K_3$ is mathematically identical to single DES — verified working on local Python 3.14!), with a compact pure-Python DES-ECB fallback if `cryptography` is absent.
     - Quality upgrade: if `more_info.get("320kbps") == "true"`, replace `_96.mp4` with `_320.mp4` (fallback chain: `_320.mp4` -> `_160.mp4` -> `_96.mp4`).
   - Secondary JSON Mirror Fallback (if `api.php` changes or blocks): `GET https://saavn.sumit.co/api/search/songs?query={q}&limit=8` -> `data.results[].downloadUrl` (pick highest `quality == "320kbps"`).

4. **Audius Decentralized REST API** — Official, keyless, full-length MP3 streams:
   - Dynamic Discovery Node Selection: `GET https://api.audius.co` -> pick first healthy host from `data[]` (fallback static host: `https://discoveryprovider.audius.co`), cached for 30 minutes.
   - Trending Tracks: `GET {host}/v1/tracks/trending?time=week&limit=15&app_name=Sonify`
   - Search Tracks: `GET {host}/v1/tracks/search?query={q}&limit=10&app_name=Sonify`
   - Direct Full-Length Audio Stream: `GET {host}/v1/tracks/{id}/stream?app_name=Sonify` (follow HTTP 302 redirect server-side).

5. **YouTube Music InnerTube Search (Supplementary Catalog & Stream Matching)** — No API key required:
   - Endpoint: `POST https://music.youtube.com/youtubei/v1/search`
   - Headers: `Origin: https://music.youtube.com`, `Referer: https://music.youtube.com/`, `Content-Type: application/json`, standard Chrome `User-Agent`.
   - JSON Body:
     ```json
     {
       "context": {
         "client": {
           "clientName": "WEB_REMIX",
           "clientVersion": "1.20260301.01.00",
           "hl": "en",
           "gl": "US"
         }
       },
       "query": "{artist} {title}",
       "params": "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D"
     }
     ```
   - Extracts `videoId`, title, artist runs, duration text (`"3:20"` -> `200`), and thumbnails from `musicResponsiveListItemRenderer` nodes (recursively traversing `contents` so minor layout changes don't break parsing).

---

### 3.3 Full-Length Ad-Free Audio Resolution Waterfall (`backend/services/stream_service.py`)

When the browser requests `GET /api/stream/{track_id}` (or `HEAD /api/stream/{track_id}`):

1. **Check Stream URL Cache:** In-memory TTL dictionary keyed by `track_id` (TTL **30 minutes**). Revalidate on upstream `403`/`410`.
2. **Load Track Metadata:** Lookup `track_id` in SQLite `tracks_cache` (or fetch via provider API if unknown).
3. **Execute 5-Tier Resolution Waterfall** (total budget per tier: 4–6s timeout so playback starts fast):
   - **Tier 0 — Native Full-Length Stream:**
     - If `source == "audius"` -> `{audius_host}/v1/tracks/{raw_id}/stream?app_name=Sonify` (`stream_mode="full"`, `source="audius"`).
     - If `source == "jiosaavn"` -> decrypt `encrypted_media_url` -> test `_320.mp4` / `_160.mp4` / `_96.mp4` (`stream_mode="full"`, `source="jiosaavn"`).
     - If `source == "local"` -> jump directly to Tier 5 (`stream_mode="offline-synth"`).
   - **Tier 1 — JioSaavn Cross-Match (320kbps Full Track, Zero Bot-Check):**
     - Query JioSaavn with cleaned `{artist} {title}`.
     - Score candidates:
       `score = 0.55 * title_similarity + 0.30 * artist_token_overlap + 0.15 * duration_proximity`
       where `duration_proximity = max(0, 1 - abs(cand_dur - target_dur) / 25)` (if target duration > 0).
     - Accept best candidate with `score >= 0.72`. Decrypt URL, upgrade to `_320.mp4` (fallback `_160.mp4` / `_96.mp4`), verify HTTP 200/206 with `HEAD` or Range `bytes=0-0`.
   - **Tier 2 — YouTube / YouTube Music Full-Length Audio Stream (V-Pop, K-Pop, Universal Coverage):**
     - Find `videoId` (directly if `yt:*` or via YTM InnerTube search, scoring by title + artist + duration proximity; penalize `"1 hour"`, `"slowed"`, `"sped up"`, `"cover"`, `"karaoke"` unless those words appear in the target title).
     - Resolve direct audio-only URL (`audio/mp4` itag 140/141 or `audio/webm` itag 251) via sub-chain:
       1. **Optional `yt-dlp`** (if `shutil.which("yt-dlp")` exists): `yt-dlp -f "bestaudio[ext=m4a]/bestaudio" -g --no-playlist --no-warnings https://music.youtube.com/watch?v={videoId}`.
       2. **Direct InnerTube `/youtubei/v1/player` rotation**: try `ANDROID_VR` (`clientVersion: "1.60.19"`, `UA: "com.google.android.apps.youtube.vr.oculus/1.60.19 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip"`) -> `IOS` (`clientVersion: "20.10.4"`, `UA: "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)"`) -> `TVHTML5_SIMPLY_EMBEDDED_PLAYER`. Filter `streamingData.adaptiveFormats` where `mimeType.startswith("audio/")` and `"url"` is directly present (skip cipher-only formats). Store matching `User-Agent` with cached stream entry!
       3. **Public Piped & Invidious Instance Pool** (with 10-minute unhealthy-instance circuit breaker):
          - Piped `GET {instance}/streams/{videoId}` -> sort `audioStreams` by `bitrate` desc, prefer `audio/mp4` then `audio/webm`.
          - Invidious `GET {instance}/api/v1/videos/{videoId}` -> filter `adaptiveFormats` with `type` starting with `audio/`, sort by `bitrate` desc.
          - Dynamically fetch active Invidious instances from `https://api.invidious.io/instances.json?sort_by=health` (cached 60m) with curated default seed list.
   - **Tier 3 — Audius Cross-Match (Full-Length MP3):**
     - Query Audius search; require strict similarity `>= 0.78` -> stream `/v1/tracks/{audius_id}/stream`.
   - **Tier 4 — Official 30-Second Preview Fallback (Deezer / iTunes):**
     - Use stored `previewUrl` (if Deezer preview returns 403 due to signature expiry, refresh via `GET https://api.deezer.com/track/{raw_id}` once).
     - Set `X-Stream-Mode: preview` so the UI informs the user transparently.
   - **Tier 5 — Offline Synthesized Stereo WAV Stream (`backend/offline_catalog.py`):**
     - Activated for `local:*` tracks OR when all upstream network connections fail (`URLError` / DNS error).
     - Generates a pleasant, deterministic 45-second stereo 16-bit PCM 44.1kHz WAV track (musical chord progression + gentle arpeggio seeded by `hash(track_id)` so every track sounds distinct and musical, not harsh beeps).
     - Supports exact byte-range slicing (`206 Partial Content`) with `X-Stream-Mode: offline-synth`.

#### HTTP Proxy & Range Request Rules (`GET / HEAD /api/stream/{track_id}`)
- Forward client `Range` header (e.g., `Range: bytes=524288-`) directly to upstream CDN.
- Preserve upstream status code: `200 OK` or `206 Partial Content` (or return `416 Range Not Satisfiable` on invalid range).
- Always send headers:
  - `Content-Type` (e.g. `audio/mp4`, `audio/mpeg`, `audio/webm`, `audio/wav`)
  - `Accept-Ranges: bytes`
  - `Content-Length`
  - `Content-Range` (for `206`)
  - `Cache-Control: private, max-age=1800`
  - `X-Stream-Mode: full | preview | offline-synth`
  - `X-Stream-Source: <provider_name>`
  - `X-Ad-Free: true`
  - `Access-Control-Expose-Headers: X-Stream-Mode, X-Stream-Source, X-Ad-Free, Content-Range`
- Stream in `64 KiB` chunks; catch `BrokenPipeError` / `ConnectionResetError` cleanly on client seek/abort and close upstream response in `finally:`.

---

### 3.4 Synced & Plain Lyrics Architecture (`backend/providers/lyrics.py` & `backend/lrc_parser.py`)

#### Title & Artist Normalization
Before querying external APIs, clean metadata to maximize hit rate:
- Strip parenthetical/bracket suffixes matching: `official\s*(music\s*)?(video|audio|mv|lyric\s*video|visualizer)`, `remaster(ed)?(\s*\d{4})?`, `feat\.?.*`, `ft\.?.*`, `radio\s*edit`, `live\s*at.*`, `audio`, `hd`, `4k`.
- Primary artist helper: split on `,`, `&`, `feat.`, `ft.`, ` x ` for fallback queries.

#### 5-Stage Lyrics Waterfall (with 7-Day SQLite Cache)
1. **Cache Lookup:** `lyrics_cache` keyed by normalized `f"{artist_norm}::{title_norm}"` (bypass if `?refresh=1`).
2. **Stage 1 — LRCLIB Exact Match:**
   `GET https://lrclib.net/api/get?track_name={clean_title}&artist_name={artist}&album_name={album}&duration={duration}`
   - Required header: `User-Agent: Sonify/1.0 (https://github.com/sonify-local/sonify)`
   - Inspect fields: `instrumental` (bool), `syncedLyrics` (LRC string | null), `plainLyrics` (string | null).
3. **Stage 2 — LRCLIB Search Fallback:**
   `GET https://lrclib.net/api/search?track_name={clean_title}&artist_name={primary_artist}` (and if empty, `?q={clean_title} {primary_artist}`).
   - Rank array results: prioritize `syncedLyrics` non-empty -> artist similarity -> `abs(cand.duration - target_duration) <= 12s`. If none has `syncedLyrics`, select best `plainLyrics` or `instrumental: true` match.
4. **Stage 3 — YouTube Music InnerTube Lyrics (Optional):**
   If `videoId` is known in `extra_json`, call `POST https://music.youtube.com/youtubei/v1/next` (`WEB_REMIX`) to get lyrics tab `browseId` (`MPLYt...`), then `POST /youtubei/v1/browse` for plain lyrics.
5. **Stage 4 — Lyrics.ovh Plain Lyrics Fallback:**
   `GET https://api.lyrics.ovh/v1/{primary_artist}/{clean_title}` -> `{"lyrics": "..."}`.
6. **Stage 5 — Offline / Built-in Synced Lyrics:**
   For `local:*` demo tracks (or offline mode), return curated synced `.lrc` lyrics embedded in `offline_catalog.py`.

#### `.lrc` Timestamp Parser Specification (`backend/lrc_parser.py`)
Must handle all real-world `.lrc` edge cases:
- Regex for timestamps: `\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]`
- Fractional conversion: 2 digits = centiseconds (`/ 100.0`), 3 digits = milliseconds (`/ 1000.0`), 1 digit = tenths (`/ 10.0`), omitted = `0.0`.
- Support **multiple timestamps per line**: e.g., `[00:15.20][01:12.40]Chorus line` produces two entries at `15.2` and `72.4`.
- Honor `[offset:+/-ms]` header tag (clamping timestamps at `>= 0.0`).
- Ignore non-lyric metadata tags (`[ar:...]`, `[ti:...]`, `[al:...]`, `[au:...]`, `[by:...]`, `[length:...]`, `[re:...]`, `[ve:...]`).
- Strip inline word-level `<mm:ss.xx>` tags cleanly while preserving readable text.
- Sort resulting lines ascending by `time` (seconds float rounded to 3 decimals).
- Preserve empty timed interlude markers optionally or convert blank lines following a lyrical line into `{ "time": t, "text": "", "interlude": true }` (omitting leading/consecutive blanks).

---

## 4. Backend REST API Contracts

All JSON endpoints return `Content-Type: application/json; charset=utf-8`. Errors use standard HTTP status codes (`400`, `404`, `405`, `500`, `502`) with payload `{"error": {"code": "NOT_FOUND", "message": "..."}}`.

### 4.1 Health & Discovery
- **`GET /api/health`**
  ```json
  {
    "status": "ok",
    "version": "1.0.0",
    "adFree": true,
    "dbReady": true,
    "ytDlpAvailable": false,
    "timestamp": 1795345800
  }
  ```

- **`GET /api/home?region=global|vn&refresh=0|1`**
  ```json
  {
    "mode": "online",
    "region": "global",
    "generatedAt": 1795345800,
    "sections": [
      {
        "id": "trending-global",
        "title": "Trending Worldwide",
        "subtitle": "Updated live charts",
        "type": "tracks",
        "items": [ { "...Track..." } ]
      },
      {
        "id": "trending-vn",
        "title": "Top Vietnam / V-Pop",
        "subtitle": "Most played in Vietnam",
        "type": "tracks",
        "items": [ { "...Track..." } ]
      },
      {
        "id": "new-releases",
        "title": "New Releases & Hot Albums",
        "type": "albums",
        "items": [
          {
            "id": "dz:137217782",
            "source": "deezer",
            "title": "After Hours",
            "artist": "The Weeknd",
            "artistId": "dz:4050205",
            "coverSmall": "...",
            "coverLarge": "...",
            "releaseDate": "2020-03-20",
            "trackCount": 14
          }
        ]
      },
      {
        "id": "audius-trending",
        "title": "Full-Track Indie & Electronic",
        "subtitle": "Direct lossless-feel streams via Audius",
        "type": "tracks",
        "items": [ { "...Track..." } ]
      },
      {
        "id": "top-artists",
        "title": "Popular Artists",
        "type": "artists",
        "items": [
          {
            "id": "dz:4050205",
            "source": "deezer",
            "name": "The Weeknd",
            "pictureSmall": "...",
            "pictureLarge": "...",
            "fans": 12000000
          }
        ]
      }
    ]
  }
  ```

- **`GET /api/search?q={query}&type=all|tracks|artists|albums&limit=20`**
  ```json
  {
    "query": "blinding lights",
    "mode": "online",
    "topResult": { "kind": "track", "item": { "...Track..." } },
    "tracks": [ { "...Track..." } ],
    "artists": [ { "...Artist..." } ],
    "albums": [ { "...Album..." } ]
  }
  ```

- **`GET /api/artist/{artist_id}`**
  ```json
  {
    "artist": { "id": "dz:4050205", "name": "The Weeknd", "pictureLarge": "...", "fans": 12000000 },
    "topTracks": [ { "...Track..." } ],
    "albums": [ { "...Album..." } ]
  }
  ```

- **`GET /api/album/{album_id}`**
  ```json
  {
    "album": {
      "id": "dz:137217782",
      "title": "After Hours",
      "artist": "The Weeknd",
      "artistId": "dz:4050205",
      "coverLarge": "...",
      "releaseDate": "2020-03-20",
      "totalDuration": 3377
    },
    "tracks": [ { "...Track..." } ]
  }
  ```

### 4.2 Streaming Proxy
- **`GET /api/stream/{track_id}`** & **`HEAD /api/stream/{track_id}`**
  - Streams raw audio bytes with `200 OK` or `206 Partial Content`, `Accept-Ranges: bytes`, `X-Stream-Mode`, `X-Stream-Source`, `X-Ad-Free: true`.
- **`GET /api/stream/{track_id}/info`**
  - Resolves & returns stream metadata without downloading full body (useful for UI stream-quality badge & automated tests):
  ```json
  {
    "trackId": "dz:908604612",
    "streamUrl": "/api/stream/dz:908604612",
    "streamMode": "full",
    "streamSource": "jiosaavn",
    "contentType": "audio/mp4",
    "adFree": true
  }
  ```

### 4.3 Lyrics
- **`GET /api/lyrics?trackId={id}&title={title}&artist={artist}&album={album}&duration={sec}&refresh=0|1`**
  ```json
  {
    "trackId": "dz:908604612",
    "title": "Blinding Lights",
    "artist": "The Weeknd",
    "status": "synced",
    "source": "lrclib",
    "instrumental": false,
    "synced": true,
    "lines": [
      { "time": 27.12, "text": "I've been tryna call" },
      { "time": 29.85, "text": "I've been on my own for long enough" }
    ],
    "plainLyrics": "I've been tryna call\nI've been on my own for long enough...",
    "rawLrc": "[00:27.12] I've been tryna call\n[00:29.85] I've been on my own for long enough"
  }
  ```
  Possible `status` values: `"synced"` | `"plain"` | `"instrumental"` | `"not_found"`.

### 4.4 User Library, Playlists, Likes, History & Settings
- **Liked Songs:**
  - `GET /api/library/likes` -> `{"tracks": [...], "likedIds": [...]}`
  - `POST /api/library/likes` body: `{"track": {...Track...}}` (or `{"trackId": "..."}`) -> `201 {"liked": true, "trackId": "..."}`
  - `DELETE /api/library/likes/{track_id}` -> `200 {"liked": false, "trackId": "..."}`
- **Custom Playlists (Full CRUD):**
  - `GET /api/playlists` -> `{"playlists": [{"id", "name", "description", "trackCount", "covers": [...up to 4], "createdAt", "updatedAt"}]}`
  - `POST /api/playlists` body: `{"name": "Chill Night", "description": "..."}` -> `201 {"playlist": {...}}`
  - `GET /api/playlists/{playlist_id}` -> `200 {"playlist": {...}, "tracks": [...]}`
  - `PATCH /api/playlists/{playlist_id}` body: `{"name": "...", "description": "..."}` -> `200 {"playlist": {...}}`
  - `DELETE /api/playlists/{playlist_id}` -> `200 {"deleted": true, "id": "..."}`
  - `POST /api/playlists/{playlist_id}/tracks` body: `{"track": {...Track...}}` (or `{"trackId": "..."}`) -> `201 {"playlistId", "trackId", "position", "trackCount"}`
  - `DELETE /api/playlists/{playlist_id}/tracks/{track_id}` -> `200 {"removed": true, "trackCount": ...}`
  - `PATCH /api/playlists/{playlist_id}/tracks/reorder` body: `{"trackIds": ["id1", "id2", ...]}` -> `200 {"reordered": true}`
- **Play History:**
  - `GET /api/history?limit=50` -> `{"items": [{"playedAt": 1795345800, "track": {...Track...}}]}`
  - `POST /api/history` body: `{"track": {...Track...}}` -> `201 {"recorded": true}`
  - `DELETE /api/history` -> `200 {"cleared": true}`
- **Player Settings & Session Restore:**
  - `GET /api/settings` -> `{"volume": 0.8, "muted": false, "shuffle": false, "repeat": "off", "region": "global", "lastTrackId": null, "lastPosition": 0}`
  - `PATCH /api/settings` body: partial settings object -> `200 { ...updatedSettings }`

---

## 5. Persistence Design (SQLite Schema — `data/music.db`)

Enable `PRAGMA journal_mode=WAL;` and `PRAGMA foreign_keys=ON;` on every connection.

```sql
CREATE TABLE IF NOT EXISTS tracks_cache (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    artist_id TEXT DEFAULT '',
    album TEXT DEFAULT '',
    album_id TEXT DEFAULT '',
    duration INTEGER DEFAULT 0,
    cover_small TEXT DEFAULT '',
    cover_large TEXT DEFAULT '',
    explicit INTEGER DEFAULT 0,
    release_date TEXT DEFAULT '',
    preview_url TEXT DEFAULT '',
    extra_json TEXT DEFAULT '{}',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL,
    track_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, track_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS liked_tracks (
    track_id TEXT PRIMARY KEY,
    liked_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    played_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_played_at ON play_history(played_at DESC);

CREATE TABLE IF NOT EXISTS lyrics_cache (
    cache_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source TEXT DEFAULT '',
    instrumental INTEGER DEFAULT 0,
    synced_lrc TEXT DEFAULT '',
    plain_lyrics TEXT DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
```

Client also mirrors lightweight UI preferences (`volume`, `muted`, `shuffle`, `repeat`, `region`, `recentSearches`) in `localStorage` for instant zero-flicker hydration before `/api/settings` responds.

---

## 6. UI Architecture, Design Tokens & Interactive Features

### 6.1 Spotify-Inspired Dark Design Tokens (`frontend/css/tokens.css`)
- **Surfaces:** `--bg-base: #000000`, `--bg-surface: #121212`, `--bg-elevated: #181818`, `--bg-hover: #282828`, `--bg-active: #333333`, `--border-subtle: rgba(255, 255, 255, 0.08)`
- **Brand Accents:** `--accent-primary: #1DB954`, `--accent-hover: #1ED760`, `--accent-subtle: rgba(29, 185, 84, 0.15)`, `--liked-gradient: linear-gradient(135deg, #450AF5 0%, #8E8EE5 100%)`
- **Typography:** `--font-sans: 'Plus Jakarta Sans', 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`; `--text-primary: #FFFFFF`, `--text-secondary: #B3B3B3`, `--text-muted: #727272`
- **Layout Dimensions:** `--sidebar-width: 264px`, `--drawer-width: 344px`, `--player-height: 88px`, `--topbar-height: 64px`, `--radius-panel: 8px`, `--radius-pill: 9999px`
- **Micro-Interactions:** Hover green play FAB rising gently (`translateY(6px)` -> `0`, `opacity: 0` -> `1`), animated 3-bar CSS equalizer next to currently playing track row, smooth shimmer skeletons during fetch, subtle toast notifications.

### 6.2 Application Layout & Views
1. **Left Sidebar (`sidebar.js`):**
   - Brand header (`Sonify` + green wave SVG icon)
   - Primary links: `Home` (`#/`), `Search` (`#/search`), `History` (`#/history`)
   - **Your Library** card: header with `+` button (Create Playlist), quick filter pills (`All`, `Playlists`), pinned **Liked Songs** tile (live count), and scrollable list of custom playlists (with cover thumbnail / 2x2 mosaic, track count, quick Play on hover, and edit/delete menu).
2. **Top Header Bar:**
   - Browser Back/Forward buttons, persistent Search bar (syncs with `#/search`), Region selector (`Global` / `Vietnam`), subtle green `Ad-Free` status pill, and offline-mode notice banner (only shown if backend returns `mode: "offline"`).
3. **Main Views:**
   - **Home / Discover (`homeView.js`):** Dynamic greeting (*Good morning / afternoon / evening*), 6-card Quick Picks grid (Liked Songs + playlists + featured charts), horizontal carousels for *Trending Worldwide*, *Top Vietnam / V-Pop*, *New Releases & Hot Albums*, *Full-Track Indie & Electronic (Audius)*, *Popular Artists*, and *Recently Played*.
   - **Live Search (`searchView.js`):** Debounced (`300ms`) live search, Recent Searches pills, Browse Genre/Mood tiles (Pop, V-Pop, Hip-Hop, K-Pop, Electronic, Lo-Fi, Rock, Indie, R&B, Acoustic) when query is empty; filtered tabs (`All`, `Songs`, `Artists`, `Albums`), Spotify-style Top Result card + Songs list + Artists/Albums grids.
   - **Liked Songs (`libraryView.js`):** Gradient hero header, Play / Shuffle CTA, live filter input, interactive track table with instant unlike & add-to-playlist.
   - **Playlist Detail (`libraryView.js`):** 2x2 mosaic artwork from first 4 tracks, Edit Name/Description modal, Delete Playlist confirmation, Play / Shuffle CTA, numbered track table with Move Up/Down & Remove from Playlist, plus inline "Find songs to add" mini-search at bottom.
   - **Artist & Album Detail (`detailView.js`):** Hero artwork/avatar, Play All, Popular Tracks / Album tracklist, Discography grid.
   - **Play History (`historyView.js`):** Chronological played tracks with relative timestamps & Clear History button.
   - **Full-Stage Lyrics View (`#/lyrics`, `lyricsPanel.js`):** Immersive full-stage synced lyrics with rich ambient gradient background derived from current track artwork.
4. **Right Side Drawer (`queuePanel.js` & `lyricsPanel.js`):**
   - Toggleable drawer showing either **Queue** (`Now Playing`, user-added `Next in Queue` with remove/reorder/clear, and `Next Up` context tracks) OR **Synced Lyrics** alongside browsing.
5. **Persistent Bottom Player Bar (`playerBar.js`):**
   - **Left:** Cover art, scrolling/truncated title & clickable artist link, Like (Heart) toggle, stream quality badge (`Full Track` / `30s Preview` / `Offline Demo`).
   - **Center:** Shuffle, Previous, Play/Pause, Next, Repeat (`off` -> `all` -> `one` with `1` indicator); live elapsed time, scrubbable Seek Bar with buffered track indicator, total duration.
   - **Right:** Lyrics drawer toggle, Full-Stage Lyrics button, Queue drawer toggle, Mute toggle + Volume slider.
   - Integrates **Browser Media Session API** (`navigator.mediaSession`) for OS/keyboard media keys and lock-screen artwork.
6. **Synced Lyrics UX Details:**
   - Binary search over `lines[]` driven by `audio.currentTime` + `requestAnimationFrame` while playing for crisp active-line transitions.
   - Smooth auto-scroll keeping active line vertically centered; **manual user scroll pauses auto-scroll for 4 seconds** and displays a floating `"Re-sync"` button.
   - **Click any synced lyric line to seek** (`audio.currentTime = line.time`, starts playback if paused).
   - Graceful rendering for **Plain Lyrics** (`Unsynced lyrics` badge), **Instrumental** (music note illustration), and **Not Found** (with inline manual search override form so user can edit title/artist and retry).
7. **Context Menu (`contextMenu.js`) & Keyboard Shortcuts (`main.js`):**
   - Right-click or `...` button on any track: `Play Now`, `Play Next`, `Add to Queue`, `Save / Remove Liked Songs`, `Add to Playlist ▸` (all playlists + `+ Create New Playlist`), `View Lyrics`, `Go to Artist`, `Go to Album`.
   - Shortcuts (suppressed when focus is inside `<input>`, `<textarea>`, `<select>`):
     - `Space`: Play/Pause
     - `ArrowRight` / `ArrowLeft`: Seek `+5s` / `-5s`
     - `Shift+ArrowRight` / `Shift+ArrowLeft`: Next / Previous track
     - `ArrowUp` / `ArrowDown`: Volume `+5%` / `-5%`
     - `M`: Mute/Unmute | `S`: Shuffle | `R`: Cycle Repeat | `L`: Toggle Lyrics | `Q`: Toggle Queue | `/`: Focus Search | `Escape`: Close menus/modals.
8. **Responsive Breakpoints:**
   - Desktop (`>= 1024px`): 3-pane Spotify layout + 88px bottom player bar.
   - Tablet (`768px–1023px`): Narrow sidebar + slide-over Queue/Lyrics drawer.
   - Mobile (`< 768px`): Bottom navigation bar (`Home`, `Search`, `Library`, `Lyrics`) + compact mini-player above bottom nav.

---

## 7. Exact Project Folder Layout

```text
/mnt/e/lam lai tu dau/music/
├── app.py                          # Entry point: python3 app.py [--host 127.0.0.1] [--port 8080]
├── README.md                       # Run instructions, feature overview, architecture notes
├── docs/
│   └── ARCHITECTURE_SPEC.md        # This specification
├── data/                           # Auto-created at runtime
│   └── music.db                    # SQLite persistence database
├── backend/
│   ├── __init__.py
│   ├── config.py                   # Host, port, DB path, cache TTLs, timeouts, offline env toggle
│   ├── db.py                       # SQLite init, connection helper, CRUD queries
│   ├── http_client.py              # Pure-stdlib HTTP/HTTPS client with gzip, timeouts, streaming
│   ├── crypto_des.py               # JioSaavn DES-ECB decryptor (cryptography + pure-Python fallback)
│   ├── lrc_parser.py               # .lrc parser (multi-timestamp, offset, metadata strip, sorting)
│   ├── offline_catalog.py          # Curated fallback catalog + synced LRC + stereo WAV generator
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── deezer.py               # Deezer charts, releases, search, artist, album, track refresh
│   │   ├── apple_itunes.py         # Apple RSS (VN + US) & iTunes search/lookup
│   │   ├── jiosaavn.py             # JioSaavn api.php + DES-ECB decrypt + mirror fallback
│   │   ├── audius.py               # Audius node discovery, trending, search, full-track stream
│   │   ├── youtube_music.py        # InnerTube search/player multi-client + Piped/Invidious + opt yt-dlp
│   │   └── lyrics.py               # LRCLIB (/get + /search) + YTM + lyrics.ovh + cache
│   ├── services/
│   │   ├── __init__.py
│   │   ├── catalog_service.py      # Home, Search (dedup/ranking), Artist, Album + TTL cache + offline fallback
│   │   ├── stream_service.py       # 5-tier full-track ad-free resolver + Range proxy
│   │   └── library_service.py      # Playlists CRUD, Likes, History, Settings
│   └── server.py                   # ThreadingHTTPServer API router + static frontend server
├── frontend/
│   ├── index.html
│   ├── favicon.svg
│   ├── css/
│   │   ├── tokens.css
│   │   ├── layout.css
│   │   ├── components.css
│   │   └── lyrics.css
│   └── js/
│       ├── main.js
│       ├── api.js
│       ├── store.js
│       ├── player.js
│       ├── lrc.js
│       ├── icons.js
│       ├── router.js
│       ├── components/
│       │   ├── sidebar.js
│       │   ├── playerBar.js
│       │   ├── trackList.js
│       │   ├── queuePanel.js
│       │   ├── lyricsPanel.js
│       │   ├── contextMenu.js
│       │   └── modals.js
│       └── views/
│           ├── homeView.js
│           ├── searchView.js
│           ├── libraryView.js
│           ├── detailView.js
│           └── historyView.js
└── tests/                          # Owned by @tester (python3 -m unittest discover -s tests)
```

---

## 8. QA & Acceptance Criteria (For `@builder` & `@tester`)

### Functional & API Acceptance Checklist
1. **Zero-Install Launch:** `python3 app.py --port 8080` starts cleanly with zero missing imports, initializes `data/music.db`, serves `frontend/index.html` at `http://127.0.0.1:8080/`, and passes `GET /api/health` (`200 OK`, `adFree: true`).
2. **Up-to-Date Catalog & Live Search:**
   - `GET /api/home` returns populated sections (Global Trending, Vietnam/V-Pop, New Releases, Audius Full Tracks, Popular Artists) when online, and cleanly falls back to `mode: "offline"` with populated demo catalog when network is unreachable (never 500s).
   - `GET /api/search?q=...` returns deduplicated tracks, artists, and albums; supports Vietnamese diacritics and English queries; handles empty queries with `400` or empty result cleanly.
   - `GET /api/artist/{id}` and `GET /api/album/{id}` return valid artist/album metadata and tracks.
3. **100% Ad-Free Streaming Proxy & HTTP Range Support:**
   - `GET /api/stream/{track_id}` returns `200 OK` with valid audio `Content-Type`, `Accept-Ranges: bytes`, `X-Ad-Free: true`, `X-Stream-Mode`, and non-empty audio bytes.
   - `GET /api/stream/{track_id}` with header `Range: bytes=0-1023` returns **`206 Partial Content`**, `Content-Length: 1024`, and `Content-Range: bytes 0-1023/{total}`.
   - `DES-ECB` unit test confirms `crypto_des.py` accurately encrypts/decrypts JioSaavn URLs with key `38346591` and upgrades bitrate suffix safely.
   - Zero ad scripts, ad domains, or ad banners anywhere in frontend HTML/CSS/JS or backend responses.
4. **Synced & Plain Lyrics + LRC Parser:**
   - `lrc_parser.py` unit tests verify standard `[mm:ss.xx]`, `[mm:ss.xxx]`, multi-timestamp lines (`[00:10.00][01:00.00]Chorus`), `[offset:+/-ms]`, metadata tag stripping, and ascending timestamp sort.
   - `GET /api/lyrics` queries LRCLIB with required `User-Agent`, falls back cleanly to plain lyrics / instrumental / not-found / offline demo LRC, and caches in SQLite.
   - Frontend synced lyrics highlights the exact active line via binary search, auto-scrolls smoothly (pausing on manual user scroll), and seeks audio immediately when a lyric line is clicked.
5. **Library Persistence & Playlist CRUD:**
   - Create, list, fetch, rename/edit description, add track, prevent duplicate errors cleanly, reorder tracks, remove track, and delete playlist all persist across server restarts in `data/music.db`.
   - Like / Unlike songs persists and reflects immediately across Home, Search, Album, Liked Songs, and Player Bar.
   - Play History records played tracks chronologically and supports Clear History.
   - Settings (`volume`, `muted`, `shuffle`, `repeat`, `region`) persist via `/api/settings`.
6. **Frontend & Interactive Quality:**
   - All JS ES modules load with valid syntax (`200 OK`, `application/javascript`) and zero uncaught runtime errors.
   - Complete Spotify-like dark UI with Home, Search, Liked Songs, Custom Playlists, Artist/Album detail, History, Queue drawer, Synced/Plain Lyrics drawer & full-stage view, Bottom Player Bar (play/pause, prev/next, scrubbable seek, shuffle, repeat off/all/one, volume/mute), context menus, keyboard shortcuts, and responsive mobile/tablet layout.
7. **Automated Tests (`tests/`):**
   - `python3 -m unittest discover -s tests -v` passes 100% cleanly in both sandboxed/offline mode (testing HTTP server, Range streaming, DES decryptor, LRC parser, SQLite CRUD, mocked upstream waterfall & fallback paths) and live online verification when network access is enabled.
