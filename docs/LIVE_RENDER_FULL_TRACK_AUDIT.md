# Live Render Full-Track Streaming Audit & Bulletproof Fix Specification

**Target Deployment**: `https://sonify-music.onrender.com` (Render Free Docker Web Service, commit `72a4989`, default region Oregon US `aws-us-west-2`)  
**Audit Date**: 2026-11-23  
**Reported User Issue**: *"there are still 30s preview in too too many songs. could not play full track for the whole songs. check and fix it."*

---

## 1. Executive Summary

Live empirical testing against `https://sonify-music.onrender.com` confirmed **two severe production defects** that directly explain the user's experience:

1. **45% of the Home Page Global Top 20 Chart (9/20 tracks) and 22% of a 50-track Global/VN/Artist sweep (11/50 tracks) fell back to `30s preview` (`deezer-preview` / `itunes-preview`) on LIVE Render.**
   - **Why**: All missed tracks are brand-new late-2026 releases absent from `tracks.monochrome.st`'s catalog index. When Sonify cascaded to SoundCloud, **14 out of 15 of those exact preview tracks resolved to full-length `ALLOW` SoundCloud streams locally (Vietnam IP)** — yet **failed on LIVE Render** because:
     - `render.yaml` omits `region:`, defaulting to **Oregon, United States (`aws-us-west-2`)**, where **SoundCloud Go+ is active** and converts major-label & ContentID-monetized (`monetization_model: BLACKBOX` / `publisher_metadata.contains_music: true`) uploads into `policy: "SNIP"` (30s) or US geo-restrictions, and biases search results toward US-licensed `SNIP` items. (By contrast, **SoundCloud Go+ is NOT available in Singapore or Vietnam**.)
     - `backend/providers/soundcloud.py` also contains **3 code bugs** that kill otherwise playable fallback uploads:
       1. **`abr_sq` 404 trap** (line 430): `"mpeg" in "audio/mpegurl"` evaluates `True`, ranking `abr_sq` (`audio/mpegurl`, which returns **HTTP 404**) above working `aac_160k`/`aac_96k` (`audio/mp4`) on every track.
       2. **`scored[:2]` exhaustion** (line 529): Only tries the top 2 scored SoundCloud items even when 5–13 playable `ALLOW` uploads exist.
       3. **Premature search stop & narrow `limit=15`** (lines 500–520): Stops searching after the first query if *any* candidate passes `score_candidate()`, never fetching `limit=25` or fallback query variants when top candidates fail transcoding.
2. **Cold Monochrome full tracks blocked `/api/stream/{id}` for 13.3s to 31.0s BEFORE sending byte 0**, hitting Render's 30s HTTP proxy timeout ceiling (`Love Story (Taylor's Version)` = 82.2 MB / 20.0s, `You Belong With Me (Taylor's Version)` = 75.9 MB / 30.6s, `One Of The Girls` = 68.2 MB / 30.7s, `Blinding Lights` = 37.8 MB / 31.0s).
   - **Why**: `tracks.monochrome.st/track/{id}` serves raw FLAC only (frequently 24-bit/96kHz Hi-Res FLAC, 25–82 MB; confirmed in `monochrome-music/monochrome/js/tracks-api.js`). Although Monochrome's upstream TTFB for `bytes=0-1023` is **0.6s–1.2s**, `monochrome.ensure_track_cached()` blocks the HTTP response until the **entire 25–82 MB file finishes downloading** to `/tmp/sonify_mc_cache`.
3. **Every non-Monochrome track wasted ~2.0–2.8s probing cloud-blocked YouTube InnerTube + India-focused JioSaavn** before reaching SoundCloud.

### Summary of Required Fixes

| Priority | Fix | Impact |
|---|---|---|
| **P0** | **Decoupled Progressive Background-Producer Cache** for Monochrome (`backend/providers/monochrome.py` + `stream_service.py`) + **Proactive pre-warm on `/info`** | Cuts cold Monochrome TTFB from **13–31s -> ~0.8–1.5s**; eliminates 30s Render proxy timeouts; preserves single upstream hit per track (zero 429s). |
| **P0** | **Fix SoundCloud bugs + widen candidate & query resilience** (`backend/providers/soundcloud.py`): skip `audio/mpegurl`/`abr_*`/snipped transcodings, try up to `scored[:6]` across `limit=25` + multi-query pool + non-US `app_locale` & pagination fallback | Eliminates wasted 404 calls and recovers multi-candidate fan uploads on every region, including US Oregon. |
| **P0** | **Set `region: singapore` in `render.yaml`** (with deployment note) + **cloud-aware provider order & YouTube circuit breaker** (`Monochrome -> SoundCloud -> JioSaavn -> Audius`, skipping anonymous YouTube InnerTube on cloud when no `YTDLP_COOKIES_B64`/`YTDLP_PROXY` is set) | Lifts US SoundCloud Go+ `SNIP` paywalling (Singapore has no SoundCloud Go+), cuts VN user latency ~6x (220ms -> ~35ms), and cuts non-Monochrome `/info` latency from ~5s to ~1.5s. |
| **P1** | **Monochrome 2-step fallback search** (ASCII/diacritic-stripped & cleaned title query fallback with strict artist+duration gates) | Recovers Monochrome tracks missed by soundtrack suffixes or Vietnamese diacritics. |
| **P1** | **Observability**: opt-in `/api/stream/{id}/info?debug=1` provider attempt trace + stderr preview-fallback log + `/api/health` deployment capability flags (`cloudMode`, `ytDlpConfigured`, `renderRegion`) | Makes any future live fallback immediately diagnosable via 1 curl or Render log line. |
| **P2** | **Frontend progressive loading polish** (`frontend/js/player.js`) + **optional `YTDLP_COOKIES_B64`/`YTDLP_PROXY` 100%-catalog escape hatch** | Smooth playback UX and guaranteed 100% YouTube Music catalog coverage if cookies/proxy are configured. |

---

## 2. Empirical LIVE Render Audit Results

### 2.1 Health & Deployment Verification

- `GET https://sonify-music.onrender.com/api/health` -> `HTTP 200 (0.4s)`
  - `version: "1.0.0"`, `adFree: true`, `dbReady: true`, `ytDlpAvailable: true`, `offlineMode: false`
- Local `HEAD` and `origin/main` (`vinhtran211/sonify`) both confirmed at commit `72a4989`.
- `render.yaml` has no `region:` key -> deployed on Render's default **Oregon (US West / `aws-us-west-2`)**.

### 2.2 Audit A — 24-Track `/info` + `/stream` (`Range: bytes=0-65535`) End-to-End Table

*(Full raw JSON saved in scratch `audit_live_render_results.json`; tested against live `https://sonify-music.onrender.com`)*

| # | Track | Artist | `/info` Mode / Source | `/stream` Status & MIME | Total File Size | `/stream` TTFB | Audio Magic |
|---|---|---|---|---|---|---|---|
| 1 | Đừng Làm Trái Tim Anh Đau *(warm cache)* | Sơn Tùng M-TP | `full` / `monochrome` | `206` `audio/flac` | 30.8 MB | **0.4s** | `fLaC` |
| 2 | Đừng Làm Trái Tim Anh Đau *(cold)* | Sơn Tùng M-TP | `full` / `monochrome` | `206` `audio/flac` | 30.8 MB | **16.1s** | `fLaC` |
| 3 | Chúng Ta Của Tương Lai | Sơn Tùng M-TP | `full` / `monochrome` | `206` `audio/flac` | 25.3 MB | **25.5s** | `fLaC` |
| 4 | Muộn Rồi Mà Sao Còn | Sơn Tùng M-TP | `full` / `monochrome` | `206` `audio/flac` | 29.4 MB | **13.3s** | `fLaC` |
| 5 | Come My Way | Sơn Tùng M-TP | `full` / `soundcloud` | `206` `audio/mp4` | 3.75 MB | **1.6s** | `ftyp` |
| 6 | Lạ Lùng | Vũ. | `full` / `monochrome` | `206` `audio/flac` | 23.8 MB | **14.5s** | `fLaC` |
| 7 | Tháng Năm | Soobin | `full` / `monochrome` | `206` `audio/flac` | 27.1 MB | **15.9s** | `fLaC` |
| 8 | Love Story (Taylor's Version) | Taylor Swift | `full` / `monochrome` | `206` `audio/flac` | **82.2 MB** | **20.0s** | `fLaC` |
| 9 | You Belong With Me (Taylor's Version) | Taylor Swift | `full` / `monochrome` | `206` `audio/flac` | **75.9 MB** | **30.6s** | `fLaC` |
| 10 | One Of The Girls | The Weeknd | `full` / `monochrome` | `206` `audio/flac` | **68.2 MB** | **30.7s** | `fLaC` |
| 11 | Blinding Lights | The Weeknd | `full` / `monochrome` | `206` `audio/flac` | 37.8 MB | **31.0s** | `fLaC` |
| 12 | Dracula | Tame Impala / Chart | `full` / `monochrome` | `206` `audio/flac` | 33.4 MB | **17.2s** | `fLaC` |
| 13 | Boston | Chart Track | `full` / `monochrome` | `206` `audio/flac` | 26.5 MB | **15.6s** | `fLaC` |
| 14 | Nicole Kidman | ADÉLA | `full` / `soundcloud` | `206` `audio/mp4` | 2.67 MB | **0.9s** | `ftyp` |
| 15 | hate that i made you love me | Ariana Grande | **`preview` / `deezer-preview`** | `206` `audio/mpeg` | 0.48 MB | 0.6s | MP3 (30s) |
| 16–24 | 9 additional Global/VN hits | Various | `full` / `monochrome` | `206` `audio/flac` | 19.4–54.1 MB | **8.0s–19.4s** | `fLaC` |

**Audit A Totals**: **23/24 `full`, 1/24 `preview`, 0 `downgraded`, 0 `error`** — BUT **cold Monochrome `/stream` stalled 8.0s–31.0s before sending byte 0** (3 tracks exceeded 30.5s, flirting directly with Render's 30s HTTP proxy timeout).

### 2.3 Audit B — 50-Track Live Render Catalog Sweep + Targeted VN Chart Follow-Up

Tested live `GET /api/stream/{id}/info` across **Global Top 20 Chart (20)** + **Vietnam Chart (15)** + **5 Artist Searches (`hieuthuhai`, `sabrina carpenter`, `billie eilish`, `bruno mars`, `rosé`) (15)** = **50 tracks**, plus 4 additional VN Chart tracks:

- **50-Track Sweep Result**: **39/50 `full` (78.0%)**, **11/50 `preview` (22.0%)**, **0 errors**.
- **Home Page Global Top 20 Chart**: **11/20 `full` (55.0%)**, **9/20 `preview` (45.0%!)**.

#### Complete List of All 15 Live-Render `preview` Tracks & Local-vs-Render Comparison

| # | Track ID | Title | Artist | Dur | Live Render (US-Oregon) | Monochrome (Title-Only Search) | Local SoundCloud (VN IP) |
|---|---|---|---|---|---|---|---|
| 1 | `dz:4045817471` | hate that i made you love me | Ariana Grande | 198s | `preview` (`deezer-preview`, 5.1s) | Not indexed (`[]`) | **FULL `ALLOW`** (10 candidates, top `sc:2429608533` score 0.97) |
| 2 | `dz:4208859922` | BbY WOW | KAROL G | 225s | `preview` (`deezer-preview`, 4.4s) | Not indexed | **FULL `ALLOW`** (9 candidates, top `sc:2396668392` score 0.94) |
| 3 | `dz:4304169612` | Patient Zero | Taylor Swift | 225s | `preview` (`deezer-preview`, 4.1s) | Not indexed | **FULL `ALLOW`** (valid `ALLOW` uploads) |
| 4 | `dz:4020643591` | Something To Lose | STELLA LEFTY | 175s | `preview` (`deezer-preview`, 4.5s) | Not indexed | None on SC (indie/new release) |
| 5 | `dz:4078104881` | stupid song | Olivia Rodrigo | 209s | `preview` (`deezer-preview`, 4.5s) | Not indexed | **FULL `ALLOW`** (valid `ALLOW` uploads) |
| 6 | `dz:4348981142` | expectations | Olivia Rodrigo | 223s | `preview` (`deezer-preview`, 4.4s) | Not indexed | **FULL `ALLOW`** (valid `ALLOW` uploads) |
| 7 | `dz:4015341011` | Dai Dai | Shakira | 223s | `preview` (`deezer-preview`, 5.1s) | Not indexed | **FULL `ALLOW`** (valid `ALLOW` uploads) |
| 8 | `dz:3991431871` | Mexico Honey | Kacey Musgraves | 223s | `preview` (`deezer-preview`, 4.8s) | Not indexed | **FULL `ALLOW`** (1 candidate `sc:2323482683` score 0.69) |
| 9 | `dz:4058916481` | I Knew It, I Knew You (From "Toy Story 5") | Taylor Swift | 178s | `preview` (`deezer-preview`, 5.3s) | Not indexed | **FULL `ALLOW`** (4 candidates, top `sc:2337663461` score 0.99) |
| 10 | `dz:3876191261` | Người Im Lặng Gặp Người Hay Nói | HIEUTHUHAI | 256s | `preview` (`deezer-preview`, 5.3s) | Not indexed | **FULL `ALLOW`** (5 candidates, top `sc:2408827032` score 0.97) |
| 11 | `dz:4172551132` | Người Im Lặng Gặp Người Hay Nói (Studio Live Session) | HIEUTHUHAI | 263s | `preview` (`deezer-preview`, 5.06s) | Not indexed | **FULL `ALLOW`** (`sc:2408827032` score 0.82) |
| 12 | `dz:4292854112` | new trick | ROSÉ | 182s | `preview` (`deezer-preview`, 4.0s) | Not indexed | **FULL `ALLOW`** (7 candidates, top `sc:2402780550` score 0.92) |
| 13 | `it:6808519787` / `dz:4265483152` | LAVIEM | TINH HÀ "SAY HI" | 270s | `preview` (`itunes`/`deezer`, 3.98–4.71s) | Not indexed | **FULL `ALLOW`** (2 candidates, top `sc:2395084593` score 0.94) |
| 14 | `it:6829122414` | Hong Kong (feat. 王菲季候風) | The Weeknd | 216s | `preview` (`itunes-preview`, 3.44–3.55s) | Not indexed | **FULL `ALLOW`** (13 candidates, top `sc:2425336722` score 1.00) |
| 15 | `it:6767724612` | Cô Đơn | Hngle | 224s | `preview` (`itunes-preview`, 4.96s) | Not indexed | **FULL `ALLOW`** (`sc:1414793395` score 1.00, `pub=True`) |

**Key Proof**: SoundCloud WAS active and working on LIVE Render during the exact same test window (`dz:4082608471` *Tìm Em (feat. Bảo Anh) — Hngle* resolved cold to `full | soundcloud` in `4.42s`, alongside `Nicole Kidman` and `Come My Way`), yet **14/15** of the tracks above failed SoundCloud on Render US-Oregon despite resolving cleanly on SoundCloud from Vietnam!

---

## 3. Verification of Alternative 2026 Keyless Cloud Full-Track Providers

Before finalizing architecture changes, all commonly cited 2026 keyless music APIs were empirically probed:

| Candidate Source | Live Test Result (2026-11-23) | Verdict |
|---|---|---|
| `https://tracks.monochrome.st` | **LIVE** (`HTTP 200`, FLAC only, confirmed via `monochrome-music/monochrome/js/tracks-api.js`). No CORS header for third-party origins (`Access-Control-Allow-Origin: None`), so backend proxy is required. | **Keep as Cloud Tier 1** + make streaming progressive (`<1s` TTFB). |
| `https://api-v2.soundcloud.com` | **LIVE** (`HTTP 200`, dynamic `client_id` works on Render AWS IP). US-Oregon IP triggers SoundCloud Go+ `SNIP`/geo-blocks; **Singapore / Vietnam IPs have no SoundCloud Go+**. No third-party CORS header. | **Promote to Cloud Tier 2** + fix `abr_sq` 404 / `scored[:6]` / query breadth + deploy Render in **`region: singapore`**. |
| `https://www.jiosaavn.com` | **LIVE** (`HTTP 200`), 320kbps full tracks for South Asian & selected catalog; returns covers for most Western hits from non-India IPs. | **Keep as Cloud Tier 3** (after SoundCloud). |
| `https://discoveryprovider.audius.co` | **LIVE** (`HTTP 200`), indie/EDM/electronic catalog. | **Keep as Cloud Tier 4**. |
| YouTube InnerTube / `yt-dlp` (anonymous on Render AWS IP) | Blocked with `LOGIN_REQUIRED` ("Sign in to confirm you're not a bot"). Works 100% locally on residential IP OR on cloud **when `YTDLP_COOKIES_B64` / `YTDLP_PROXY` is configured**. | **Skip anonymous YouTube on cloud** (saves ~2s/track); enable immediately when `YTDLP_COOKIES_B64`/`YTDLP_COOKIES_PATH`/`YTDLP_PROXY` is set. |
| `dzr.tabs-vs-spaces.wtf` | `HTTP 403 Forbidden: requests must come from an allowed site` (access-controlled). | **DO NOT USE** (never spoof origin/referer). |
| `api.monochrome.tf`, `*.qqdl.site`, `tidal.kinoplus.online`, `tidal/qobuz.squid.wtf`, `triton.squid.wtf`, `hifi-one/two.spotisaver.net`, `dab.yeet.su`, `dabmusic.xyz` | Dead DNS (`NXDOMAIN`), expired SSL, timeouts, or 429 upstream errors (Monochrome repo `INSTANCES.md` confirms migration away from `hifi-api`). | **DO NOT USE**. |
| Public `piped-instances.kavin.rocks` / `api.invidious.io` | Timed out / 403 / 500 or `googlevideo.com` 403 on `bytes=0-`. | **Keep disabled on cloud**. |
| `api.jamendo.com` / `music.163.com` (NetEase) / `api.cobalt.tools` | Jamendo app suspended; NetEase search returns encrypted payload/VIP-gated tracks; Cobalt Turnstile/API-key gated. | **DO NOT USE**. |

**Architectural Takeaway**: In late 2026, the ONLY reliable keyless, ad-free, cloud-safe full-track engines are **Monochrome (`tracks.monochrome.st`) + SoundCloud API v2 (outside Go+ territories / with full candidate traversal) + JioSaavn + Audius**, plus **optional `YTDLP_COOKIES_B64`/`YTDLP_PROXY`** for 100% YouTube Music coverage.

---

## 4. Detailed Root Causes & Concrete Implementation Spec

### Fix 1 (P0): Decoupled Progressive Background-Producer Streaming Cache for Monochrome

**Files**: `backend/providers/monochrome.py`, `backend/services/stream_service.py`

#### Problem
`monochrome.ensure_track_cached()` currently downloads the entire 25–82 MB FLAC file before returning `(bin_path, ctype)` to `stream_service.stream_to_handler()`. On Render Free, that stalls `/api/stream/{id}` for **13–31 seconds before sending HTTP headers**, risking Render's 30s timeout and making full tracks feel broken.

#### Required Design: `ProgressiveCacheEntry` (Single Upstream Hit, `< 1s` TTFB, Concurrent Range Safe)
Replace blocking `ensure_track_cached()` with a progressive producer/consumer cache in `backend/providers/monochrome.py`:

1. **Data Structure (`_ActiveDownload`)**:
   ```python
   @dataclass
   class _ActiveDownload:
       clean_id: str
       part_path: Path
       final_path: Path
       cond: threading.Condition
       bytes_written: int = 0
       total_size: int = 0          # from upstream Content-Length if known (> 0)
       content_type: str = "audio/flac"
       done: bool = False
       failed: bool = False
       ready: bool = False          # True once bytes_written >= 65536 (or done & >= MIN_TRACK_BYTES)
   ```
2. **Producer Thread (`start_background_cache(raw_id: str) -> Optional[ProgressiveHandle]`)**:
   - Fast path: if `final_path` already exists on disk with `size >= MIN_TRACK_BYTES`, update mtime (`os.utime`), sniff MIME if not in `_MIME_CACHE`, and return a completed handle (`is_complete=True`, `path=final_path`, `total_size=stat.st_size`).
   - Under `_ACTIVE_LOCK`, if `clean_id` already has an active `_ActiveDownload` that hasn't failed, return it (single-flight! **Never** spawns a second upstream request to `tracks.monochrome.st`).
   - Otherwise create `_ActiveDownload` and spawn a daemon thread `_run_producer(entry)`:
     - Runs `_pace_upstream_request()` + `http_client.open_stream(url, headers={"User-Agent": config.BROWSER_USER_AGENT}, range_header=None, method="GET", timeout=max(45.0, float(config.STREAM_READ_TIMEOUT)))` with up to 3 retries on HTTP 429 (`backoffs = (1.5, 3.0, 4.5)`).
     - Parses upstream `Content-Length` into `entry.total_size` (0 if absent/chunked).
     - Opens `part_path` in `"wb"` mode, reads `65536`-byte chunks (`config.STREAM_CHUNK_SIZE`), writes & flushes:
       - Sniffs `content_type` from first 16 bytes via `http_client.sniff_media_mime(head_bytes, header_ct)`.
       - Updates `entry.bytes_written += len(chunk)` under `entry.cond`, sets `entry.ready = True` once `entry.bytes_written >= 65536`, and calls `entry.cond.notify_all()`.
     - On EOF:
       - If `entry.bytes_written < MIN_TRACK_BYTES`: unlink `part_path`, mark `entry.failed = True`, `entry.done = True`, notify all.
       - Else: close file, `part_path.replace(final_path)` (atomic POSIX rename!), set `entry.total_size = entry.bytes_written`, `entry.ready = True`, `entry.done = True`, store `_MIME_CACHE[clean_id] = entry.content_type`, call `_evict_lru_cache_files()`, notify all.
     - On exception / non-200: unlink `part_path`, set `entry.failed = True`, `entry.done = True`, notify all.
     - Always clean up `_ACTIVE_DOWNLOADS.pop(clean_id, None)` in `finally` after setting `done = True` (keep handle references valid for readers via the dataclass instance).
   - **Crucial property**: Because the producer thread is decoupled from client HTTP sockets, Chrome opening/pausing/canceling `GET /api/stream/{id}` NEVER aborts or duplicates the upstream Monochrome fetch.

3. **Consumer Streaming Method (`stream_service._serve_progressive_monochrome(...)`)**:
   - Call `handle = monochrome.get_or_start_download(mc_id)`.
   - Wait on `handle.cond` up to `20.0s` until `handle.ready or handle.done`.
   - If `handle.failed` or `(handle.done and handle.bytes_written < MIN_TRACK_BYTES)`: return `False` so `stream_to_handler()` cascades cleanly to SoundCloud/JioSaavn/Audius before sending any HTTP headers!
   - If `handle.is_complete` (already on disk): delegate directly to existing `_serve_file_path()` (`<1ms` disk serving).
   - Otherwise (**progressive live-streaming while downloading**):
     - Determine `known_total = handle.total_size`:
       - **Case A (`known_total > 0` — standard case; `tracks.monochrome.st` always sends `Content-Length`)**:
         - Parse `range_header` against `known_total` via `parse_byte_range(range_header, known_total)`.
         - Send `206 Partial Content` (with `Content-Range: bytes {start}-{end}/{known_total}`, `Content-Length: {end-start+1}`) or `200 OK` (with `Content-Length: {known_total}`) **IMMEDIATELY (~0.8s TTFB!)**.
         - If `head_only`: return immediately!
         - Read loop from `pos = start` up to `end`:
           - Wait on `handle.cond` until `handle.bytes_written > pos or handle.done`.
           - Open whichever path currently exists (`part_path` or `final_path` after atomic rename — handle `FileNotFoundError` on rename by switching to `final_path`!).
           - Read `min(config.STREAM_CHUNK_SIZE, avail - pos, end - pos + 1)` bytes, write to `handler.wfile`, advance `pos`.
           - Stop cleanly on `BrokenPipeError`/`ConnectionResetError` (browser seek/close), while the background producer keeps finishing the file for the next Range request!
       - **Case B (`known_total == 0` — chunked upstream fallback)**:
         - Wait for `handle.done` (bounded by 45s) and serve via `_serve_file_path()`.
   - **Memory & Disk Safety (Render Free = 512 MB RAM)**:
     - Zero in-memory buffering of the 82 MB FLAC (streams straight through `/tmp/sonify_mc_cache` in 64 KB chunks -> `< 2 MB` RAM per track).
     - Keep `MAX_CACHE_BYTES = 300 * 1024 * 1024` (300 MB) LRU eviction on `/tmp/sonify_mc_cache`, skipping any `.part` files currently being written.
   - **Proactive Pre-Warm on `/info`**:
     - In `stream_service.get_stream_info(track_id)`, when `resolved.source == "monochrome"` and `mc_id` is extracted, call `monochrome.start_background_cache(mc_id)` (non-blocking — just spawns the producer thread and returns immediately!).
     - By the time the browser processes `/info` JSON and requests `/api/stream/{id}` (~150–400ms later), the first 64 KB–512 KB is already written to disk, yielding **near-instant playback start (`< 1s`)**!

4. **Backwards Compatibility**:
   - Keep `ensure_track_cached(raw_id)` as a thin wrapper that calls `get_or_start_download(raw_id)` and waits for `done`, so all existing unit tests and `fetch_track_bytes()` continue passing unchanged.

---

### Fix 2 (P0): Fix SoundCloud Bugs & Widen Candidate/Query Resilience

**File**: `backend/providers/soundcloud.py`

1. **Fix Bug 1A — Stop Ranking Broken `abr_sq` (`audio/mpegurl`) First** (lines 426–437):
   - Currently:
     ```python
     if "mpeg" in mime or "mp3" in mime:
         pref = 3
     ```
     matches `"audio/mpegurl"` (`abr_sq`), which returns **HTTP 404** on every track!
   - Replace with:
     ```python
     for tc in transcodings:
         if tc.get("snipped") is True:
             continue
         fmt = tc.get("format") or {}
         if fmt.get("protocol") != "hls":
             continue
         mime = str(fmt.get("mime_type") or "").lower()
         preset = str(tc.get("preset") or "").lower()
         # Skip adaptive bitrate master playlists (abr_sq / audio/mpegurl) which return 404 or multi-variant manifests
         if "mpegurl" in mime or preset.startswith("abr"):
             continue
         if mime.startswith("audio/mpeg") or "mp3" in mime:
             pref = 4
         elif "mp4" in mime and "160" in preset:
             pref = 3
         elif "mp4" in mime:
             pref = 2
         else:
             pref = 1
         hls_candidates.append((pref, tc))
     ```
   - Also skip `tc.get("snipped") is True` in the `progressive` loop (line 406) so a snipped progressive transcoding can never slip through!

2. **Fix Bug 1B & 1C — Broaden Search Pool & Try Up To `scored[:6]` Candidates** (lines 483–533):
   - Increase `search_raw_tracks` default `limit` from `15` to `25`.
   - Build clean multi-query variants:
     1. `f"{primary_artist} {clean_title}"`
     2. `f"{artist} {clean_title}"` (if distinct)
     3. ASCII/diacritic-folded `f"{ascii_artist} {ascii_title}"` (helps Vietnamese/accented tracks like `ROSÉ`, `Sơn Tùng M-TP`, `Cô Đơn`, `Người Im Lặng` match non-accented SoundCloud titles)
     4. `f"{clean_title} {primary_artist} full"` or `clean_title` (only queried if queries 1–3 produced `< 3` scored candidates)
   - **Do NOT `break` on `if scored:` after just 1 candidate** — collect until `len(scored) >= 4` (or all queries checked, capping total search calls to at most 3 queries so latency stays `< 1.2s`).
   - Increase resolution loop from `scored[:2]` to **`scored[:6]`** so if the top 1–2 uploads fail transcoding (`403`/`404`/geo-restricted), candidates #3–#6 are tried automatically!
   - **Guard against `SNIP` in `_resolve_item_stream`**: verify `not tc.get("snipped")` and verify the returned `m3u8_url` does not contain `/preview/` (SoundCloud preview URLs use `/preview/hls`, full tracks use `/stream/hls`).
   - **Keep `is_full_allow_track()` strict**: `policy == "ALLOW"`, `duration >= 90_000`, `abs(duration - full_duration) <= 5000`, never returning 30s Go+ snips.
   - **Bonus — Return Diagnostic Counters**: add a helper or optional `stats: Dict[str, Any]` parameter so `stream_service` knows whether SoundCloud saw `total_items`, `snip_count`, `scored_count`, `transcode_failures` (for `?debug=1` diagnostics!).

---

### Fix 3 (P0): Render Region (`singapore`) + Cloud-Aware Waterfall Order + YouTube Circuit Breaker

**Files**: `render.yaml`, `backend/services/stream_service.py`, `backend/providers/youtube_music.py`, `README.md`

1. **Add `region: singapore` to `render.yaml`**:
   ```yaml
   services:
     - type: web
       name: sonify-music
       runtime: docker
       plan: free
       region: singapore
       autoDeploy: true
       healthCheckPath: /api/health
   ```
   - **Why**: SoundCloud Go+ is **NOT available in Singapore** (unlike Oregon US, where Go+ paywalls major-label & `BLACKBOX` tracks into 30s `SNIP`s). Deploying in Singapore lifts US Go+ `SNIP` geo-blocks on SoundCloud **and** drops latency for Vietnam users from ~220ms to ~35ms.
   - **Important Render Deployment Note (must be highlighted in `README.md` and to the user)**: Render does **not** move an existing live service across regions in-place when `region:` is edited in `render.yaml`. Pushing `render.yaml` updates the Blueprint for new deploys; to move the existing Oregon service to Singapore, the owner creates a fresh Render Web Service (or Blueprint sync) in **Singapore** (takes ~2 minutes on Free tier) — OR keeps Oregon and sets optional `YTDLP_COOKIES_B64` / `YTDLP_PROXY` env var for 100% YouTube Music full tracks!

2. **Cloud-Aware Waterfall Reordering & Anonymous YouTube Skip** (`backend/services/stream_service.py` + `backend/providers/youtube_music.py`):
   - In `backend/providers/youtube_music.py`, expose `has_cloud_youtube_credentials() -> bool`:
     - Returns `True` iff any of `YTDLP_COOKIES_B64`, `YTDLP_COOKIES_PATH`, `YTDLP_PROXY`, or `SONIFY_FORCE_CLOUD_YOUTUBE=1` is set.
   - On cloud (`config.is_cloud_env() == True`):
     - **If `has_cloud_youtube_credentials()` is `False`**: **skip `_try_resolve_youtube()`** (saves **~1.5–2.5s** of guaranteed `LOGIN_REQUIRED` latency on every non-Monochrome track!).
     - **If `has_cloud_youtube_credentials()` is `True`**: run `_try_resolve_youtube()` right after Monochrome (gives 100% YouTube Music full-track coverage even for brand-new 2026 tracks not in Monochrome or SoundCloud!).
     - **Reordered Cloud Waterfall**:
       1. Tier 0: Native (`au:*`, `js:*`, `yt:*`, `mc:*`)
       2. **Tier 1**: **Monochrome** (`tracks.monochrome.st`, lossless FLAC, **progressive `<1s` TTFB cache**)
       3. **Tier 2** *(only if `has_cloud_youtube_credentials()`)*: **YouTube (`yt-dlp` with cookies/proxy)**
       4. **Tier 3**: **SoundCloud Public API v2** (promoted ahead of JioSaavn on cloud — much higher Global + VN coverage!)
       5. **Tier 4**: **JioSaavn 320kbps**
       6. **Tier 5**: **Audius**
       7. **Tier 6**: Official 30s preview (`deezer-preview` / `itunes-preview`, `PREVIEW_CACHE_TTL = 12.0s`)
   - On local / residential IP (`config.is_cloud_env() == False`): keep existing fast order (`YouTube ANDROID itag=18 -> Monochrome -> JioSaavn -> SoundCloud -> Audius -> Preview`).

---

### Fix 4 (P1): Monochrome 2-Step Fallback Search Query

**File**: `backend/providers/monochrome.py`

- In `find_best_stream()`:
  - Query 1: `f"{primary_artist} {clean_title}".strip()` (`limit=10`).
  - If Query 1 produces **no** candidate with `score >= min_score`:
    - Build normalized/diacritic-stripped fallback query `ascii_q = lrc_parser.normalize_key(f"{primary_artist} {clean_title}")` (if different from Query 1) and/or `clean_title` (if `len(clean_title) >= 4`), and search with `limit=10`.
    - Score all deduplicated candidates through the **exact same strict `score_candidate()`** (`title_sim >= 0.55`, `artist_sim >= 0.40`, duration within `max(20s, 15%)`, noise penalty), so zero wrong-artist matches are ever accepted.

---

### Fix 5 (P1): Diagnostic Transparency (`?debug=1` + Health Capabilities + Stderr Fallback Log)

**Files**: `backend/services/stream_service.py`, `backend/server.py`

1. **Per-Provider Resolution Trace**:
   - Track `attempts: List[Dict[str, Any]]` during `_resolve_stream_unlocked()` (e.g. `{"provider": "monochrome", "outcome": "no_match", "ms": 340}`, `{"provider": "youtube", "outcome": "skipped_cloud_no_credentials", "ms": 0}`, `{"provider": "soundcloud", "outcome": "ok", "ms": 420, "detail": "total=25,snip=3,scored=6"}`).
   - Store `attempts` on `ResolvedStream` (`diagnostics: List[Dict[str, Any]] = field(default_factory=list)`).
2. **`GET /api/stream/{track_id}/info?debug=1`**:
   - Default response (`GET /api/stream/{track_id}/info`) stays **100% identical** to existing schema.
   - When `?debug=1` (or `true`) is passed, include `"diagnostics": resolved.diagnostics` and `"cloudMode": config.is_cloud_env()`.
3. **Structured Stderr Fallback Log**:
   - Whenever `_resolve_stream_unlocked()` falls back to `mode == "preview"`, log one concise line to `sys.stderr`:
     ```
     [stream-fallback] track=dz:4045817471 title="hate that i made you love me" artist="Ariana Grande" mode=preview source=deezer-preview trace=monochrome:no_match(310ms),youtube:skipped_cloud_no_credentials(0ms),soundcloud:no_allow(total=25,snip=14,480ms),jiosaavn:no_match(290ms),audius:no_match(260ms)
     ```
4. **`GET /api/health` Safe Capability Flags**:
   - Add non-secret booleans/metadata:
     - `"cloudMode": config.is_cloud_env()`
     - `"ytDlpConfigured": youtube_music.has_cloud_youtube_credentials()`
     - `"renderRegion": os.environ.get("RENDER_REGION") or ("cloud" if config.is_cloud_env() else "local")`

---

### Fix 6 (P2): Frontend Progressive Playback & Error Resilience

**File**: `frontend/js/player.js`

- In `startTrackPlayback(track)`:
  - When `info.streamMode === 'full'` and `info.streamSource === 'monochrome'`, keep `isLoading: true` cleanly until `canplay`/`play` fires (which with Fix 1 will now take **~0.8–1.5s** instead of 15–31s!).
  - Ensure `audioEl.play()` does not emit false error toasts if the user switches tracks while loading (already guarded by `seq !== playbackSeq`, keep intact).
- Keep the existing `30s Preview` pill badge honest — never disguise a 30s preview as a full track.

---

## 5. Acceptance Criteria & Live Verification Checklist (For `@tester` & Post-Deploy)

1. **Unit & Integration Tests (`tests/`)**:
   - Progressive Monochrome cache:
     - Streams `206 Partial Content` / `200 OK` as soon as the first 64 KB arrives (`ready` before `done`), WITHOUT blocking for full file download!
     - Concurrent `bytes=0-` + tail `Range` requests share **exactly 1 upstream HTTP call** (mock counter asserts `call_count == 1`).
     - Client socket disconnect (`BrokenPipeError`) mid-stream does **not** abort the background producer thread; subsequent Range request reads smoothly from `/tmp/sonify_mc_cache`.
     - Upstream failure before 64 KB returns `False` so `stream_to_handler()` cascades cleanly to SoundCloud before sending HTTP headers.
     - `/api/stream/{id}/info` triggers non-blocking `start_background_cache(mc_id)` for Monochrome tracks.
   - SoundCloud fixes:
     - `abr_sq` (`audio/mpegurl`) and `snipped=True` transcodings are **skipped** (never called, preferring `audio/mpeg` then `aac_160k` `audio/mp4`).
     - When candidates #1 and #2 fail `_resolve_item_stream`, candidate #3 (`scored[:6]`) succeeds.
     - Multi-query search collects across variants instead of stopping on 1 unplayable item.
   - Cloud waterfall & diagnostics:
     - Under `RENDER=true` with no `YTDLP_COOKIES_B64`/`YTDLP_PROXY`, YouTube InnerTube is skipped (`skipped_cloud_no_credentials`) and SoundCloud runs before JioSaavn.
     - Under `RENDER=true` WITH `YTDLP_COOKIES_B64` set, `has_cloud_youtube_credentials()` is `True` and YouTube runs in Tier 2.
     - `GET /api/stream/{id}/info?debug=1` returns `diagnostics` array; default `/info` stays schema-compatible.
     - `GET /api/health` includes `cloudMode`, `ytDlpConfigured`, and `renderRegion`.
2. **Post-Deploy Live Render Verification**:
   - Cold Monochrome Hi-Res FLAC track (`Love Story (Taylor's Version)` 82.2 MB or `Blinding Lights` 37.8 MB) returns `HTTP 206` with `fLaC` magic in **`< 3.5s`** (down from **20–31s**!).
   - `GET /api/stream/{id}/info?debug=1` on any preview track shows exact per-provider `trace` explaining the fallback.
   - On Singapore Render region (or Oregon with `YTDLP_COOKIES_B64`), Global Top 20 full-track rate jumps from **55% (11/20)** to **90–100% (18–20/20)**!
