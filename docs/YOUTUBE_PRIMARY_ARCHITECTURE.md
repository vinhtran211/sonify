# Sonify — YouTube-Primary MP3/MP4 Architecture Spec

> **Status**: Implementation-Ready Specification (`#104`)  
> **Owner**: `researcher` (`docs/YOUTUBE_PRIMARY_ARCHITECTURE.md`)  
> **Target Implementers**: `@builder` (backend, Docker, web frontend, iOS SwiftUI) & `@tester` (automated tests + verification)

---

## 1. Executive Summary & Root Cause

Users reported that non-YouTube streaming providers (Monochrome FLAC, SoundCloud, JioSaavn, Bilibili, Audius) frequently fail to match tracks, return wrong versions, get rate-limited, or miss songs that exist trivially on YouTube.

### Why the Previous Architecture Under-Used YouTube
1. **Search hid YouTube (`backend/services/catalog_service.py:638-674`)**: `youtube_music.search_songs()` only ran as a last resort when Deezer + iTunes + JioSaavn + Audius returned `< 3` tracks, and received a lower `source_bonus` (`0.05`) than Deezer (`0.08`) and iTunes (`0.07`). As a result, search rarely returned direct `yt:<videoId>` tracks, forcing fragile cross-provider fuzzy matching at playback time.
2. **Cloud stream waterfall put YouTube behind Monochrome/SoundCloud (`backend/services/stream_service.py:1077-1305`)**: Under `RENDER=true`, Monochrome FLAC (`~23 MB`) and SoundCloud ran *before* YouTube even when `YTDLP_COOKIES_B64` was configured, and anonymous cloud requests skipped YouTube entirely.
3. **Missing 2026 `yt-dlp` JS runtime in Docker (`Dockerfile`, `requirements.txt`)**: In 2026, `yt-dlp` requires an external JS runtime (**Deno `>= 2.3.0`** by default) plus **`yt-dlp[default]`** (which bundles `yt-dlp-ejs` player challenge-solver scripts) to extract YouTube media URLs reliably. The current `Dockerfile` installed plain `yt-dlp` with no JS runtime.
4. **Native iOS did not default to YouTube Direct (`ios/Sonify/`)**: `AudioPlayerService.preferYouTubeDirect` defaulted to `false`, `OfflineDownloadManager.downloadTrack()` called the cloud backend first instead of the iPhone's residential IP, and `YouTubeDirectResolver.resolveFullTrack()` did not fast-path tracks whose `id` already starts with `yt:<videoId>`.
5. **`.mp3` URL extension did not trigger MP3 transcoding (`backend/server.py:578`)**: `strip_known_stream_extension()` stripped `.mp3` without setting `target_format="mp3"`, so `GET /api/stream/{id}.mp3` could still serve FLAC/M4A unless `?format=mp3` was also supplied.

### Design Goals
- **YouTube is Tier 1 PRIMARY everywhere**: Search surfaces YouTube Music (`yt:<videoId>`) directly alongside Deezer/iTunes metadata; playback resolves YouTube MP3/M4A/MP4 first on local machines, cloud deployments (with user cookies/proxy), and natively on iOS residential IPs.
- **Light, Fast Audio (`~2.4–4.0 MB` per song)**: Pure `audio/mp4` (`itag=140`), zero-reencode `ffmpeg -vn -c:a copy` M4A from `itag=18`, or `128k` MP3 via `transcoder.py` (`libmp3lame`).
- **100% Ad-Free, Lawful & Security-Hardened**: Zero ads, zero CAPTCHA/BotGuard anti-bot bypasses, and 100% preservation of OWASP hardening from commit `2177812`.
- **Silent Emergency Fallback**: Keep SoundCloud, Monochrome, JioSaavn, Bilibili, Audius, and Deezer/iTunes previews strictly as automatic fallback when YouTube is unavailable or an anonymous cloud IP is gated.

---

## 2. Pillar 1 — 2026 YouTube MP3/MP4 Extraction & Compliance Boundary

### 2.1 Explicit Anti-Bot / CAPTCHA Compliance Decision
> [!IMPORTANT]
> **Do NOT install or deploy `bgutil-ytdlp-pot-provider` (BotGuard PO-token minting / anti-bot attestation bypass).**
>
> `bgutil-ytdlp-pot-provider` specifically executes YouTube's BotGuard VM to defeat YouTube's *"Sign in to confirm you're not a bot"* verification gate on datacenter IPs, and also requires a multi-process Node/Deno HTTP sidecar (`:4416`) inside Render's single free container. Sonify must **never** bypass CAPTCHAs or anti-bot challenges.
>
> Similarly, live 2026 probes confirmed public Invidious/Piped mirrors (`pipedapi.kavin.rocks`, `api.piped.private.coffee`, `inv.nadeko.net`, `yewtu.be`) return HTTP `403`/`500` and are unsuitable as reliable production extractors.

Instead, Sonify achieves reliable YouTube MP3/MP4 playback through three clean, sanctioned paths:
1. **iOS & Local / Residential IP (Zero Config, Works Out of the Box)**:
   - Direct InnerTube `ANDROID 20.10.38` (`itag=140` adaptive `audio/mp4` with 2-step tail probe -> `itag=18` progressive `video/mp4`) -> `IOS 20.10.4` HLS (iOS only) -> local `yt-dlp[default]` + `deno`.
   - Empirically verified on 2026-11-28: `0.17–0.82s` resolution, `~2.3–3.5 MB` per full track.
2. **Cloud / Render Deployment (`Dockerfile` with `deno` + `yt-dlp[default]` + `ffmpeg`)**:
   - Standard `yt-dlp[default]` + `deno` runtime handles YouTube's 2026 EJS player URL extraction cleanly.
   - When YouTube gates Render's AWS datacenter IP with *"Sign in to confirm you're not a bot"* (`LOGIN_REQUIRED`), the user supplies their own authenticated YouTube session via Render environment variables (`YTDLP_COOKIES_B64` or `YTDLP_COOKIES_PATH`) or an egress proxy (`YTDLP_PROXY`).
   - If no cloud credentials are set, Sonify does a single fast bounded probe (`2.5s`) protected by a **5-minute datacenter gate circuit-breaker**, transparently falls back to emergency providers (SoundCloud / Monochrome / Bilibili / JioSaavn), and surfaces a clear setup hint in `/api/health` and debug stream info.
3. **Audio Compaction & MP3/M4A Delivery (`backend/services/transcoder.py`)**:
   - Already hardened with `shell=False`, `-protocol_whitelist file,pipe`, `Semaphore(2)`, `30s` timeout, and `150 MiB` LRU disk cache at `/tmp/sonify_compact_cache`.
   - **`m4a_copy`**: `ffmpeg -vn -c:a copy -movflags +faststart` strips video packets from YouTube `itag=18` MP4 in `< 0.4s` with negligible CPU -> pure `audio/mp4` (`~2.0–3.5 MB`).
   - **`mp3`**: `ffmpeg -vn -c:a libmp3lame -b:a 128k` produces universal `audio/mpeg` MP3 (`~3.4 MB` for a 3:40 track).

### 2.2 Exact `Dockerfile` & `requirements.txt` Changes

**`requirements.txt`**:
```text
# Cloud/Docker YouTube extraction + decryption + audio tooling
cryptography>=43.0.0,<46.0.0
yt-dlp[default]>=2025.11.0,<2027.0.0
```
*(The `[default]` extra installs `yt-dlp-ejs` solver scripts required for 2026 YouTube extraction.)*

**`Dockerfile`** (add official Deno binary before `pip install`; keep non-root `USER user` UID 1000 and `ffmpeg`):
```dockerfile
FROM python:3.12-slim

# Install CA certificates, curl (for Docker HEALTHCHECK), and ffmpeg (MP3/M4A compaction)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Official Deno binary (default JS runtime for 2026 yt-dlp EJS player extraction)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Install cloud streaming & decryption dependencies (cryptography + yt-dlp[default] with yt-dlp-ejs)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt
```
Set `ENV XDG_CACHE_HOME=/tmp/.cache` alongside existing `ENV` vars so `yt-dlp` and `deno` have a writable cache dir under non-root `USER user`.

---

## 3. Pillar 2 — YouTube-First Backend Pipeline

### 3.1 `backend/providers/youtube_music.py`

1. **Cloud Anonymous Gate Circuit-Breaker**:
   - Add module-level thread-safe state:
     ```python
     _CLOUD_ANON_LOCK = threading.Lock()
     _CLOUD_ANON_GATE_UNTIL: float = 0.0
     _CLOUD_ANON_GATE_COOLDOWN_SEC: float = 300.0
     ```
   - Helper `is_cloud_anon_gated() -> bool` and `trip_cloud_anon_gate(seconds=300.0) -> None` (plus `reset_cloud_anon_gate()` for unit tests).
   - When `_resolve_via_innertube_player()` receives `playabilityStatus.status in ("LOGIN_REQUIRED", "UNPLAYABLE", "ERROR")` with a bot/sign-in reason (`"bot"`, `"sign in"`, `"confirm"`) under `config.is_cloud_env()` and `not has_cloud_youtube_credentials()`, call `trip_cloud_anon_gate()`.
2. **Upgraded `_resolve_via_yt_dlp(video_id: str)`**:
   - Keep `security.validate_provider_id("yt", video_id)` and `"--"` terminator before `https://www.youtube.com/watch?v={video_id}`.
   - When `has_cloud_youtube_credentials()` is True (cookies/proxy set), update `--extractor-args` to:
     - With cookies: `"youtube:player_client=web,tv_embedded,android,ios"` (authenticated `web` + `tv_embedded` + `deno` EJS succeeds reliably with cookies and yields full `audio/mp4` `itag=140` / `webm` / `itag=18`!).
     - Without cookies (local/residential): `"youtube:player_client=android,ios,web"` (fast anonymous `itag=140` / `itag=18`).
   - Add `--js-runtimes deno` only if `shutil.which("deno")` exists (Deno is auto-detected by `yt-dlp`, passing explicitly if present is clean; if `node` is present locally and `deno` is absent, pass `--js-runtimes node`).
   - Keep 2-step tail verification (`verify_tail=True`) on adaptive `audio/*` formats so 1 MiB PO-token traps automatically fall back to progressive `itag=18` MP4.
3. **Track Metadata Enrichment Helper `get_track_metadata(video_id: str) -> Optional[Dict[str, Any]]`**:
   - Calls InnerTube `player` (`ANDROID` or `WEB_REMIX`) to fetch `videoDetails` (`title`, `author`, `lengthSeconds`, high-res thumbnail `https://i.ytimg.com/vi/{video_id}/hqdefault.jpg`) when `_load_track_metadata("yt:<videoId>")` encounters an uncached `yt:` ID, so direct `yt:<videoId>` links display real titles, artists, durations, and covers instead of raw video IDs.
4. **Runtime Capability Inspector `get_runtime_status() -> Dict[str, Any]`**:
   - Returns strictly non-sensitive booleans for `/api/health`:
     ```python
     {
         "ytDlp": bool(shutil.which("yt-dlp")),
         "deno": bool(shutil.which("deno")),
         "node": bool(shutil.which("node")),
         "ffmpeg": bool(shutil.which("ffmpeg")),
         "cloudMode": config.is_cloud_env(),
         "cloudCredentialsConfigured": has_cloud_youtube_credentials(),
         "cloudAnonGated": is_cloud_anon_gated(),
     }
     ```
   - Never expose cookie contents, cookie file paths, or proxy URLs.

### 3.2 `backend/services/catalog_service.py` (YouTube-First Search)

1. **Always Run YouTube Music in Parallel Search (`search()`, lines 638–674)**:
   - Increase `ThreadPoolExecutor(max_workers=7)` and submit `f_yt_t = pool.submit(_safe, youtube_music.search_songs, q, min(lim, 15))` whenever `need_tracks` is True!
   - Include `(f_yt_t, yt_tracks)` in `submitted_results` so search still succeeds online even if Deezer/iTunes fail.
   - Combine tracks with YouTube Music included upfront:
     ```python
     combined_tracks = (yt_tracks or []) + (dz_tracks or []) + (it_tracks or []) + (js_tracks or []) + (au_tracks or [])
     ```
   - Remove the old `< 3` fallback-only guard for YouTube Music.
2. **Promote YouTube Music in `_dedup_and_rank_tracks()` (lines 416–507)**:
   - Update `source_bonus`:
     ```python
     source_bonus = {
         "youtube_music": 0.11,
         "deezer": 0.08,
         "itunes": 0.07,
         "jiosaavn": 0.04,
         "audius": 0.02,
         "local": 0.0,
     }
     ```
   - Update `rank_boost` (line 485) so top-ranked YouTube Music hits also receive positional boost:
     ```python
     rank_boost = max(0.0, 0.07 * (1.0 - pos / 20.0)) if src in ("youtube_music", "deezer", "itunes") else 0.0
     ```
   - **Cross-Enrichment on Dedup**: When deduplicating on `key = f"{title_norm}::{art_norm}"`:
     - If a `youtube_music` track collides with a `deezer`/`itunes` track, preserve `videoId` on the winner's `_extra["videoId"]` AND preserve high-res artwork / `previewUrl` / `album` / `isrc` from Deezer/iTunes if the YouTube hit has a blank album or smaller cover!
     - Because `youtube_music` has the highest `source_bonus` (`0.11`), exact studio YouTube Music tracks (`yt:<videoId>`) naturally win top search slots with rich artwork, exact duration, AND zero-lookup `yt:<videoId>` playback!
   - Keep `_SEARCH_NOISE_PATTERNS` penalty (`-0.42`) intact so karaoke/slowed/nightcore/tribute knockoffs are still demoted unless explicitly searched.

### 3.3 `backend/services/stream_service.py` (YouTube ALWAYS Tier 1 Primary)

Reorder `_resolve_stream_unlocked()` so **YouTube is ALWAYS Tier 1 PRIMARY** across BOTH local and cloud environments:

| Priority | Condition | Provider / Action | Typical Output |
| :--- | :--- | :--- | :--- |
| **Tier 0** | `prefix == "yt"` (direct YouTube track) | `youtube_music.resolve_audio_stream(raw_id)` (respecting cloud anon gate breaker if no cloud creds) | `audio/mp4` (`itag=140`) / `video/mp4` (`itag=18`) / `yt-dlp` |
| **Tier 1 (PRIMARY — Local & Cloud w/ Creds)** | Any track (`dz:`, `it:`, `au:`, `js:`, etc.) when NOT cloud-anon-gated | `self._try_resolve_youtube(...)` (`find_best_video_id` -> `resolve_audio_stream`) — **runs BEFORE Monochrome & SoundCloud in ALL quality modes (`auto`, `compact`, `fast`, `lossless`)!** | `~2.3–3.8 MB` YouTube `audio/mp4` or `video/mp4` |
| **Tier 1b (Cloud Anon Fast Probe)** | `RENDER=true` AND `not has_cloud_youtube_credentials()` | If `not youtube_music.is_cloud_anon_gated()`: try 1 fast bounded YouTube InnerTube probe (`timeout=2.5s`). If gated, trip 5-min breaker and record `outcome="cloud_ip_gated_set_cookies"`. If breaker already open, skip in `0ms` (`outcome="skipped_cloud_no_credentials"`). | Either instant YouTube full track or 0ms fast failover |
| **Tier 2 (Silent Emergency Fallback)** | Only when YouTube missed or cloud IP is gated | Existing resilient waterfall: `compact`/`fast`: SoundCloud -> JioSaavn -> Bilibili -> Audius -> Monochrome; `auto`/`lossless`: Parallel Monochrome + SoundCloud -> JioSaavn -> Bilibili -> Audius | Full track backup |
| **Tier 3 (Pass 2 Rescue)** | All primary + fallback missed | `_resolve_pass2_rescue()` (relaxed multi-provider search) | Full track rescue |
| **Tier 4 / 5** | Last resort | Deezer/iTunes 30s preview -> Offline Synth WAV | Never hard-crashes |

#### Automatic Audio-Only Compaction & MP3 Delivery
1. **When `target_format == "mp3"`** (via `?format=mp3` OR `/api/stream/{id}.mp3`):
   - Stream/transcode the resolved YouTube (or fallback) audio through `transcoder.transcode_to_mp3()` -> `Content-Type: audio/mpeg`, `source = f"{base_source}-mp3"`.
2. **When `target_format == "m4a"` OR (`quality == "compact"` and YouTube resolved `video/mp4`)**:
   - Remux via `transcoder.compact_to_m4a_copy()` (`ffmpeg -vn -c:a copy -movflags +faststart`) -> `Content-Type: audio/mp4`, `source = f"{base_source}-compact"`.
   - If YouTube ALREADY resolved pure `audio/mp4` (`itag=140`) or `audio/webm`, serve directly with ZERO transcode CPU!
3. **`get_stream_info()` Debug & Readiness Hints**:
   - When `debug=True`, include `"youtubeCloudReady": (not config.is_cloud_env()) or youtube_music.has_cloud_youtube_credentials()` and `"youtubeCloudHint"` (`"Set YTDLP_COOKIES_B64 on Render for cloud YouTube MP3/MP4"` when cloud + unconfigured, else `None`).

### 3.4 `backend/server.py` (Direct `.mp3` / `.m4a` Stream Endpoints & `/api/health`)

1. **Extension-Aware Format Inference (`backend/server.py:578-590`)**:
   - Before stripping the extension from `rest`, inspect its lowercase suffix:
     ```python
     raw_stream_segment = urllib.parse.unquote(rest).strip("/")
     inferred_fmt = ""
     lower_seg = raw_stream_segment.lower()
     if lower_seg.endswith(".mp3"):
         inferred_fmt = "mp3"
     elif lower_seg.endswith(".m4a"):
         inferred_fmt = "m4a"
     track_id = strip_known_stream_extension(raw_stream_segment)
     fmt = self._qparam(qs, "format", "").strip().lower() or inferred_fmt
     ```
   - This makes `GET /api/stream/yt:dQw4w9WgXcQ.mp3` and `GET /api/stream/dz:12345.mp3` directly return `audio/mpeg` (`128k` MP3) without requiring `?format=mp3`, while keeping `/api/stream/{id}` unchanged for existing web/iOS clients!
   - Also validate `fmt in ("", "mp3", "m4a", "auto")` (reject unknown formats with `400 INVALID_FORMAT`).
2. **`GET /api/health` Enhancement**:
   - Include `"youtubeRuntime": youtube_music.get_runtime_status()` in the JSON response so operators and tests can verify `deno`, `yt-dlp`, `ffmpeg`, and `cloudCredentialsConfigured` in one call.

---

## 4. Pillar 3 — Native iOS YouTube-First by Default (`ios/Sonify/`)

Because an iPhone runs on a **real residential/cellular IP**, native iOS YouTube playback bypasses Render's AWS datacenter IP completely and resolves YouTube audio directly in `< 0.7s`.

### 4.1 `ios/Sonify/Services/YouTubeDirectResolver.swift`
1. **Direct `yt:<videoId>` Validation & Instant Fast-Path**:
   - Add a static helper:
     ```swift
     static func extractDirectVideoId(from trackId: String) -> String? {
         guard trackId.hasPrefix("yt:") else { return nil }
         let raw = String(trackId.dropFirst(3))
         guard raw.count == 11,
               raw.unicodeScalars.allSatisfy({
                   ($0.value >= 65 && $0.value <= 90) ||
                   ($0.value >= 97 && $0.value <= 122) ||
                   ($0.value >= 48 && $0.value <= 57) ||
                   $0 == "-" || $0 == "_"
               }) else { return nil }
         return raw
     }
     ```
   - At the very top of `resolveFullTrack(for track: Track, allowHLS: Bool = true)` (right after `cachedStream(for: track.id)`):
     ```swift
     if let directVideoId = Self.extractDirectVideoId(from: track.id) {
         if let resolved = await resolveVideoStream(videoId: directVideoId, allowHLS: allowHLS) {
             storeCache(resolved, for: track.id)
             return resolved
         }
     }
     ```
   - Result: Any `yt:<videoId>` track from Search skips `searchYouTubeMusic()` completely and goes straight to InnerTube `ANDROID` (`itag=140` `audio/mp4` -> `itag=18`) / `IOS` HLS!

### 4.2 `ios/Sonify/Services/AudioPlayerService.swift`
1. **Default `preferYouTubeDirect` to `true`**:
   - Change the `UserDefaults` read on init from `UserDefaults.standard.bool(forKey: "sonify.preferYouTubeDirect")` (which defaulted to `false`) to:
     ```swift
     if UserDefaults.standard.object(forKey: "sonify.preferYouTubeDirect") == nil {
         self.preferYouTubeDirect = true
     } else {
         self.preferYouTubeDirect = UserDefaults.standard.bool(forKey: "sonify.preferYouTubeDirect")
     }
     ```
   - Keep the existing `preferYouTubeDirect` playback branch at lines 591–607 (which tries `YouTubeDirectResolver.shared.resolveFullTrack(for: track)` FIRST and cleanly falls back to `api.getStreamInfo` if it returns `nil`).
   - Also allow `preferYouTubeDirect` to be toggled in `AudioSettingsView` / Settings if a user ever wants to force Render Lossless FLAC mode.

### 4.3 `ios/Sonify/Services/OfflineDownloadManager.swift`
1. **YouTube-First Light Offline Downloads (`downloadTrack()`, lines 334–352)**:
   - Invert the resolution order so **YouTube Direct (`allowHLS: false`) runs FIRST by default** (unless `UserDefaults.standard.object(forKey: "sonify.preferYouTubeDirect") as? Bool == false` or `effectiveQuality == "lossless"`):
     1. Try `YouTubeDirectResolver.shared.resolveFullTrack(for: track, allowHLS: false)` FIRST!
        - Gets `itag=140` pure `audio/mp4` (`~3.4 MB`), or `itag=18` `video/mp4` which the existing `AVAssetExportPresetAppleM4A` post-processor strips to pure `.m4a` (`~2.3–3.5 MB`)!
     2. Only if YouTube Direct returns `nil` (or fails download validation), fall back to `api.getStreamInfo(trackId: track.id, quality: effectiveQuality)` and download from the backend proxy!
   - Preserve existing magic-byte sniffing, `350 KB` minimum file size check, and `AVAssetExportPresetAppleM4A` video-stripping pipeline.

---

## 5. Pillar 4 — Web UI & Security Preservation

### 5.1 Web Frontend (`frontend/js/components/playerBar.js`)
Update the stream badge label mapping (`lines 265–274`) so YouTube MP3/Audio/MP4 streams display cleanly:
```js
const rawSrc = String(st.streamSource || '').toLowerCase();
const srcLabel =
  rawSrc === 'monochrome'
    ? 'Lossless FLAC'
    : rawSrc === 'youtube-mp3' || rawSrc === 'yt-dlp-mp3'
    ? 'YouTube MP3'
    : rawSrc === 'youtube-compact' || rawSrc === 'yt-dlp-compact' || rawSrc === 'youtube-direct'
    ? 'YouTube Audio'
    : rawSrc === 'youtube' || rawSrc === 'yt-dlp'
    ? (st.streamContentType === 'video/mp4' ? 'YouTube MP4' : 'YouTube Audio')
    : rawSrc.endsWith('-mp3')
    ? 'Compact MP3'
    : rawSrc.endsWith('-compact')
    ? 'Compact M4A'
    : st.streamSource || '';
```
*(Ensure `store.js`/`player.js` passes `streamContentType` or keep graceful fallback to `'YouTube Audio'` if omitted.)*

Keep `quality=compact` as the default for Web offline downloads (`offlineDownloads.js`) so cached offline songs remain `~3–4 MB`.

### 5.2 Security Preservation Checklist (Zero OWASP Regressions)
- [ ] `security.validate_provider_id("yt", video_id)` enforced before every `yt-dlp` subprocess or InnerTube request.
- [ ] `"--"` argument terminator always precedes `https://www.youtube.com/watch?v={video_id}` in `_resolve_via_yt_dlp`.
- [ ] `transcoder.py` retains `shell=False`, `-protocol_whitelist file,pipe`, `Semaphore(2)`, `30s` timeout, `150 MiB` LRU cache cap.
- [ ] `YTDLP_COOKIES_B64` retains `0o700` dir / `0o600` file permissions + `O_NOFOLLOW`; `/api/health` and `/api/stream/{id}/info` NEVER leak cookie bytes, paths, or proxy URLs.
- [ ] `security.validate_outbound_url` enforced on all outbound HTTP requests and redirects.
- [ ] Zero BotGuard/CAPTCHA bypass libraries or sidecars added.
- [ ] iOS uses strictly Apple system frameworks (`Foundation`, `SwiftUI`, `AVFoundation`, `MediaPlayer`, `UIKit`, `CryptoKit`), zero external dependencies, balanced braces across all Swift files.

---

## 6. Deployment Note for Cloud Users (Render)

Add to `.env.example` / `README.md` (already has `YTDLP_COOKIES_B64` stub from `#94` — verify wording is crisp):
- **Local (`./run.sh`) & Native iOS**: YouTube MP3/MP4 playback works immediately out of the box (residential IP).
- **Render Cloud (`https://sonify-music.onrender.com`)**: Because YouTube gates AWS datacenter IPs with *"Sign in to confirm you're not a bot"*, set `YTDLP_COOKIES_B64` (base64-encoded Netscape `cookies.txt` exported from a signed-in browser session) or `YTDLP_PROXY` in Render Environment Variables to unlock 100% cloud YouTube MP3/MP4 extraction. Without it, Render automatically uses emergency fallback providers and reports `youtubeRuntime.cloudCredentialsConfigured: false` on `/api/health`.

---

## 7. QA & Automated Test Acceptance Criteria (`@tester`)

### 7.1 Legacy Test Update
- Update `tests/test_stream_and_waterfall.py::test_cloud_waterfall_skips_anonymous_youtube_runs_soundcloud_before_jiosaavn_and_debug_info`:
  - Under `RENDER=true` **with `YTDLP_COOKIES_B64`**, assert YouTube runs **FIRST** (even if Monochrome is mocked to succeed, YouTube wins Tier 1!).
  - Under `RENDER=true` **without credentials** when anonymous YouTube is gated, assert the circuit-breaker trips and SoundCloud/Monochrome fallback succeeds cleanly without repeated timeouts.

### 7.2 New Test Suite (`tests/test_youtube_primary_architecture.py`)
1. **YouTube-Primary Search**: Verify `catalog_service.search("any query")` always calls `youtube_music.search_songs()` in parallel (even when Deezer/iTunes return 10+ results) and ranks exact `yt:<videoId>` matches at the top while enriching metadata on dedup.
2. **YouTube Tier 1 Primary Waterfall (Local & Cloud w/ Creds)**: Verify `stream_service.resolve_stream()` resolves YouTube BEFORE Monochrome and SoundCloud in both `auto` and `compact` modes.
3. **Cloud Anon Gate Circuit-Breaker**: Verify an anonymous cloud `LOGIN_REQUIRED` response trips `is_cloud_anon_gated()`, prevents repeated YouTube probes on the next track, and cleanly falls back to emergency providers.
4. **Direct `.mp3` & `.m4a` Stream Routes**: Verify `GET /api/stream/yt:dQw4w9WgXcQ.mp3` infers `target_format="mp3"` and returns `audio/mpeg`, while `.m4a` infers `target_format="m4a"` (`audio/mp4`), and invalid `?format=evil` returns `400`.
5. **`/api/health` Runtime Status**: Verify `youtubeRuntime` exposes booleans (`ytDlp`, `deno`, `ffmpeg`, `cloudMode`, `cloudCredentialsConfigured`, `cloudAnonGated`) and leaks zero secrets/paths even when `YTDLP_COOKIES_B64` / `YTDLP_PROXY` are set.
6. **Container & Dependency Verification**: Static checks on `Dockerfile` (`COPY --from=denoland/deno:bin /deno /usr/local/bin/deno`, `ffmpeg`, `USER user`, `XDG_CACHE_HOME`) and `requirements.txt` (`yt-dlp[default]`, no `bgutil`).
7. **Security Regressions**: All 126+ existing tests (`test_security_hardening.py`, `test_youtube_mp3_and_light_offline.py`, etc.) pass cleanly.

### 7.3 iOS Tests (`ios/SonifyTests/YouTubeDirectResolverTests.swift`)
- Verify `YouTubeDirectResolver.extractDirectVideoId(from:)` accepts valid 11-char `yt:<id>` and rejects malformed/injection strings.
- Verify `resolveFullTrack` fast-paths `yt:<videoId>` directly without issuing a search request.
- Verify `AudioPlayerService.preferYouTubeDirect` defaults to `true` when `UserDefaults` key is unset, and `OfflineDownloadManager` tries YouTube Direct first.
- Verify balanced `{}`/`()` and Apple-only imports across all Swift files in `ios/Sonify/`.
