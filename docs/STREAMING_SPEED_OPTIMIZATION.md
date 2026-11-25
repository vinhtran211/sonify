# Sonify Streaming Speed Profiling & Optimization Architecture

**Target**: Live Render (`https://sonify-music.onrender.com`) + Web App (`frontend/`) + Native iOS App (`ios/`)  
**Audit Date**: 2026-11-25  
**User Report**: *"the songs are loading pretty slow. is that cause by network or something else? can you make it better? im pretty sure that my network speed is strong enough."*

---

## 1. Executive Answer: It Is **NOT** the User's Network

Live millisecond profiling against `https://sonify-music.onrender.com` proved that the user's network is **not** the bottleneck (steady-state cached transfer from Render measured `800–1,882 KB/s`). Instead, **six compounding server & streaming architecture bottlenecks** cause cold songs to stall **6 to 35+ seconds** (and Home catalog loads to take **8.3–22.5 seconds**):

| Rank | Bottleneck (Empirically Measured Live) | Current Latency Cost | After Optimization |
|---|---|---|---|
| **#1** | **Monochrome Tail-Range Wait Stall** (`_serve_progressive_monochrome` blocks `handle.cond.wait()` until all 24–82 MB of Hi-Res FLAC downloads whenever Chrome `<video>` / iOS `AVPlayer` sends a tail metadata probe!) | **13.6s – 26.0s – >35s TIMEOUT** | **0.8s – 2.0s** (direct upstream tail Range pass-through + faster 256 KiB producer writes) |
| **#2** | **Serial Cloud Provider Waterfall + Synchronous Deezer ISRC Pre-Fetch** (Monochrome `no_match` burns **2.6–4.2s** serially before SoundCloud even starts; SoundCloud itself resolves in **0.77–1.28s**) | **3.9s – 5.7s** on every SoundCloud track (`+0.5s` on Monochrome `dz:*`) | **0.9s – 1.5s** (parallel Monochrome + SoundCloud + JioSaavn race + drop serial Deezer ISRC call + HTTP Keep-Alive pooling) |
| **#3** | **Un-Prewarmed SoundCloud HLS Assembly & Unnecessary Render Proxy Hop** (iOS `AVPlayer` & Safari can play SoundCloud CloudFront HLS `.m3u8` and JioSaavn/Audius CDNs **directly from local edge**; currently Render downloads all 22–40 segments cold on `bytes=0-1` = `+0.9–1.5s` AND routes audio 12,000 km through Oregon!) | **1.0s – 2.5s** extra cold start + Render 0.1 vCPU hop | **< 0.35s** on iOS/Safari (native `hlsUrl` / `directUrl` edge CDN with automatic proxy fallback); **< 0.5s** on Chrome (pre-warmed single-flight HLS assembly during `/info`) |
| **#4** | **Zero Queue Next-Track Pre-Warming** (Web `player.js` & iOS `AudioPlayerService.swift` wait until track switch to call `/info` and start warming the cache) | **1.3s – 5.7s** on every Next / Auto-Advance | **< 0.25s – 0.4s** (Spotify-like instant Next!) |
| **#5** | **Unshared Home Cache (`global` vs `vn`) + Unbounded Slow Home Future + Cold Start** (`/api/home?region=global` took **8.36s**, switching to `vn` took ANOTHER **8.25s**; Render Free cold boot took **22.5s**) | **8.3s** initial + **8.3s** region switch | **< 1.5s** cold, **< 5ms** region switch, **0ms** stale-while-revalidate + startup pre-warm |
| **#6** | **Render Free Region & Sleep Infrastructure** (Live service is still on Oregon US ~220ms RTT + 15-min idle sleep = 22.5s wake) | **+220ms/RTT** & **22.5s** idle wake | **~35ms RTT** on `region: singapore` + free 14-min health keep-alive ping |

---

## 2. Live Millisecond Profiling Results (`https://sonify-music.onrender.com`)

### 2.1 Health, Home & Search Latency (Live Render)

| Endpoint | HTTP | TTFB | Total | Observation |
|---|---|---|---|---|
| `GET /api/health` (cold wake) | 200 | **22,544 ms** | 22,545 ms | Render Free 15-minute idle spin-down wake |
| `GET /api/home?region=global` | 200 | **8,355 ms** | 8,358 ms | Blocked by slowest of 5 upstream futures (`PROVIDER_TIMEOUT=7.5s`) |
| `GET /api/home?region=vn` | 200 | **8,254 ms** | 8,269 ms | **BUG**: Unshared `_home_cache` refetched all 5 identical providers! |
| `GET /api/search?q=son+tung+mtp` | 200 | 1,022 ms | 1,025 ms | Healthy parallel search |

### 2.2 8-Track End-to-End Cold Stream Breakdown (Including Tail Range Probe!)

Each cold track was profiled with:
1. `GET /api/stream/{id}/info?debug=1`
2. `GET /api/stream/{id}.{ext}` (`Range: bytes=0-1` — iOS `AVPlayer` initial probe)
3. `GET /api/stream/{id}.{ext}` (`Range: bytes=0-262143` — first 256 KiB)
4. `GET /api/stream/{id}.{ext}` (`Range: bytes=TOTAL-65536..TOTAL-1` — Chrome `<video>` / iOS `AVPlayer` tail metadata probe!)
5. `GET /api/stream/{id}.{ext}` (`Range: bytes=262144-1048575` — steady-state 768 KiB throughput)

| # | Track | Source / Size | `/info` (Provider Trace) | `0-1` Probe | Head 256K | **Tail 64K Probe** | 768K Speed |
|---|---|---|---|---|---|---|---|
| 1 | **Dracula (with JENNIE)** | `monochrome` FLAC **23.6 MB** | `1,380 ms` (mc: 1,173ms) | `651 ms` | `2,099 ms` | **`22,305 ms`** (hdr 659ms, body stalled 21.6s!) | `1,129 KB/s` |
| 2 | **Boston** | `monochrome` FLAC **32.1 MB** | `1,959 ms` (mc: 1,744ms) | **`10,040 ms`** | `445 ms` | **`>35,000 ms TIMEOUT`** | — |
| 3 | **Choosin' Texas** | `monochrome` FLAC **24.9 MB** | `1,264 ms` (mc: 1,069ms) | `953 ms` | `463 ms` | **`13,637 ms`** (hdr 679ms, body stalled 13.0s!) | `1,882 KB/s` |
| 4 | **Như Một Người Rưng** | `soundcloud` M4A **2.3 MB** | **`5,683 ms`** (mc miss **4,213ms** -> sc ok **1,279ms**) | `875 ms` | `636 ms` | `409 ms` | `861 KB/s` |
| 5 | **LAVIEM** | `soundcloud` M4A **4.8 MB** | **`5,040 ms`** (mc miss **3,673ms** -> sc ok **1,093ms**) | `1,350 ms` | `655 ms` | `496 ms` | `956 KB/s` |
| 6 | **Come My Way** | `soundcloud` M4A **4.3 MB** | **`5,332 ms`** (mc miss **4,170ms** -> sc ok **974ms**) | `1,482 ms` | `347 ms` | `185 ms` | `1,306 KB/s` |
| 7 | **Hãy Trao Cho Anh** | `monochrome` FLAC **27.4 MB** | `2,760 ms` (mc: 2,596ms) | `1,896 ms` | `574 ms` | **`25,987 ms`** (hdr 830ms, body stalled 25.1s!) | `798 KB/s` |
| 8 | **Cruel Summer** | `monochrome` FLAC **3.8 MB** | `1,263 ms` (mc: 886ms) | **`16,123 ms`** | `330 ms` | `217 ms` | `494 KB/s` |

*(Also confirmed live: `dz:4045817471` `hate that i made you love me — Ariana Grande` resolved `full/soundcloud` in `3,882 ms` = `2,842 ms` Monochrome miss + `853 ms` SoundCloud ok!)*

### 2.3 Direct Upstream Provider Benchmarks (Proving Direct CDN & Tail-Range Feasibility)

| Provider | Measured Metric | Result | Implication |
|---|---|---|---|
| **Monochrome** (`tracks.monochrome.st`) | Head `0-65535` + immediate Tail `bytes=TOTAL-65536..TOTAL-1` | Both returned **HTTP 206** (no 429!); FLAC only (no `?quality=` param, confirmed in `tracks-api.js`); `Content-Type: application/octet-stream` | Must stay proxied, **AND** small tail/seek Range requests can pass directly through to upstream instead of waiting 13–35s for 25–82 MB sequential download! |
| **SoundCloud HLS** (`playback.media-streaming.soundcloud.cloud`) | `m3u8` = `397ms`, first 2 segments = `350ms`, ALL 22 segments = `1,290ms` (`4.1 MB`); CloudFront `Policy` has **NO IP restriction** (`DateLessThan` only!) | **100% IP-agnostic CloudFront edge HLS!** | iOS `AVPlayer` & Safari can play `hlsUrl` **directly from CloudFront edge in `< 0.35s`**! Chrome gets pre-warmed server assembly in `< 0.5s`! |
| **JioSaavn** (`aac.saavncdn.com`) | Direct CDN `0-65535` = **`217 ms`** (320kbps M4A, IP-agnostic) | Fast direct HTTPS CDN | Expose `directUrl` for instant edge playback on iOS + Web! |

---

## 3. Concrete Implementation Plan (Ranked by Latency Savings)

### Speedup 1 (P0 — Saves 13s to 35s+): Fix Tail-Range Stall & Producer Overhead in Monochrome

**Files**: `backend/services/stream_service.py`, `backend/providers/monochrome.py`

1. **Direct Upstream Pass-Through for Tail / Far-Seek Small Ranges (`stream_service._serve_progressive_monochrome`)**:
   - After `byte_range, ok = parse_byte_range(range_header, known_total)` succeeds (and `not head_only`, `not handle.is_complete`):
   - Define `TAIL_PASSTHROUGH_GAP = 512 * 1024` (512 KiB) and `TAIL_PASSTHROUGH_MAX_LEN = 2 * 1024 * 1024` (2 MiB).
   - Check `if byte_range is not None:`
     - `start, end = byte_range`
     - `length = end - start + 1`
     - If `start > handle.bytes_written + TAIL_PASSTHROUGH_GAP` and `length <= TAIL_PASSTHROUGH_MAX_LEN`:
       - **Before sending response headers**, attempt a fast direct upstream Range request to `monochrome.get_stream_url(mc_id)` with `range_header=f"bytes={start}-{end}"` and `timeout=8.0` (do **NOT** call `_pace_upstream_request()`, and do **NOT** cancel the background sequential producer!).
       - If upstream returns `206` (and valid bytes): send `206 Partial Content`, `Content-Range: bytes {start}-{end}/{known_total}`, `Content-Length: {length}`, stream those bytes directly to `handler.wfile`, and return `True`!
       - If upstream returns `429`, non-206, or times out: fall back cleanly to the existing sequential `handle.cond.wait()` loop so playback never fails!
   - **Result**: Chrome `<video>` and iOS `AVPlayer` tail metadata probes complete in **`~0.8–2.5s`** instead of stalling **`13.6s–35s+`** waiting for 25–82 MB to finish downloading!

2. **Faster Background Producer Throughput (`backend/providers/monochrome.py`)**:
   - In `_run_producer()`:
     - Read `65536` bytes for the first chunk (so `READY_THRESHOLD_BYTES = 65536` triggers immediately and `out_f.flush()` runs once), then increase the loop read size to **`262144` (256 KiB)** and flush every `524288` bytes (512 KiB) instead of flushing on every 64 KiB read!
     - Reduce `_MIN_UPSTREAM_GAP_SEC` from `1.5` to `0.6` seconds (and keep `429` retry backoffs `(1.0, 2.0, 3.5)` instead of `(1.5, 3.0, 4.5)` so recovery from a transient 429 is 3s faster).
     - Lower `search_tracks()` timeout from `10.0s` to `5.0s` and cap `find_best_stream()` to at most **2** search queries (primary + 1 fallback only if distinct).

---

### Speedup 2 (P0 — Saves 2.6s to 4.2s on Cold `/info`): Parallel Provider Race + Drop Serial Deezer ISRC Call + Keep-Alive HTTP Pooling

**Files**: `backend/services/stream_service.py`, `backend/http_client.py`

1. **Drop Synchronous `deezer.get_track(raw_id)` Inside `_try_resolve_monochrome()`**:
   - In `backend/services/stream_service.py` (lines ~268–275), remove the blocking `deezer.get_track(raw_id)` call during Monochrome resolution. Use `extra.get("isrc")` when already present in SQLite; otherwise go straight to title+artist matching (saves `400–600ms` per cold `dz:*` track with zero accuracy loss).

2. **Parallel Cloud Provider Race (`ThreadPoolExecutor`) in `_resolve_stream_unlocked()`**:
   - When `config.is_cloud_env()` is `True` (and track is not already resolved by Tier 0 or cached `monochromeTrackId` or YouTube with credentials):
     - Submit **Monochrome**, **SoundCloud**, and **JioSaavn** lookups **CONCURRENTLY** in a `ThreadPoolExecutor(max_workers=3)`!
     - **Studio-Master Priority With Zero Wasted Wait**:
       1. Wait for Monochrome (`f_mc`) FIRST with a bounded budget (e.g. `timeout=2.8s` — note Monochrome hits succeed in `0.8–1.7s`!).
       2. If Monochrome succeeds -> return Monochrome immediately (studio master FLAC preserved!).
       3. If Monochrome returns `None` (`no_match`) or exceeds `2.8s` -> check SoundCloud (`f_sc`), which has **ALREADY been running in parallel** and is already finished (`0.77–1.28s`) -> return SoundCloud **INSTANTLY (`0ms` additional wait!)**!
       4. If SoundCloud also returned `None` -> check JioSaavn (`f_js`, also already finished in parallel!) -> then Audius -> then Preview.
       - Never wait for slow losers (`pool.shutdown(wait=False, cancel_futures=True)`).
     - Record accurate per-provider `diagnostics` (`provider`, `outcome`, `ms`, `parallel: true`).
   - **Impact**: Cold `/info` for SoundCloud tracks drops from **`5,040–5,683 ms`** down to **`~1,000–1,600 ms`** (a **3.5x–5x speedup!**).

3. **Persistent HTTPS Keep-Alive Connection Pooling (`backend/http_client.py`)**:
   - Add an optional thread-safe `requests.Session` (when `requests` is importable — installed automatically in Docker via `yt-dlp` and on standard Python dev setups, with zero-install stdlib `urllib.request` fallback preserved!):
     - Mount `HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)`.
     - Use the pooled session in `fetch_bytes()` (eliminates repeated TCP+TLS 1.3 handshakes across Monochrome search, SoundCloud search + transcoding + all 22 HLS segments, Deezer, iTunes, LRCLIB!).
     - Keep `open_stream()` compatible with existing tests & urllib mocks (note: check if existing tests monkeypatch `urllib.request.urlopen` or `http_client.fetch_bytes`/`open_stream` — keep `http_client.fetch_bytes` clean and easily mockable, or allow `SONIFY_USE_POOLED_HTTP=0` / test compatibility!).

---

### Speedup 3 (P0 — Saves 1.0s to 2.5s + Edge CDN Speed): Direct CDN / Native HLS Fast-Path + SoundCloud Background Pre-Warm

**Files**: `backend/providers/soundcloud.py`, `backend/services/stream_service.py`, `ios/Sonify/Models/StreamAndLyricsModels.swift`, `ios/Sonify/Services/AudioPlayerService.swift`, `frontend/js/player.js`

1. **Single-Flight Background SoundCloud HLS Pre-Warm (`backend/providers/soundcloud.py` + `stream_service.py`)**:
   - Add per-`cache_key` single-flight locking + `start_background_hls_assembly(m3u8_url, cache_key, mime_hint)` in `soundcloud.py` (daemon thread, non-blocking, increases `ThreadPoolExecutor` workers from 12 -> 16 for HLS segments).
   - Call `soundcloud.start_background_hls_assembly(...)` inside `stream_service.get_stream_info()` whenever a SoundCloud `hls` track is resolved (mirroring Monochrome's pre-warm!).
   - Now when Chrome opens `/api/stream/{id}` ~150ms after `/info`, SoundCloud HLS assembly is ALREADY running/complete instead of blocking cold on `bytes=0-1`!

2. **Expose Optional Direct Edge URLs in `GET /api/stream/{track_id}/info` (`backend/services/stream_service.py`)**:
   - Keep `"streamUrl"` and `"streamUrlWithExt"` 100% unchanged!
   - Add two optional, backwards-compatible fields when safe (strictly **IP-agnostic** HTTPS CDNs only):
     - **`"hlsUrl"`**: set to `resolved.headers["X-SC-M3U8"]` when `resolved.source == "soundcloud"` and `X-SC-M3U8` starts with `https://`. (CloudFront policy has NO IP lock!)
     - **`"directUrl"`**: set to `resolved.url` ONLY when `resolved.source in ("jiosaavn", "audius", "deezer-preview", "itunes-preview")` OR (`resolved.source == "soundcloud"` and not HLS) and `resolved.url.startswith("https://")`.
     - **NEVER** set `directUrl` for `monochrome` (requires our single-flight proxy to avoid `application/octet-stream` + burst 429s) or YouTube (`googlevideo.com` is IP-bound to server IP!).

3. **iOS `AVPlayer` Native HLS + Direct Edge Fast-Path with Automatic Proxy Fallback (`ios/Sonify/`)**:
   - Add optional `hlsUrl: String?` and `directUrl: String?` to `StreamInfoResponse` (`StreamAndLyricsModels.swift`).
   - Add `AudioPlayerService` URL selection priority:
     1. `info.hlsUrl` (native iOS `AVPlayer` HLS directly from CloudFront edge -> **`< 350ms` start, zero Render hop!** — do NOT pass `AVURLAssetOverrideMIMETypeKey` when loading `.m3u8` HLS so AVPlayer parses the HLS playlist natively!)
     2. `info.directUrl` (direct JioSaavn/Audius/SoundCloud progressive CDN -> **`< 250ms` start!**)
     3. `api.streamURL(for: info)` (`streamUrlWithExt` backend proxy — always used for Monochrome FLAC & local/YouTube!)
   - **Automatic 1-Shot Fallback**: Track `didFallbackToProxy: Bool` per `playbackSeq`. If an `AVPlayerItem` loaded from `hlsUrl` or `directUrl` enters `.failed`, immediately retry ONCE using `api.streamURL(for: info)` (our Render proxy) before surfacing an error!
   - Lower `item.preferredForwardBufferDuration` from `5.0` -> `2.0` seconds for faster cold playback start.

4. **Web `<video>` Fast-Path with Automatic Proxy Fallback (`frontend/js/player.js`)**:
   - For Safari (`audioEl.canPlayType('application/vnd.apple.mpegurl')`) when `info.hlsUrl` is present -> play `info.hlsUrl` directly!
   - When `info.directUrl` is present -> play `info.directUrl` directly in `<video>` (no `crossorigin` attribute -> no CORS preflight needed!).
   - On `audioEl` `error` event, if currently playing a direct/HLS URL and not yet retried for this `playbackSeq`, immediately fall back to `/api/stream/{track.id}?s={seq}` silently!

---

### Speedup 4 (P1 — Makes Next Track `< 0.3s`!): Queue Next-Track Pre-Warming (Web + iOS)

**Files**: `frontend/js/player.js`, `ios/Sonify/Services/AudioPlayerService.swift`

1. **Web (`frontend/js/player.js`)**:
   - Add `scheduleNextTrackPrefetch()` triggered ~2.0 seconds after `playing` fires (never before current track starts playing, so it never competes for bandwidth!).
   - Peek the next track (`st.userQueue[0]` else `st.queue[st.queueIndex + 1]` if not shuffle).
   - Call `api.getStreamInfo(nextTrack.id)` silently in the background (deduplicated via a bounded `prefetchedTrackIds` Set). Because backend `/info` warms both Monochrome `.part/.bin` AND SoundCloud HLS caches, clicking **Next** or auto-advancing starts in **`< 0.3s`**!

2. **iOS (`ios/Sonify/Services/AudioPlayerService.swift`)**:
   - Add an in-memory `prefetchedInfo: [String: (info: StreamInfoResponse, fetchedAt: Date)]` cache (TTL 10 minutes, max 12 entries).
   - In `startPlayback(for: track)`: check `prefetchedInfo[track.id]` first (if fresh, skip the `/info` round-trip completely -> **`0ms` `/info` wait!**).
   - Once `item.status == .readyToPlay`, spawn a low-priority `Task` (after `1.5s` sleep) to peek `queueEngine.upcomingTracks.first` and call `api.getStreamInfo(trackId: next.id)` to warm both the local `prefetchedInfo` cache AND Render's backend audio cache!

---

### Speedup 5 (P1 — Cuts Cold Home from `8.3s x 2` to `< 1.5s` & Region Switch to `< 5ms`!): Shared Home Cache + 3.5s Non-Critical Deadline + Startup Pre-Warm

**Files**: `backend/services/catalog_service.py`, `backend/server.py`

1. **Shared Base Home Snapshot + Stale-While-Revalidate (`backend/services/catalog_service.py`)**:
   - Replace per-region `_home_cache["global"]` / `_home_cache["vn"]` fetching with ONE shared `_home_base_cache: Optional[Tuple[float, Dict[str, Any]]]` containing the base sections, projected cleanly to `global` or `vn` order via `_project_home_region(base_payload, region_key)` in `< 1ms`!
   - Add **stale-while-revalidate**: if `_home_base_cache` is between `HOME_CACHE_TTL` (600s) and `3600s` old (and `refresh=False`), return the cached snapshot **immediately (`< 5ms`)** and spawn a single-flight background thread to refresh `_home_base_cache`!
   - Use `concurrent.futures.wait(futures, timeout=3.5)` on the 5 Home provider futures (collecting whichever providers finished within 3.5s so one sluggish provider like Audius or Apple RSS never blocks `/api/home` for 8.3s!).

2. **Startup Home Pre-Warm (`backend/server.py`)**:
   - In `run_server()` (when not offline mode), launch a daemon thread `threading.Thread(target=lambda: httpd.catalog.get_home("global"), daemon=True).start()` so Render finishes warming `/api/home` right as the container boots!

---

### Speedup 6 (P2 — Infrastructure & Deployment Guidance): Render Singapore + Keep-Alive Ping

Document clearly in `README.md` (and report to the user):
1. **Live Render Region**: `https://sonify-music.onrender.com/api/health` currently reports `renderRegion: "cloud"` (default US Oregon, ~220–360ms RTT from Vietnam). Recreating the Render Web Service in **Singapore** (already configured in `render.yaml` `region: singapore`) cuts Vietnam RTT by **~6x (`~35ms`)** AND removes US SoundCloud Go+ `SNIP` geo-blocks!
2. **Render Free 15-Minute Idle Spin-Down (`22.5s` cold wake)**: Point a free external cron/uptime monitor (e.g., UptimeRobot or cron-job.org) at `GET https://sonify-music.onrender.com/api/health` every 14 minutes to eliminate 22.5s cold container sleeps completely.

---

## 4. Expected Latency Improvements (Before vs. After)

| Scenario | Before (Measured Live) | After (Target) | Primary Drivers |
|---|---|---|---|
| Cold Monochrome FLAC (with tail probe) | **15.0s – 35.0s+ (timeout)** | **1.5s – 3.0s** | Tail Range direct pass-through + 256 KiB producer + drop serial Deezer ISRC |
| Cold SoundCloud Track (iOS `AVPlayer`) | **6.5s – 7.2s** | **1.1s – 1.6s** | Parallel race (`-3.5s`) + direct CloudFront `hlsUrl` (`-1.5s`) |
| Cold SoundCloud Track (Web Chrome) | **6.5s – 7.2s** | **1.5s – 2.2s** | Parallel race (`-3.5s`) + pre-warmed HLS assembly + Keep-Alive pool |
| Next Track / Queue Auto-Advance (Web + iOS) | **1.3s – 5.7s** | **0.15s – 0.4s** | Queue next-track pre-warming + iOS `prefetchedInfo` cache |
| Home Page Cold Load / Global <-> VN Switch | **8.35s / 8.25s** | **< 1.5s / < 5ms** | Shared Home base cache + 3.5s wait cap + startup pre-warm + SWR |

---

## 5. QA & Benchmark Acceptance Checklist (For `@tester`)

1. **Monochrome Tail-Range Fast-Path Unit & Integration Tests**:
   - Mock a slow Monochrome producer stuck at `bytes_written = 131_072` of a `30_000_000`-byte FLAC; assert a tail request `Range: bytes=29934464-29999999` triggers direct upstream Range pass-through and returns `206` in **`< 0.2s`** WITHOUT waiting for the producer to finish!
   - Assert if the direct tail pass-through returns HTTP `429`, it falls back cleanly to waiting on `handle.cond` and still returns valid `206` bytes when the producer completes.
2. **Parallel Provider Race & Diagnostics Tests**:
   - Under `RENDER=true`, mock Monochrome slow/miss (`0.4s`) + SoundCloud fast (`0.05s`) -> assert wall-clock resolution is `~0.4s` (parallel, NOT `0.45s+` serial) and Monochrome studio master still wins when Monochrome matches within the grace window.
   - Assert `_try_resolve_monochrome()` does NOT make an extra blocking `deezer.get_track()` call when `isrc` is absent.
3. **Direct Edge URLs & SoundCloud Pre-Warm Tests**:
   - Assert `GET /api/stream/{id}/info` returns `hlsUrl` for SoundCloud HLS, `directUrl` for JioSaavn/Audius/SoundCloud-progressive, and **omits** `directUrl`/`hlsUrl` for Monochrome and YouTube!
   - Assert `GET /api/stream/{id}/info` triggers non-blocking single-flight `soundcloud.start_background_hls_assembly(...)`.
4. **Shared Home Cache & SWR Tests**:
   - Assert calling `get_home("global")` then `get_home("vn")` invokes upstream `deezer.get_chart` **only once** (`call_count == 1`) and returns `trending-vn` first for `vn`.
5. **Web & iOS Static/Unit Verification**:
   - Verify `ios/Sonify/Models/StreamAndLyricsModels.swift` decodes `hlsUrl` & `directUrl` cleanly (add decoding test to `ModelDecodingTests.swift` and URL priority + proxy fallback tests to `QueueLogicTests.swift`).
   - Verify `frontend/js/player.js` implements direct/HLS fast-path with automatic proxy fallback AND next-track pre-warming.
   - Run full `python3 -m unittest discover -s tests -v` (all existing 62 + new tests passing) AND run a **live `RENDER=true` before/after millisecond benchmark** verifying cold Monochrome tail probe `< 3s`, cold SoundCloud `/info` `< 2s`, and `global -> vn` Home switch `< 10ms`!
