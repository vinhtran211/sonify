# Sonify Streaming Bug — Live Root-Cause Analysis & Verified Fix Specification

> **Investigator:** `@researcher`  
> **Trigger:** User reported (`#10`) that clicking **Đừng Làm Trái Tim Anh Đau — Sơn Tùng M-TP** (`4:39`) displayed `Full Track • youtube-innertube` while actually playing the 45-second `offline-synth` WAV theme song (`0:45`).  
> **Status:** Root causes empirically reproduced and fixed protocol verified **7/7 LIVE** across V-Pop, Global Pop, and K-Pop.

---

## 1. Exact Root Causes (Empirically Reproduced Live)

### Root Cause 1: YouTube GVS PO-Token `403 Forbidden` on `adaptiveFormats` + False-Positive `bytes=0-0` Probe
1. `backend/providers/youtube_music.py` (`_resolve_via_innertube_player`) rotated `ANDROID_VR` (returns `LOGIN_REQUIRED`) → `IOS` (returns `adaptiveFormats` only, `formats: []`) → `TVHTML5_SIMPLY_EMBEDDED_PLAYER` (returns `ERROR: YouTube is no longer supported`).
2. `IOS` returned `adaptiveFormats` (`itag=140` `audio/mp4`, `itag=251` `audio/webm`).
3. `http_client.probe_url()` tested the URL with **`Range: bytes=0-0`**. Without a GVS PO-token, YouTube's CDN (`googlevideo.com`) **allows small bounded ranges within the first ~1 MiB (~25% of file)** (`206 Partial Content`), **but rejects open-ended `Range: bytes=0-` (which every browser `<audio>`/`<video>` sends!) and any byte offset past ~1 MiB with `HTTP 403 Forbidden`!**
4. Therefore:
   - `probe_url` (`bytes=0-0`) falsely reported `ok=True` → `resolve_stream()` cached `ResolvedStream(mode="full", source="youtube-innertube")` → `/api/stream/{id}/info` told the UI **`Full Track • youtube-innertube`**.
   - Immediately afterward, the browser's `<audio src="/api/stream/...">` sent **`Range: bytes=0-`** → `stream_to_handler()` forwarded `Range: bytes=0-` to `googlevideo.com` → **`HTTP 403 Forbidden`**.

### Root Cause 2: Silent Fallback to 45s `offline-synth` WAV in Online Mode (`stream_service.py`)
1. When `stream_to_handler()` caught `HTTPError 403` on attempt 0 (lines 508–514), it called `resolve_stream(track_id, force_refresh=True)` **without excluding the failed source**, re-resolving the exact same failing `youtube-innertube` URL.
2. On attempt 1 failure (lines 515–518 & 531–533), `stream_to_handler()` **skipped Tier 3 (Audius) and Tier 4 (official Deezer/iTunes preview)** and **silently served `self._tier5_offline_wav(track_id)`** (45-second procedural WAV chord theme)!
3. Similarly, `resolve_stream()` (line 357) silently returned `_tier5_offline_wav()` even in online mode if upstream tiers failed.

### Root Cause 3: Unsynchronized Parallel `/info` + `/stream` Requests (`frontend/js/player.js`)
- `startTrackPlayback()` simultaneously assigned `audioEl.src = /api/stream/{id}` and called `api.getStreamInfo(track.id)`, causing duplicate cold-cache resolutions and allowing the badge (`Full Track • youtube-innertube`) to lie about what `/api/stream/{id}` actually served.

### Root Cause 4: Cross-Matcher False-Positive Risks (`youtube_music.py`, `jiosaavn.py`, `audius.py`)
- Live JioSaavn searches from non-India IPs returned **unrelated cover artists** for Western major-label songs (e.g., `"Lady Gaga Die With A Smile"` returned `"Die With A Smile"` by `Nyae`, `Sound Tower`, `Sunset Roses`). Without a hard `artist_sim` floor, a cover with matching title + duration can score `0.70` (borderline hijack).
- `youtube_music.find_best_video_id()` used `best_score >= 0.45` with **no minimum `title_sim` floor** and **no `artist_sim` floor**, allowing a wrong song by the same artist (`0.30*1.0 + 0.15*1.0 = 0.45`) or an unrelated cover/remix/piano version to pass.

---

## 2. Empirically Verified Working Full-Track Protocol (7/7 Live Pass)

### Primary YouTube InnerTube Client: `ANDROID` (`20.10.38`) Progressive `formats` (`itag=18`)
Live testing across all 7 requested songs proved that **`ANDROID` (`20.10.38`)** returns `playabilityStatus.status = "OK"` AND includes **`streamingData.formats` (`itag=18`, progressive fast-start `ftypmp42` MP4 with `moov` at byte 24 and stereo AAC-LC `mp4a.40.2`)** which is **completely exempt from YouTube's GVS PO-token 1 MiB cutoff**:

- **Endpoint:** `POST https://www.youtube.com/youtubei/v1/player?prettyPrint=false`
- **Request Headers:**
  ```http
  User-Agent: com.google.android.youtube/20.10.38 (Linux; U; Android 15) gzip
  Content-Type: application/json
  X-YouTube-Client-Name: 3
  X-YouTube-Client-Version: 20.10.38
  ```
- **Request Body** (do **NOT** include `thirdParty`):
  ```json
  {
    "videoId": "<videoId>",
    "context": {
      "client": {
        "clientName": "ANDROID",
        "clientVersion": "20.10.38",
        "androidSdkVersion": 35,
        "osName": "Android",
        "osVersion": "15",
        "hl": "en",
        "gl": "US"
      }
    },
    "contentCheckOk": true,
    "racyCheckOk": true
  }
  ```
- **Stream Proxy Header:** Forward the exact same `User-Agent: com.google.android.youtube/20.10.38 (Linux; U; Android 15) gzip` to `googlevideo.com`.

### Live Empirical Proof (All 7 Songs Verified via Real Byte-Range HTTP GETs)

| # | Track | Artist | YTM `videoId` | Resolved YouTube Official Track | Duration | `itag=18` MP4 Size | `Range: bytes=0-` | `LAST_64KB` |
|---|---|---|---|---|---|---|---|---|
| 1 | **Đừng Làm Trái Tim Anh Đau** | Sơn Tùng M-TP | `7u4g483WTzw` | `Đừng Làm Trái Tim Anh Đau` (`Sơn Tùng M-TP - Topic`) | **279.3s (4:39)** | **2,339,881 B** | **206 OK (100% in 0.11s)** | **206 OK** |
| 2 | **Hãy Trao Cho Anh** | Sơn Tùng M-TP | `emMR03tG2CE` | `Hãy Trao Cho Anh` (`Sơn Tùng M-TP - Topic`) | **245.7s (4:06)** | **2,156,084 B** | **206 OK** | **206 OK** |
| 3 | **Chúng Ta Của Tương Lai** | Sơn Tùng M-TP | `UoXllQoqEBY` | `Chúng Ta Của Tương Lai` (`Sơn Tùng M-TP - Topic`) | **249.9s (4:10)** | **1,946,142 B** | **206 OK** | **206 OK** |
| 4 | **Lạc Trôi** | Sơn Tùng M-TP | `RKvRLLQtDbg` | `Lạc Trôi` (`Sơn Tùng M-TP - Topic`) | **233.0s (3:53)** | **1,975,710 B** | **206 OK** | **206 OK** |
| 5 | **Blinding Lights** | The Weeknd | `J7p4bzqLvCw` | `Blinding Lights` (`The Weeknd - Topic`) | **201.6s (3:22)** | **1,784,661 B** | **206 OK** | **206 OK** |
| 6 | **Die With A Smile** | Lady Gaga & Bruno Mars | `DlFXDl_ROAM` | `Die With A Smile` (`Lady Gaga - Topic`) | **251.8s (4:12)** | **1,942,223 B** | **206 OK** | **206 OK** |
| 7 | **밤편지 (Through the Night)** | IU | `NnRjwEhFU70` | `Through the Night (밤편지)` (`IU - Topic`) | **253.3s (4:13)** | **4,410,928 B** | **206 OK** | **206 OK** |

- MP4 structure verified on `7u4g483WTzw`: `[('ftyp', 24), ('moov', 59925), ('mdat', 2279932)]` — **fast-start MP4** with `moov` at byte 24 for immediate duration detection and instant scrubbing.

---

## 3. Required Code Fixes for `@builder`

### 3.1 `backend/http_client.py` — Real Playability Probe (`probe_url`)
- Change `probe_url()` to send **`Range: bytes=0-`** (NOT `bytes=0-0`!), read at most `1024` bytes, then close the connection immediately.
- Verify:
  1. HTTP status is `200` or `206`.
  2. `Content-Type` starts with `audio/`, `video/mp4`, `video/webm`, or is `application/octet-stream` (reject `text/html` / `application/json` / error pages).
  3. At least 1 byte was readable (and if `Content-Range` reports total size `T > 1_200_000`, optionally keep an optional `verify_tail=True` parameter for YouTube adaptive streams that probes `bytes={T-1024}-{T-1}` so partial 1 MiB PO-token traps can **never** pass).
- If a server rejects `Range: bytes=0-` with `416` or `405`, fall back to plain `GET` (no `Range`), read 1024 bytes, close.

### 3.2 `backend/providers/youtube_music.py` — Working `ANDROID` `itag=18` + Strict Song Matching
1. **Player Client Order & Format Extraction (`_resolve_via_innertube_player`):**
   - Primary client: **`ANDROID` (`20.10.38`, `androidSdkVersion: 35`, `osName: "Android"`, `osVersion: "15"`, UA `com.google.android.youtube/20.10.38 (Linux; U; Android 15) gzip`, headers `X-YouTube-Client-Name: 3`, `X-YouTube-Client-Version: 20.10.38`, NO `thirdParty`)**.
   - Secondary fallback clients (if needed): `IOS` (`20.10.4` with `X-YouTube-Client-Name: 5`) and `ANDROID_VR` (`1.60.19`).
   - **Candidate Selection Order per Client:**
     1. First try audio-only `adaptiveFormats` ONLY IF they pass **`probe_url(..., verify_tail=True)`** (both `Range: bytes=0-` AND tail `bytes={total-1024}-{total-1}` succeed — future-proof if a PO-token-free adaptive stream or `yt-dlp` works).
     2. Otherwise select **`streamingData.formats`** progressive MP4 (`itag=18` or any `video/mp4` format with `mp4a` audio codec and direct `url`), verified with **`probe_url(url, headers=hdrs)` (`Range: bytes=0-`)**.
     3. Return accurate MIME type **`video/mp4`** for progressive `itag=18` (and `audio/mp4` / `audio/webm` for audio-only adaptive formats).
   - **Important:** When `probe_url()` follows a redirect to `final_url`, keep the returned `final_url` (or original `url` if redirect URLs are single-use — test shows `googlevideo.com` direct URL from player has no redirect and reuses cleanly for 30 min).
2. **Strict Song Matching (`find_best_video_id`):**
   - Strip `" - Topic"`, `"Official"`, `"VEVO"` from candidate artist before computing `artist_sim`.
   - **Hard Acceptance Gates:**
     - `title_sim >= 0.55` (never match 0% title overlap!).
     - If target `artist` is non-empty and not `"Unknown Artist"`, require `artist_sim >= 0.40` (or normalized primary artist substring match in candidate artist OR candidate title).
     - If both target `duration > 30` and `cand_dur > 0`, reject if `abs(cand_dur - duration) > max(35, int(duration * 0.25))`.
     - Expand `_PENALTY_KEYWORDS` to include `"remix"`, `"live"`, `"piano"`, `"instrumental"`, `"acoustic"`, `"lofi"`, `"lo-fi"`, `"mashup"`, `"teaser"`, `"trailer"`, `"reaction"`, `"parody"`, `"beat"`, `"guitar"`, `"violin"`, `"tribute"`, `"lullaby"` (penalized `-0.35` unless present in target title/artist).
     - Bonus `+0.08` when duration is within `±5s` of target duration.
     - Require final `best_score >= 0.62`.
   - Also validate matched `videoDetails` inside `_resolve_via_innertube_player` (optional return of actual duration/title for logging/verification).

### 3.3 `backend/providers/jiosaavn.py` & `backend/providers/audius.py` — Hard Artist & Title Floors
- In **both** `jiosaavn.find_best_stream()` and `audius.find_best_stream()`:
  - Require **`title_sim >= 0.60`** AND (when target artist is known) **`artist_sim >= 0.50`** AND duration within `max(25, target_dur * 0.20)`!
  - Apply the same noise-keyword penalty (`nightcore`, `techno`, `ambient`, `cover`, `remix`, `karaoke`, `instrumental`, `slowed`, `sped up`, `lofi`, `piano`) when not present in target title.
  - This guarantees JioSaavn cover artists (like `Nyae` / `Sound Tower` for `Die With A Smile`) NEVER hijack the real track!

### 3.4 `backend/services/stream_service.py` — Zero Silent `offline-synth` in Online Mode + Real Waterfall Cascade
1. **Strict Offline-Synth Guard:**
   - **ONLY** return `_tier5_offline_wav()` when `track_id.startswith("local:")` OR `http_client.is_offline_mode()` is `True`!
   - In online mode, **NEVER** silently substitute `_tier5_offline_wav()` when a real track fails to stream.
2. **Tier Order in Online Mode:**
   - **Tier 0:** Native full-track direct (`au:*` → Audius, `js:*` → JioSaavn, `yt:*` → YouTube Music).
   - **Tier 1 (Primary Universal Full-Track):** YouTube Music (`yt-dlp` if installed → `ANDROID` `itag=18` → Piped/Invidious).
   - **Tier 2:** JioSaavn 320kbps cross-match (strict artist+title gate).
   - **Tier 3:** Audius full-track cross-match (strict artist+title gate).
   - **Tier 4:** Official 30-second preview of **that exact track** (`deezer-preview` / `itunes-preview`, refreshing expired Deezer signed URL once).
   - **No Tier 5 in Online Mode:** If Tiers 0–4 all fail, raise a typed `StreamUnavailableError` → server returns **`HTTP 502 Bad Gateway`** JSON `{"error": {"code": "STREAM_UNAVAILABLE", "message": "Unable to stream this track right now."}}` (and `/api/stream/{track_id}/info` returns `502` or `{"available": false, ...}`).
3. **Exclude-Failed-Source Retry & Single-Flight Locking:**
   - Add per-`track_id` resolution lock so simultaneous `/info` + `/stream` requests share one resolution cleanly.
   - Add `exclude_sources: Optional[set[str]] = None` to `resolve_stream()`. If `stream_to_handler()` encounters upstream `403`/`404`/`410`/network error before headers are sent, invalidate cache, add `resolved.source` to `exclude_sources`, and call `resolve_stream(track_id, force_refresh=True, exclude_sources=exclude_sources)` so it genuinely cascades to the next real provider (e.g. preview) instead of looping the same broken URL or serving fake WAV!
4. **Accept `video/mp4` / `video/webm` in Upstream Content-Type Pass-Through (`stream_to_handler`):**
   - Line 468 currently checks `if ctype.startswith("audio/"):` — update to `if ctype.startswith(("audio/", "video/mp4", "video/webm")):` so `itag=18`'s accurate `Content-Type: video/mp4` is preserved cleanly for Chrome, Firefox, Edge, and Safari.

### 3.5 `frontend/index.html`, `frontend/js/player.js`, `frontend/js/components/playerBar.js` — Cross-Browser Playback & Honest UI
1. **`frontend/index.html`:** Replace `<audio id="audio-engine" preload="auto"></audio>` with:
   ```html
   <video id="audio-engine" preload="auto" playsinline style="display:none;width:0;height:0;position:absolute;pointer-events:none" aria-hidden="true"></video>
   ```
   A hidden `<video>` element exposes the exact same `HTMLMediaElement` API as `<audio>` while reliably decoding both `video/mp4` (`itag=18` `avc1 + mp4a.40.2`) AND `audio/*` (`mp4`, `mpeg`, `webm`, `wav`) across **Chrome, Edge, Firefox, AND Safari** with zero Firefox `DEMUXER_ERROR`!
2. **`frontend/js/player.js`:**
   - In `startTrackPlayback(track)`: resolve `api.getStreamInfo(track.id, signal)` **first** (warms single-flight server cache and confirms real playability before setting `audioEl.src`), update `streamMode` / `streamSource` accurately from the resolved response, and then set `audioEl.src = /api/stream/${encodeURIComponent(track.id)}` (with a cache-busting query param if retrying) and call `audioEl.play()`.
   - On `502` / stream unavailable or `audioEl` `'error'` event: display an honest error toast (e.g., `"Could not stream this track — try another result or check connection"`), set `streamMode: 'error'`, `isPlaying: false`, `isLoading: false`, and NEVER claim `Full Track` while failing.
3. **`frontend/js/components/playerBar.js`:**
   - Add clean visual badge state for `streamMode === 'error'` (`Stream Unavailable`).
   - Keep `Full Track` / `30s Preview` / `Offline Studio Synth` strictly aligned with the actual audio stream being played.

---

## 4. Verification Criteria for `@tester`

1. **Automated Tests (`tests/`):** Update/add tests confirming:
   - `probe_url` uses `Range: bytes=0-` (rejects URLs that fail open-ended Range with `403` even if `bytes=0-0` succeeds).
   - `youtube_music` extracts `ANDROID` `20.10.38` progressive `itag=18` (`video/mp4`) cleanly and rejects cross-match covers/wrong titles/wrong durations.
   - `jiosaavn` and `audius` reject cover artists with `artist_sim < 0.50`.
   - `StreamService` in online mode NEVER falls back to `offline-synth` (cascades to preview or returns `502 STREAM_UNAVAILABLE`), while `--offline` and `local:*` tracks continue using deterministic `offline-synth` WAV seamlessly.
2. **Live Online End-to-End Verification (Real Audio Bytes!):**
   - Start live Sonify server (online mode) and test **Đừng Làm Trái Tim Anh Đau — Sơn Tùng M-TP** (plus Hãy Trao Cho Anh, Chúng Ta Của Tương Lai, Lạc Trôi, Blinding Lights, Die With A Smile, 밤편지 — IU):
     - Confirm `/api/stream/{id}/info` returns `streamMode: "full"`, `streamSource: "youtube-innertube"`, `contentType: "video/mp4"` (NOT `audio/wav`!).
     - Confirm `GET /api/stream/{id}` with `Range: bytes=0-` returns `206 Partial Content`, `Content-Type: video/mp4`, `X-Stream-Mode: full`, `X-Ad-Free: true`, valid MP4 magic (`ftypmp42`), `Content-Length > 1,500,000` bytes (for Đừng Làm Trái Tim Anh Đau: ~2.34 MB, NOT the 7.9 MB 45s WAV!), AND `LAST_64KB` Range succeeds with `206`.
     - Confirm parsed MP4 `mvhd` duration matches ~279s (`4:39`) for **Đừng Làm Trái Tim Anh Đau** (NOT `0:45`!).
     - Confirm synced LRCLIB lyrics still load alongside the real full-length track.
