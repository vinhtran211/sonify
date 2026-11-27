# Zero-Preview Elimination Audit & 2-Pass Full-Track Architecture

**Target**: Live Render Web/PWA (`https://sonify-music.onrender.com`, commit `cced032`) + Native SwiftUI iOS App (`ios/Sonify/`)  
**Audit Date**: 2026-11-26  
**User Mandate**: *"there are still 30s preview on songs. fix it all"*

---

## 1. Empirical Findings Across All 5 Mandatory Checks

### 1.1 Check #1 — GitHub `main` & Live Render State
- Local `HEAD` == GitHub `vinhtran211/sonify` `main` == **`cced032`** (`2026-11-26T10:01:49Z`).
- `GET https://sonify-music.onrender.com/api/health` -> `HTTP 200 (742ms)`, `cloudMode: true`, `ytDlpConfigured: false`, `renderRegion: "cloud"`.
- Also note: users who installed Sonify on iPhone via the website's **"Install Directly to iPhone" (`.mobileconfig` Web Clip)** run the **Web/PWA frontend against Render**, so server-side & web fixes are just as critical on iPhone as `YouTubeDirectResolver.swift` is for the Native `.ipa`!

### 1.2 Check #2 — Real Human Rapid-Click Burst Test (`14` Songs Fired `0.35s` Apart on Live Render)
Firing 14 overlapping `/api/stream/{id}/info?debug=1` + `/api/stream/{id}` (`Range: bytes=0-65535`) requests `0.35s` apart revealed a **major concurrency bottleneck**:
- Songs `#01–#03` (*Dracula* 23 MB, *Boston* 32.8 MB, *Choosin' Texas* 25.5 MB) each launched an unthrottled full-file Monochrome background download (`12.3s–13.3s`), saturating `tracks.monochrome.st` and Render's shared bandwidth.
- Consequently, **7 of the next 8 rapid clicks (`#07, #08, #09, #10, #11, #12, #14`) hit `monochrome:timeout(2800ms)`!**
- Worse: `get_stream_info()` currently calls `monochrome.start_background_cache(mc_id)` unconditionally on **every** `/info` call — even when a user skips past 10 tracks in 4 seconds, leaving **10 zombie 30–80 MB FLAC downloads** running simultaneously in the background!

### 1.3 Check #3 — 105-Track Uncurated All-Surfaces Audit on Live Render + Short-Clip Leak Audit
Tested **105 unique uncurated tracks** across Home Global, Home VN, 6 Home New Release Albums, 4 Top Artists, 4 Made For You mixes, 2 Track Radios, 6 Genre Hubs (`pop`, `vpop`, `kpop`, `focus`, `acoustic`, `lofi`), and 10 Diverse Searches (`C-Pop`, `J-Pop`, `V-Pop Rap/Indie`, `K-Pop`, `Rock`, `EDM`) at `max_workers=5`:

- **Result**: **`97 / 105` `full` (92.4%)**, **`8 / 105` `preview` (7.6%)**, **`0` short (<60s) catalog tracks marked `full`**.
  - Full sources: `monochrome: 54`, `soundcloud: 39`, `audius: 3`, `bilibili: 1`.
  - Genre Hubs (`pop`, `vpop`, `kpop`, `focus`, `acoustic`, `lofi`): **18 / 18 = 100% `full`**!

#### Exact Root Causes of All 8 Remaining `preview` Tracks:
1. **Self-Inflicted Monochrome Concurrency Timeouts (`8/8` hit `monochrome:timeout(2800ms)`)**:
   - Re-testing #8 (`dz:3011513621` *Jay Chou 周杰伦 — Henry Zhang*) cleanly in isolation resolved to **`full/monochrome` in `1,235ms`** (`mc:157012657948512256`)! It ONLY fell to `deezer-preview` because concurrent zombie Monochrome `.part` downloads starved `search_tracks()`.
2. **SoundCloud DRM-Only Transcodings Crowding Out `scored[:6]` (`it:6803301747` *Tình Cờ — Lope Dope*)**:
   - Inspecting SoundCloud track `2384261229` (*Tình Cờ — Lope Dope*, `policy: ALLOW`, `276s`) proved all 6 of its transcodings use **`protocol: "cbc-encrypted-hls"` / `"ctr-encrypted-hls"`** (DRM-encrypted!). Because `is_full_allow_track()` didn't check for at least one unencrypted `hls`/`progressive` transcoding, DRM-only tracks wasted `scored` slots (`tc_fail=1`).
3. **Obscure Regional Reissues from `deezer.get_new_releases()` (`5/8` previews)**:
   - All 5 album misses (*Follow Me In Death — Tailgunner*, *People of the Moon — Atlantean Kodex*, *What You Want — Annaleigh Ashford*, *Chrysalis Pt. 1 & Pt. 3 — Christopher Franke* from 1994 *Babylon 5, Vol. 2*) came from `deezer.get_new_releases()` (`/editorial/0/releases`), which surfaces niche European reissues ahead of `apple_itunes.get_top_albums("us")` and `deezer.get_chart()["albums"]`.
4. **Pass-1 Strict Gates Rejecting Valid Label/MV/Live/Cover Full Uploads**:
   - Pass 1 rejects real full-length songs uploaded by record labels (`HYBE LABELS`, `SMTOWN`, `1theK`, `Republic`, `M-TP Official`) when `author != artist` and title lacks artist name, or penalizes Official MVs / Live / Acoustic / Remix / Cover versions below `0.60–0.65` and immediately drops to a **30s Deezer/iTunes preview**.
5. **Hidden Short-Clip Leak Gaps Found in Code Audit**:
   - `monochrome.py` (`MIN_TRACK_BYTES = 200_000`, no `cand_dur >= 60` floor when `target_duration == 0`), `jiosaavn.py` (`probe_url` `min_total_bytes=0`, no `cand_dur >= 60` floor), and `audius.py` (`probe_url` `min_total_bytes=0`, no `cand_dur >= 60` floor) could theoretically accept a 29–30s ringtone/teaser as `mode="full"`.
   - Client-side `PREFETCH_INFO_TTL_MS = 10 * 60 * 1000` (10 min) in `frontend/js/player.js` and `AudioPlayerService.swift` exceeds SoundCloud CloudFront's ~4-minute signed `hlsUrl` lifetime!

### 1.4 Check #5 — Live `YouTubeDirectResolver.swift` Audit Against Real `youtubei` Endpoints
Executed an exact 1:1 port of `ios/Sonify/Services/YouTubeDirectResolver.swift` against live `music.youtube.com/youtubei/v1/search` + `www.youtube.com/youtubei/v1/player`:
- **5 / 5 real songs passed end-to-end with `HTTP 206` playable `itag=18` MP4**:
  1. *Blinding Lights — The Weeknd* -> `J7p4bzqLvCw` (`202s`, `HTTP 206`, `1.78 MB`)
  2. *Đừng Làm Trái Tim Anh Đau — Sơn Tùng M-TP* -> `7u4g483WTzw` (`280s`, `HTTP 206`, `2.34 MB`)
  3. *APT. — ROSÉ* -> `DiTd771WumE` (`170s`, `HTTP 206`, `3.14 MB`)
  4. *Die With A Smile — Lady Gaga* -> `DlFXDl_ROAM` (`252s`, `HTTP 206`, `1.94 MB`)
  5. *Through the Night — IU* -> `NnRjwEhFU70` (`254s`, `HTTP 206`, `4.41 MB`)
- Minor cleanup identified: `YouTubeDirectResolver.swift:129` passes `"params": "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D"` (URL-encoded inside JSON); replace with canonical base64 `"EgWKAQIIAWoKEAkQBRAKEAMQBA=="` and add a Pass-2 relaxed fallback query + proxy-failure trigger in `AudioPlayerService.swift`.

---

## 2. Concrete Implementation Blueprint

### Fix 1 (P0): Stop Zombie Monochrome Downloads & Burst Timeouts (`backend/providers/monochrome.py`, `backend/services/stream_service.py`)

1. **Add `cancel_event` & Cap Active `.part` Producers (`monochrome.py`)**:
   - Add `cancel_event: threading.Event = field(default_factory=threading.Event)` and `started_at: float = field(default_factory=time.monotonic)` to `ProgressiveCacheEntry`.
   - Inside `_run_producer()`'s chunk read loop (`while True:`): check `if entry.cancel_event.is_set(): break` so a canceled download aborts its upstream socket immediately, unlinks `.part`, and frees bandwidth!
   - Enforce **`MAX_CONCURRENT_PRODUCERS = 2`**: in `get_or_start_download(raw_id)`, under `_ACTIVE_LOCK`, if `len(_ACTIVE_DOWNLOADS) >= MAX_CONCURRENT_PRODUCERS`, find the oldest active entry (excluding `clean_id`) and call `oldest.cancel_event.set()`.
2. **Do NOT Launch 40 MB Downloads on Bare `/info` Calls (`stream_service.py` + `server.py`)**:
   - Add optional `prewarm: bool = False` (`?prewarm=1` query param on `GET /api/stream/{track_id}/info`).
   - Only call `monochrome.start_background_cache(mc_id)` and `soundcloud.start_background_hls_assembly(...)` inside `get_stream_info()` when `prewarm=True` (used by the 2s-delayed next-track prefetch!) OR when `len(monochrome._ACTIVE_DOWNLOADS) == 0`!
   - Actual playback (`GET /api/stream/{track_id}`) always calls `get_or_start_download(mc_id)` immediately (`~0.7s` to first 64 KiB). Rapidly clicking 15 songs in 5 seconds now spawns **at most 2** active downloads instead of 15!
3. **Resilient Monochrome Race Timing**:
   - Raise `MONOCHROME_PARALLEL_GRACE_SEC` from `2.8` to `3.6` seconds, reduce `monochrome.search_tracks()` 429 retry sleep from `1.5s` to `0.6s`, and keep the `3.5s` late wait when SoundCloud misses.

---

### Fix 2 (P0): Enforce Universal Short-Clip Guards & Filter SoundCloud DRM-Only Items (`soundcloud.py`, `monochrome.py`, `jiosaavn.py`, `audius.py`, `bilibili.py`)

1. **Reject DRM-Only SoundCloud Items Before Scoring (`backend/providers/soundcloud.py`)**:
   - Add helper `has_playable_transcoding(item: Dict[str, Any]) -> bool`:
     ```python
     def has_playable_transcoding(item: Dict[str, Any]) -> bool:
         tcs = ((item.get("media") or {}).get("transcodings")) or []
         if not tcs:
             return False
         for tc in tcs:
             if tc.get("snipped") is True:
                 continue
             fmt = tc.get("format") or {}
             proto = str(fmt.get("protocol") or "").lower()
             mime = str(fmt.get("mime_type") or "").lower()
             preset = str(tc.get("preset") or "").lower()
             if proto == "progressive":
                 return True
             if proto == "hls" and "mpegurl" not in mime and not preset.startswith("abr"):
                 return True
         return False
     ```
   - Call `if not has_playable_transcoding(item): return False` inside `is_full_allow_track()` (when `"media"` is present on `item` — note: if a minimal unit-test dict omits `"media"`, only enforce when `"media" in item` so existing unit-test fixtures without `"media"` on `score_candidate()` keep passing!).
2. **Universal Anti-Short-Clip Floor (`>= 60s` & `>= 350 KB`) Across All Full-Track Providers**:
   - Helper `is_valid_full_duration(cand_dur: int, target_duration: int) -> bool`:
     - If `cand_dur <= 0`: return `True` (unknown metadata duration, rely on `min_total_bytes >= 350_000`).
     - If `30 <= target_duration < 60`: require `cand_dur >= max(30, target_duration - 10)` (supports genuine 45s album interludes).
     - Otherwise: **strictly require `cand_dur >= 60`** (blocks 29–30s ringtones/teasers/previews from ever being labeled `"full"`!).
   - Apply in `monochrome.score_candidate()`, `jiosaavn.find_best_stream()`, `audius.find_best_stream()`, `soundcloud.is_full_allow_track()`, and `bilibili.score_candidate()`.
   - Enforce `MIN_TRACK_BYTES = 350_000` in `monochrome.py` and pass `min_total_bytes=350_000` in `jiosaavn.probe_best_url()` and `audius.find_best_stream()`!

---

### Fix 3 (P0): Build the 2-Pass Full-Track Engine (`Pass 1: Studio Master` -> `Pass 2: Relaxed Full-Track Rescue`)

Whenever **Pass 1** (strict studio master matching) finds no full track across all providers, `stream_service._resolve_stream_unlocked()` MUST execute **Pass 2 (`_resolve_pass2_rescue`)** before ever giving up!

1. **Add `relaxed: bool = False` Mode to Providers (`soundcloud.py`, `bilibili.py`, `monochrome.py`, `jiosaavn.py`, `audius.py`)**:
   - **Inviolable Full-Track Rules in `relaxed=True`**:
     - Still strictly enforces `is_valid_full_duration(cand_dur, target_duration)` (`60s <= cand_dur <= 900s`) and `min_total_bytes >= 350_000`.
     - Still strictly rejects `policy != "ALLOW"`, `snipped == True`, `/preview/` URLs, and DRM `cbc/ctr-encrypted-hls`!
   - **Relaxed Matching Rules in `relaxed=True`**:
     - **Duration tolerance**: widen to `cand_dur in [60, 900]` (allows extended mixes, Official MVs with intros/outros, Live versions).
     - **Label / Channel Uploaders Allowed**: accept candidates when `title_sim >= 0.52` AND (`artist_sim >= 0.25` OR any primary artist token of length `>= 3` appears in normalized `title + artist/author` OR `title_sim >= 0.85` with `abs(cand_dur - target_duration) <= 45`).
     - **Reduced Noise Penalty**: multiply noise penalty by `0.25` (`~0.06–0.08` instead of `0.25–0.35`) so Official MVs, Lyric Videos, Live, Acoustic, Cover, or Remix full-length versions are rescued instead of rejected!
     - **Extra Rescue Queries**: try `f"{primary_artist} {clean_title} audio"`, `f"{clean_title} {primary_artist}"`, and `clean_title` (when `len(clean_title) >= 5` and paired with artist/duration verification).
     - Lower `min_score` to `0.48`.
2. **Parallel Pass-2 Execution in `stream_service.py`**:
   - Run Pass 2 concurrently across **SoundCloud (`relaxed=True`) + Bilibili (`relaxed=True`) + Monochrome (`relaxed=True`) + JioSaavn (`relaxed=True`)** (`ThreadPoolExecutor(max_workers=4)`, `timeout=5.0s`), preferring `Monochrome -> JioSaavn -> SoundCloud -> Bilibili`, then `Audius(relaxed=True)`.
   - Record `{"provider": "<src>-rescue", "outcome": "ok", "ms": ...}` in `diagnostics` so `?debug=1` clearly distinguishes Pass-1 studio masters from Pass-2 rescued full tracks!

---

### Fix 4 (P0): Remove Voluntary 30s Previews by Default (`SONIFY_ALLOW_PREVIEWS=0`) + Catalog Hygiene

1. **Disable Voluntary 30s Previews by Default (`backend/config.py` + `backend/services/stream_service.py`)**:
   - Add `config.allow_previews() -> bool`:
     ```python
     def allow_previews() -> bool:
         return os.environ.get("SONIFY_ALLOW_PREVIEWS", "").strip().lower() in (
             "1", "true", "yes", "on"
         )
     ```
   - Gate Tier 6 (`deezer-preview` / `itunes-preview`) in `stream_service._resolve_stream_unlocked()` behind `if config.allow_previews():`!
   - When `config.allow_previews()` is `False` (the default in production and local!), if both Pass 1 and Pass 2 fail to find a real full-length stream, raise `StreamUnavailableError` (`HTTP 502 STREAM_UNAVAILABLE`) — **NEVER serve a surprise 30-second preview clip!**
   - *(Test compatibility note for `@tester`: in `tests/test_stream_and_waterfall.py`, wrap the two explicit Tier-6 preview unit checks at lines ~804 and ~967 in `mock.patch.dict(os.environ, {"SONIFY_ALLOW_PREVIEWS": "1"})`, and add a test asserting default `SONIFY_ALLOW_PREVIEWS` unset raises `StreamUnavailableError` with zero 30s previews!)*

2. **Upgrade Home New Releases & Cold-Start Made For You (`backend/services/catalog_service.py`)**:
   - In `get_home()`: build `new_releases` from **`us_albums` (`apple_itunes.get_top_albums("us", 16)`) + `dz_chart.get("albums")` + chart track albums FIRST**, using `dz_releases` (`editorial/0/releases`) only as a last resort if `< 12` albums were collected! This replaces obscure 1994 TV reissues / niche regional albums with real chart-topping albums that have ~100% full-track availability!
   - In `get_made_for_you()`: ensure cold-start fallback mixes draw from `trending-global` / `trending-vn` / flagship genre tracks rather than obscure tail search hits.

---

### Fix 5 (P0): Client-Side Cache TTL, Web Auto-Rescue/Skip & iOS `YouTubeDirectResolver` Hardening

1. **Cap `hlsUrl` Prefetch Cache TTL to `210s` (3.5 min) on Both Web & iOS**:
   - In `frontend/js/player.js` (`getCachedStreamInfo`): use `const ttl = entry.info?.hlsUrl ? 210_000 : PREFETCH_INFO_TTL_MS;`. Pass `&prewarm=1` on `scheduleNextTrackPrefetch()`'s `api.getStreamInfo(nextTrack.id, undefined, qual, true)` call!
   - In `ios/Sonify/Services/AudioPlayerService.swift` (`cachedPrefetchInfo`): use `let ttl: TimeInterval = (entry.info.hlsUrl != nil) ? 210 : Self.prefetchTTL`, and pass `prewarm: true` from `scheduleNextTrackPrefetch`.
2. **Web Resilient Alternate-Version Rescue & Smart Queue Skip (`frontend/js/player.js`)**:
   - When `api.getStreamInfo()` throws `502 STREAM_UNAVAILABLE`:
     1. **1-Shot Alternate Track Rescue**: search `api.search(\`\${track.title} \${track.artist}\`, 'tracks', 5, signal)` and try the first alternate track ID (`alt.id !== track.id`) if one exists!
     2. **Smart Queue Auto-Skip (Spotify behavior)**: if still unavailable and the user is playing a multi-track queue (`st.queue.length > 1 || st.userQueue.length > 0`) with `< 3` consecutive skips, show toast `"Full track unavailable for '${track.title}' — skipping to next"` and call `playNextTrack(true)` cleanly (resetting `consecutiveSkips = 0` as soon as any track plays)!
3. **Native iOS `YouTubeDirectResolver.swift` & `AudioPlayerService.swift` Hardening (`ios/Sonify/`)**:
   - In `YouTubeDirectResolver.swift`:
     - Fix line 129 `"params": "EgWKAQIIAWoKEAkQBRAKEAMQBA=="` (canonical base64).
     - Add Pass-2 relaxed fallback in `resolveFullTrack(for:)`: if strict `score >= 0.58` finds 0 matches, try relaxed scoring (`score >= 0.45`, duration `60..900s`, reduced noise penalty) and secondary query `"\(cleaned) \(primary) official audio"` so even niche/MV/label tracks resolve 100% on iPhone!
   - In `AudioPlayerService.swift`:
     - Notice that with `SONIFY_ALLOW_PREVIEWS=0` on Render, any track Render can't resolve throws `502`, which ALREADY enters `catch` at line 600 and triggers `YouTubeDirectResolver.shared.resolveFullTrack(for: track)`!
     - **ALSO** add a runtime rescue inside `observePlayerItem` (`.failed`): if BOTH direct CDN AND Render proxy fail during playback, invoke `YouTubeDirectResolver.shared.resolveFullTrack(for: track)` once before surfacing an error!

---

## 3. QA & Zero-Preview Verification Checklist (For `@tester`)

1. **Automated Test Suite (`tests/test_zero_preview_elimination.py` + existing 90 tests)**:
   - **Zero Voluntary Previews**: With default env (`SONIFY_ALLOW_PREVIEWS` unset), when Pass 1 + Pass 2 return `None`, `resolve_stream()` raises `StreamUnavailableError` (NEVER returns `mode == "preview"`). With `SONIFY_ALLOW_PREVIEWS=1`, opt-in preview still works.
   - **2-Pass Rescue Engine**: Mock a track where Pass 1 rejects a label-uploaded Official MV / Live version (`uploader="HYBE LABELS"`, `duration +25s`), assert Pass 2 rescues it as `mode == "full"` while still rejecting `SNIP`, `/preview/`, `< 60s`, and DRM-only items!
   - **DRM-Only SoundCloud Filter**: Assert a SoundCloud item with only `cbc-encrypted-hls` / `ctr-encrypted-hls` transcodings is rejected by `is_full_allow_track()`, allowing a valid `hls` item behind it to win!
   - **Monochrome Active Producer Cap & Cancel**: Assert starting a 3rd concurrent `.part` download sets `cancel_event` on the oldest active producer (`MAX_CONCURRENT_PRODUCERS = 2`), and bare `get_stream_info(prewarm=False)` does not spawn extra downloads when one is already active.
   - **Universal Short-Clip Floor**: Assert `monochrome`, `jiosaavn`, `audius`, `soundcloud`, and `bilibili` all reject `< 60s` candidates (when `target_duration >= 60` or `0`) and `< 350 KB` responses.
   - **Client & iOS Checks**: Verify `210s` `hlsUrl` TTL in `player.js` & `AudioPlayerService.swift`, Web alternate-track rescue + max-3 queue skip, canonical `params` + Pass-2 rescue in `YouTubeDirectResolver.swift`, and all Swift files balanced & Apple-only.
2. **Real-World Stress Verification (`RENDER=true`)**:
   - **Test A — 15-Song Rapid-Click Burst Test (`0.3s` apart)**: Fire 15 overlapping `/info` + `/stream` requests -> assert **0 previews** (`0%` preview rate!) and fast bounded completion!
   - **Test B — 60-Song Uncurated All-Surfaces Sweep** (Home Global, Home VN, New Release Albums, Top Artists, Made For You, Radio, Genre Hubs, C-Pop/J-Pop/K-Pop/V-Pop/Indie searches) -> assert **`0` `preview` responses (0.0% previews)** and **>= 98% `full` playable tracks** (`HTTP 206`, `>= 350 KB`)!
