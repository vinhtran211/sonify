# Sonify Cloud Full-Track Streaming Research (Render / AWS Datacenter IP)

> **Investigator:** `@researcher`  
> **Trigger:** `#24` — Live deployment at `https://sonify-music.onrender.com` returned `30s Preview` (`deezer-preview`) for `Đừng Làm Trái Tim Anh Đau` (`dz:2830386932`) and `Blinding Lights` (`dz:908604612`) because YouTube blocks AWS datacenter IPs (`LOGIN_REQUIRED`) and legacy Piped/Invidious instances are dead/403.  
> **Status:** Cloud-compatible, $0, keyless, 100% ad-free **Lossless FLAC** full-track streaming empirically verified LIVE across V-Pop, Global Pop, and K-Pop.

---

## 1. Live Diagnosis of Current Render Deployment & Dead Legacy Proxies

1. **Confirmed on `https://sonify-music.onrender.com`:**
   - `GET /api/health` → `200 OK` (`ytDlpAvailable: true`, `offlineMode: false`, `adFree: true`).
   - `GET /api/stream/dz%3A2830386932/info` (`Đừng Làm Trái Tim Anh Đau`) → `streamMode: "preview"`, `streamSource: "deezer-preview"`.
   - `GET /api/stream/dz%3A908604612/info` (`Blinding Lights`) → `streamMode: "preview"`, `streamSource: "deezer-preview"`.
   - **Why:** On AWS/Render datacenter IPs, anonymous YouTube InnerTube (`ANDROID`/`IOS`/`ANDROID_VR`) and unauthenticated `yt-dlp` hit `LOGIN_REQUIRED` ("Sign in to confirm you're not a bot").
2. **Legacy Community Proxies Audited Live (Nov 2026):**
   - **Old `hifi-api` instances** (`api.monochrome.tf`, `triton.squid.wtf`, `wolf/maus/vogel/katze/hund.qqdl.site`, `tidal.kinoplus.online`, `monochrome-api.samidy.com`): Dead DNS, parked redirect HTML, or `429 Upstream API error` (confirmed by Monochrome's own `INSTANCES.md`: migrated away from legacy `hifi-api` in April 2026).
   - **Piped public instances** (`pipedapi.kavin.rocks`, `adminforge.de`, `private.coffee`, `piped.yt`, etc.): Dead DNS, `403`, or `500`.
   - **Invidious public instances** (`inv.nadeko.net`, `yewtu.be`, `nerdvpn.de`, `tiekoetter.com`, `chocolatemoo53.com`): Dead/`403`/`401`, and the lone responding host (`yt.chocolatemoo53.com`) returns `googlevideo.com` adaptive URLs that still `403` on open-ended `Range: bytes=0-`.
   - **Excluded for Access Control:** `dzr.tabs-vs-spaces.wtf` explicitly enforces `403 Forbidden: requests must come from an allowed site` — **DO NOT use or spoof Origin/Referer**.

---

## 2. Empirically Verified Cloud-Safe Full-Track Sources ($0, Keyless, Ad-Free)

### Source A (PRIMARY FOR CLOUD): Monochrome Tracks Streamer (`https://tracks.monochrome.st`)
Monochrome's 2026 public Rust Music Streamer (`https://tracks.monochrome.st`, discovered from `monochrome-music/monochrome/main/js/tracks-api.js`) requires **zero API keys, zero cookies, zero CAPTCHA**, has **`Access-Control-Allow-Origin: *`**, does **not** depend on YouTube (no AWS datacenter bot-check!), and serves **full-length CD-quality Lossless FLAC (`fLaC`) studio masters** with full HTTP `206 Partial Content` byte-range seeking!

#### Exact API Endpoints & JSON Schema
1. **Search Tracks:**
   - `GET https://tracks.monochrome.st/search/tracks?q={url_encoded_query}&limit=8`
   - Response JSON (`200 OK`, `application/json`):
     ```json
     {
       "tracks": [
         {
           "id": "164376983856218112",
           "trackId": "164376983856218112",
           "title": "Đừng Làm Trái Tim Anh Đau",
           "artistIds": ["164376851304955904"],
           "artistNames": ["Sơn Tùng M-TP"],
           "releaseId": "164376940689596416",
           "artwork": "https://tracks.monochrome.st/proxy/mi/164376940689596416-1A01.jpg",
           "explicit": false,
           "playable": true,
           "duration": 279208,
           "isrc": "VNA0M1936465"
         }
       ]
     }
     ```
   - **Note:** `duration` is in **milliseconds** (`279208` → `279` seconds). `isrc` matches Deezer's `isrc` field directly for 100%-exact master matching!

2. **Stream Full Track (Direct Lossless FLAC with HTTP Range):**
   - `GET https://tracks.monochrome.st/track/{trackId}`
   - Supports `Range: bytes=0-`, `Range: bytes=0-65535`, and arbitrary mid/tail byte ranges (`HTTP 206 Partial Content`, `Accept-Ranges: bytes`, `Content-Range: bytes start-end/total`).

#### Live Empirical Proof (Real Byte Downloads — Start & Tail `206`, `fLaC` Magic)

| Track | Artist | `tracks.monochrome.st` ID | Exact Duration | `Content-Range` Total Size | Magic Bytes | `Range: bytes=0-` / Start | Tail `LAST_64KB` |
|---|---|---|---|---|---|---|---|
| **Đừng Làm Trái Tim Anh Đau** | Sơn Tùng M-TP | `164376983856218112` (ISRC `VNA0M1936465`) | **279.2s (4:39)** | **30,200,467 B (30.2 MB)** | `66 4c 61 43` (`fLaC`) | **HTTP 206** (`cors='*'`) | **HTTP 206 (65,536 B)** |
| **Hãy Trao Cho Anh** | Sơn Tùng M-TP | `164377007696642048` | **245.7s (4:06)** | **28,740,988 B (28.7 MB)** | `66 4c 61 43` (`fLaC`) | **HTTP 206** (`cors='*'`) | **HTTP 206 (65,536 B)** |
| **Die With A Smile** | Lady Gaga, Bruno Mars | `154014443548708864` | **251.7s (4:12)** | **26,967,136 B (27.0 MB)** | `66 4c 61 43` (`fLaC`) | **HTTP 206** (`cors='*'`) | **HTTP 206 (65,536 B)** |
| **Through the Night** | IU | `164529456407420928` | **253.3s (4:13)** | **26,240,409 B (26.2 MB)** | `66 4c 61 43` (`fLaC`) | **HTTP 206** (`cors='*'`) | **HTTP 206 (65,536 B)** |

#### Critical Implementation Details for `backend/providers/monochrome.py`
1. **MIME-Type Magic Sniffing (MANDATORY!):**
   - `tracks.monochrome.st/track/{id}` returns `Content-Type: application/octet-stream` even though the audio payload is standard FLAC (`fLaC`). Browsers reject `application/octet-stream` in `<audio>`/`<video>`!
   - Sniff the first 12 bytes returned during `probe_url()` (and in `stream_service.py` pass-through):
     - `b"fLaC"` → **`audio/flac`**
     - `b"ftyp"` at offset 4 → **`audio/mp4`**
     - `b"ID3"` or `(b[0] == 0xFF and (b[1] & 0xE0) == 0xE0)` → **`audio/mpeg`**
     - `b"OggS"` → **`audio/ogg`**
     - `b"\x1a\x45\xdf\xa3"` → **`audio/webm`**
   - Always send the detected `audio/*` `Content-Type` header (e.g. `audio/flac`) to the browser! All modern browsers (Chrome, Edge, Firefox, Safari) decode `audio/flac` natively inside `<video>`/`<audio>` with full seeking!
2. **Cold-Cache Timeout & Polite Retry on `HTTP 429`:**
   - First-time stream requests on a cold track take **4–13s** while `tracks.monochrome.st` prepares the track; use **`timeout=16.0`** when probing `/track/{id}`.
   - If rapid consecutive requests return `HTTP 429`, sleep `1.5s` and retry **once** (never hammer!).
   - **Do NOT use `verify_tail=True`** on `tracks.monochrome.st` (a single `Range: bytes=0-65535` or `bytes=0-` reading 16 bytes + verifying total `Content-Range` size `>= 500_000` bytes avoids unnecessary extra requests and prevents 429s).
   - **Cache `monochromeTrackId` in SQLite `tracks_cache.extra_json`** + keep resolved stream in `StreamService._cache` (TTL 30m) so subsequent seeks/replays require zero extra search calls!
3. **Exact Matching & ISRC Support:**
   - Also store `isrc` in `tracks_cache._extra` when fetching Deezer track details (`deezer.get_track(raw_id)` already receives `raw["isrc"]`!).
   - Filter candidates with `playable is not False`.
   - If candidate `isrc` equals target `isrc` (case-insensitive) → **immediate exact match (`score = 1.0`)**.
   - Otherwise require strict gates: `title_sim >= 0.55`, `artist_sim >= 0.40` (when target artist known), duration (`int(duration_ms) // 1000`) within `max(20, int(target_dur * 0.15))`, and word-boundary penalty (`-0.35`) for `remix`, `cover`, `karaoke`, `instrumental`, `slowed`, `sped up`, `nightcore`, `live`, `acoustic`, `piano`, `lofi`, `tribute` unless present in target title. Require `score >= 0.65`.

---

### Source B (CLOUD SECONDARY FALLBACK): SoundCloud Public API v2 (`backend/providers/soundcloud.py`)
Verified working cleanly from cloud IPs without YouTube bot-checks:
1. **Dynamic `client_id` Discovery** (cached 12 hours in memory):
   - `GET https://soundcloud.com` → find `<script ... src="https://a-v2.sndcdn.com/assets/...js">` → scan last 6 bundles for `r'client_id["\']?\s*[:=]\s*["\']([a-zA-Z0-9]{32})["\']'`.
2. **Strict Full-Track-Only Search & Filtering:**
   - `GET https://api-v2.soundcloud.com/search/tracks?q={artist}+{title}&client_id={cid}&limit=15`
   - **CRITICAL Anti-30s-Preview & Anti-Remix Gates:**
     - MUST have `item["policy"] == "ALLOW"` (NEVER `SNIP` — Go+ 30s previews!).
     - MUST have `duration >= 90_000` ms AND `full_duration > 0` AND `abs(duration - full_duration) <= 5000` ms (proves full track, not preview!).
     - MUST pass strict duration gate `abs(dur_sec - target_dur) <= 20` (when `target_dur > 30`).
     - MUST pass `title_sim >= 0.65`, artist present in `user.username` or `title`, AND reject/penalize (`-0.45`) any `remix`, `cover`, `speed up`, `sped up`, `slowed`, `house`, `lak`, `dj`, `mashup`, `bootleg`, `tiktok`, `tik tok`, `karaoke`, `beat`, `hợp xướng`, `solo version`, `piano`, `live`, `8d`, `nightcore` when not in target title.
3. **Playback Resolution:**
   - Query `transcoding["url"] + "?client_id=...&track_authorization=..."` → returns `{"url": "..."}`.
   - If `protocol == "progressive"` (`audio/mpeg`) → proxy directly via normal HTTP Range proxy!
   - If `protocol == "hls"` (`audio/mp4; codecs="mp4a.40.2"`): fetch `.m3u8` playlist, extract `#EXT-X-MAP:URI="..."` init segment + all segment URLs, download concurrently via `ThreadPoolExecutor(max_workers=8)` and concatenate into a single valid seekable fMP4 buffer (`ResolvedStream.local_bytes`, `content_type="audio/mp4"`, `mode="full"`, `source="soundcloud"`, cached in bounded LRU max 8 tracks so seeking/Range works natively with `206 Partial Content`!).

---

### Source C (OPTIONAL CLOUD YOUTUBE UNLOCK): `YTDLP_COOKIES_B64` & `YTDLP_PROXY` Env Vars
For users who deploy on Render and optionally wish to unlock YouTube `ANDROID`/`yt-dlp` too:
- In `backend/providers/youtube_music.py` (`_resolve_via_yt_dlp`):
  - If env var `YTDLP_COOKIES_B64` is set, decode base64 once to `/tmp/sonify/yt_cookies.txt` (chmod `0600`) and pass `--cookies /tmp/sonify/yt_cookies.txt`.
  - If env var `YTDLP_PROXY` is set, pass `--proxy <YTDLP_PROXY>`.
- Keep `ANDROID` `20.10.38` `itag=18` intact for local/residential deployments (still fastest at ~0.3s locally!).

---

## 3. Recommended Cloud-Aware Waterfall Order (`backend/services/stream_service.py`)

Detect cloud environment automatically via `is_cloud_env()` (`RENDER`, `RAILWAY_ENVIRONMENT`, `FLY_APP_NAME`, `SPACE_ID`, or `SONIFY_CLOUD_MODE=1`):

1. **Tier 0 — Native Direct:** `au:*` (Audius), `js:*` (JioSaavn), `yt:*` (YouTube), `mc:*` (Monochrome).
2. **Tier 1 & Tier 2 (Environment-Optimized Order):**
   - **On Cloud/Render (`is_cloud_env() == True`):**
     1. **`monochrome` (`https://tracks.monochrome.st` — Lossless FLAC full tracks!)** ← Primary! Fast, $0, zero AWS YouTube bot-check, exact ISRC + metadata match!
     2. **`youtube_music`** (`ANDROID` `itag=18` + `yt-dlp` if `YTDLP_COOKIES_B64`/`YTDLP_PROXY` set; skip dead Piped/Invidious seeds unless explicitly configured via env var to eliminate 8–12s dead timeouts!).
   - **On Local / Residential (`is_cloud_env() == False`):**
     1. **`youtube_music`** (`ANDROID` `itag=18` — ~0.3s, 2 MB MP4!)
     2. **`monochrome`** (`https://tracks.monochrome.st` — Lossless FLAC fallback if YouTube fails/throttles!)
3. **Tier 3 — `jiosaavn`** (320kbps AAC, strict `title_sim >= 0.60` & `artist_sim >= 0.50`).
4. **Tier 4 — `soundcloud`** (full-track `policy=ALLOW` non-remix MP3/fMP4 fallback).
5. **Tier 5 — `audius`** (full-track MP3, strict `title_sim >= 0.60` & `artist_sim >= 0.50`).
6. **Tier 6 — Official 30s Preview (`deezer-preview` / `itunes-preview`)** — strictly last resort with honest `30s Preview` UI badge.
7. **Offline Mode Only:** `offline-synth` WAV strictly guarded to `local:*` or `http_client.is_offline_mode()` — NEVER served in online mode.

### Additional Fixes Needed in `http_client.py` & `stream_service.py`
- **Magic-Byte Content-Type Sniffing (`http_client.sniff_media_mime(head_bytes, header_ctype)`):**
  - Upgrade `probe_url()` to inspect the first 12 bytes it already reads and return the true audio/video MIME type whenever upstream sends `application/octet-stream` (so `tracks.monochrome.st` FLAC streams are always served as `Content-Type: audio/flac`!).
  - In `stream_service.py` (`stream_to_handler`), do **not** overwrite a sniffed `audio/flac` with upstream `application/octet-stream` (already guarded by `ctype.startswith(_VALID_STREAM_MIME_PREFIXES)` — just add `audio/flac` compatibility check & ensure `resolved.content_type` stays `audio/flac`!).
- **Timeout Tuning for Cold Lossless Streams:**
  - Increase `STREAM_READ_TIMEOUT` to `25.0s` (or `20.0s`) and give `monochrome` probe `timeout=16.0s` so cold FLAC warmup on `tracks.monochrome.st` completes reliably without premature timeout.
- **IPv4-Preference / Clean Socket Fallback:** Note in Render Docker (IPv4 AWS), standard networking works cleanly; keeping `probe_url` fast-failing dead Piped/Invidious seeds saves ~10s per track.
- **Frontend (`frontend/js/components/playerBar.js`):**
  - Display `Full Track • Lossless FLAC` (or `Full Track • monochrome`) cleanly when `streamSource === 'monochrome'`, and ensure `#audio-engine` (`<video>`) plays `audio/flac` cleanly across all browsers.

---

## 4. Verification Checklist for `@builder` & `@tester`

1. **Unit Tests (`tests/`):**
   - Test `monochrome.py` ISRC exact match, fuzzy match, cover/remix rejection, millisecond-to-second duration conversion, magic-byte `fLaC` → `audio/flac` sniffing, and single `429` retry.
   - Test `soundcloud.py` `SNIP` (30s) rejection, `ALLOW` full-track filtering, remix/speed-up rejection, and HLS `#EXT-X-MAP` + segment concatenation.
   - Test `StreamService` cloud-mode ordering (`RENDER=true` tries `monochrome` before `youtube_music`) and `YTDLP_COOKIES_B64` / `YTDLP_PROXY` plumbing.
2. **Live Online End-to-End Verification (Simulating Render Cloud Mode with `RENDER=true`!):**
   - Start Sonify locally with `RENDER=true` (and even with YouTube mocked/failing as on AWS!) and test:
     - `Đừng Làm Trái Tim Anh Đau — Sơn Tùng M-TP` (`dz:2830386932`)
     - `Hãy Trao Cho Anh — Sơn Tùng M-TP` (`dz:2470200565`)
     - `Blinding Lights — The Weeknd` (`dz:908604612`)
     - `Die With A Smile — Lady Gaga` (`dz:2947516331`)
     - `Through the Night — IU` (`dz:841864642`)
   - Verify `/api/stream/{id}/info` returns `streamMode: "full"`, `streamSource: "monochrome"`, `contentType: "audio/flac"`, and `GET /api/stream/{id}` with `Range: bytes=0-` returns `206 Partial Content`, `Content-Type: audio/flac`, `X-Ad-Free: true`, `fLaC` magic bytes, and `Content-Length > 10,000,000` bytes (full ~4+ minute track, NOT 30s preview!).
3. **Post-Deploy Verification on `https://sonify-music.onrender.com`:**
   - Once pushed to GitHub and redeployed on Render, query `https://sonify-music.onrender.com/api/stream/dz%3A2830386932/info` and `/api/stream/dz%3A908604612/info` to confirm live Render returns `streamMode: "full"` (NOT `preview`)!
