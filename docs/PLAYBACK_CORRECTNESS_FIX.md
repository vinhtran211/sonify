# Sonify Playback Correctness & Zero-Mismatch Audit Spec (`docs/PLAYBACK_CORRECTNESS_FIX.md`)

**Status**: Implementation-Ready Root-Cause Audit & Surgical Fix Spec  
**Baseline Audited**: Commit `61fc0f7` (Live on `https://sonify-music.onrender.com`)  
**Scope**: Backend (`backend/`), Web/PWA (`frontend/js/`), Native SwiftUI iOS (`ios/Sonify/`) + Regression Suite (`tests/`, `ios/SonifyTests/`)

---

## 1. Executive Summary — All 9 Confirmed Root Causes

Live probes against `https://sonify-music.onrender.com` (`it:1749963740` *Đừng Làm Trái Tim Anh Đau*, `it:6827404203` *Như Một Người Rưng*) combined with a line-by-line audit of `backend/`, `frontend/js/`, and `ios/Sonify/` uncovered **9 concrete playback correctness bugs** across 4 pillars:

1. **Bug 1A — Unverified Monochrome `/info` + 10.3s `429` Producer Retry Stall + Mid-Request Codec Swap (`monochrome.py`, `stream_service.py`)**:
   - `get_stream_info()` returns `streamSource: "monochrome"`, `contentType: "audio/flac"`, `streamUrlWithExt: "/api/stream/{id}.flac"` without checking whether Monochrome's upstream `/track/{mc_id}` actually delivers bytes.
   - When `tracks.monochrome.st` rate-limits (`HTTP 429`) or stalls on Render, `monochrome._run_producer()` sleeps `0.6s (pace) + 1.0s + 2.0s + 3.5s + 4 RTTs = ~10.3s` before failing, and `_serve_progressive_monochrome()` blocks up to `20.0s`.
   - `stream_to_handler()` then cascades **mid-request** on `/api/stream/{id}.flac` to SoundCloud/Bilibili (`audio/mp4` `ftyp` bytes), causing a **10s freeze at `0:00`**, leaving Web's badge stuck on `Full Track • Lossless FLAC`, and **fatally breaking iOS `AVPlayer`**.
2. **Bug 1B — iOS `AVPlayer` `AVURLAssetOverrideMIMETypeKey = "audio/flac"` Hard-Lock on `.proxy` Routes (`AudioPlayerService.swift:482–486`)**:
   - On iOS 17+, `makePlayerItem()` sets `AVURLAssetOverrideMIMETypeKey` even on `.proxy` routes (`/api/stream/{id}.flac`). When `/api/stream` cascades mid-request to `audio/mp4`, CoreMedia's forced FLAC parser rejects the `ftyp` stream (`AVPlayerItem.status == .failed`), preventing playback completely.
3. **Bug 2A — Unbounded Substring Bug in `lrc_parser.token_similarity()` (`lrc_parser.py:182–186`)**:
   - `if na in nb or nb in na:` has **zero minimum length** and **zero word-boundary check**. Single-letter/short strings match unrelated words with `>= 0.85` similarity (e.g. `token_similarity("a", "osad") == 0.85`, `token_similarity("den", "golden hour") == 0.855`), bypassing title and artist gates!
4. **Bug 2B — Symmetric `±140s` (`75%`) Duration Tolerance in `soundcloud.py` & `[60, 900]` in Pass 2 Allowing Chopped (`1:59` for `2:57`) & Sped-Up (`4:20` for `4:39`) Uploads**:
   - On `it:6827404203` (*Như Một Người Rưng*, `177s` / `2:57`), SoundCloud Pass 1 matched `SC id=2434705296` (`119s` / `1:59` `(AYDEN EDIT) FREEDOWNLOAD`, `score=0.819`) — **58s shorter** — blocking Tier 4.5 Bilibili which had the **exact `180s` (`3:00`) authentic full track (`BV1fpWn6nEMM`, `score=0.990`)**!
   - On `it:1749963740` (*Đừng Làm Trái Tim Anh Đau*, `279s` / `4:39`), SoundCloud matched `SC id=1848871632` (`260s` / `4:20` — **19s shorter** `1.073x` sped-up copyright-dodge reupload).
5. **Bug 2C — Unrequested Remixes, Club Edits, Mashups, Covers, Sped-Up/Slower, & Dance Versions Passing Pass 1 & Pass 2 (`soundcloud.py`, `monochrome.py`, `bilibili.py`, `jiosaavn.py`, `audius.py`, `YouTubeDirectResolver.swift`)**:
   - Subtracting a `-0.22` noise penalty (`-0.055` in Pass 2!) from `0.95` still leaves `0.73 >= 0.60` (`DANNYZ MASHUP` scored `0.732`, `SONGTOAN (cover)` scored `0.679`, `bản Cha Cha Cha` scored `0.889`, `Afterglow Edit` scored `0.678`, `(Slower)` scored `0.786`). Worse, `soundcloud._UPLOAD_JUNK_RE` actively stripped `"free download"` before scoring!
6. **Bug 2D — Zero-Artist `title_sim >= 0.85` + `±45s` Loophole Playing Unrelated Songs Sharing Generic Titles**:
   - Across all 5 backend providers and `YouTubeDirectResolver.swift`, `artist_sim` is artificially boosted to `0.50–0.55` whenever `title_sim >= 0.85` and `abs(cand_dur - target_duration) <= 45`, allowing generic titles (`"Stay"`, `"Home"`, `"Tình Cờ"`, `"Yêu"`) to match **completely different songs by unrelated artists**.
7. **Bug 3A — Suspended `AudioContext` Silent Audio Bug (`main.js:516`, `player.js:209,235–260`, `audioEffects.js:32–84`)**:
   - `main.js:516` calls `syncAudioSettings()` during page `bootstrap()` (zero user gesture). If the user has any saved non-flat EQ preset, `ensureEqGraph(audioEl)` immediately creates `new AudioContext()` + `createMediaElementSource(audioEl)` in **`"suspended"`** state. Later, `startTrackPlayback()` awaits `api.getStreamInfo()` (`0.5–4s`, expiring user activation) before calling `play()`, and `resumeEqContext()` inside the `'play'` media event is rejected by browser autoplay policy — causing `currentTime` to advance (`0:01 -> 0:06`) with **100% silent speakers**.
   - Additionally, `applyEqSettings()` (`player.js:240`) attaches `createMediaElementSource(audioEl)` **before** switching `audioEl.src` from cross-origin `directUrl` to same-origin `activeProxyUrl`, and returns early without calling `resumeEqContext()`.
8. **Bug 3B/3D/3E — Desktop Chromium Experimental HLS Quirks, Crossfade Volume Stuck at `0.02`, & Blind Alternate-Track Swap (`player.js`)**:
   - **3B**: Modern Desktop Chromium returns `"maybe"` for `canPlayType('application/vnd.apple.mpegurl')`, routing SoundCloud `hlsUrl` (`playlist.m3u8` + `.m4s`) into Chromium's experimental HLS demuxer instead of Sonify's pre-warmed seekable `/api/stream/{id}` byte-range proxy.
   - **3D**: During crossfade's final `fadeSec` seconds (`player.js:344–352`), `audioEl.volume` ramps down to `0.02`. If the user seeks backward, pauses, switches to `Repeat One`, or turns Crossfade off, `audioEl.volume` is **never restored** and stays near-muted (`0.02–0.15`)!
   - **3E**: `player.js:494` (`const alt = searchRes.tracks.find(t => t.id !== track.id)`) performs **zero title, artist, or duration verification**, silently playing a different song while displaying the original song's title, artwork, and lyrics!
9. **Bug 4B/4C — iOS `AVPlayer.play()` Resetting `playbackRate` to `1.0x` & `OfflineDownloadManager` Missing Proxy-Failure Fallback + Magic-Byte MIME Verification (`AudioPlayerService.swift`, `OfflineDownloadManager.swift`)**:
   - `AVPlayer.play()` resets `rate` to `defaultRate` (`1.0`) whenever `automaticallyWaitsToMinimizeStalling` resumes after buffering unless `player.defaultRate` is also set.
   - `OfflineDownloadManager.downloadTrack()` falls back to `YouTubeDirectResolver` when `getStreamInfo` throws `502`, **but NOT when `getStreamInfo` succeeds and `bridge.download(from: proxyURL)` subsequently fails with `502`**, nor does it sniff downloaded magic bytes before choosing `.flac` vs `.m4a` on disk.

---

## 2. Exact Surgical Fixes by File

### Pillar 1 — Zero `/info` ↔ `/stream` Mismatch & Fast Monochrome Failover

#### 1.1 `backend/providers/monochrome.py`
- **Fast initial `429` failover in `_run_producer()` (eliminates 10.3s stall)**:
  - Split initial-connect (`not is_resume and total_written == 0`) retry budget from mid-stream resume (`is_resume`) budget:
    - When `not is_resume`: allow **at most 1 fast retry** (`0.75s` sleep on `429` or transient socket error, NOT `1.0s + 2.0s + 3.5s = 6.5s`). If the retry also returns `429`/error, mark `entry.failed = True` immediately (`~1.5–2.0s` total instead of `10.3s`).
    - When `is_resume` (`total_written > 0`): keep `backoffs = (1.0, 2.0, 3.5)` and `MAX_RESUME_ATTEMPTS = 8` so large Hi-Res FLAC mid-stream resumes remain rock-solid.

#### 1.2 `backend/services/stream_service.py` & `backend/server.py`
- **Support `?refresh=1` on `GET /api/stream/{id}/info` (`backend/server.py`, `StreamService.get_stream_info`)**:
  - Add `force_refresh: bool = False` parameter to `StreamService.get_stream_info(...)` (wired to `refresh = self._qparam(qs, "refresh", "0") in ("1", "true", "yes")` in `backend/server.py`), passing `force_refresh=force_refresh` into `self.resolve_stream(...)`.
- **Verify Monochrome First-Chunk Readiness Inside `get_stream_info()` Before Returning `monochrome` / `.flac`**:
  - Keep existing `with monochrome._ACTIVE_LOCK: has_active_mc = len(monochrome._ACTIVE_DOWNLOADS) > 0` and `should_prewarm = prewarm or not has_active_mc` (preserving `test_monochrome_concurrent_producer_cap_and_conditional_prewarm` in `tests/test_zero_preview_elimination.py`!).
  - When `mc_id and should_prewarm`:
    ```python
    handle = None
    try:
        handle = monochrome.start_background_cache(mc_id)
    except Exception:
        handle = None

    # For active playback /info requests (not background ?prewarm=1 prefetch),
    # wait up to 3.2s for the real ProgressiveCacheEntry to confirm 200/206 readiness.
    # (Note: isinstance check preserves unit tests that mock start_background_cache -> MagicMock/None.)
    if not prewarm and isinstance(handle, monochrome.ProgressiveCacheEntry):
        is_ready = handle.wait_until_ready(timeout=3.2)
        if is_ready and handle.content_type:
            resolved.content_type = handle.content_type
        elif handle.failed or (handle.done and handle.bytes_written < monochrome.MIN_TRACK_BYTES) or handle.bytes_written == 0:
            handle.cancel_event.set()
            self.invalidate(track_id)
            resolved = self.resolve_stream(
                track_id,
                force_refresh=True,
                exclude_sources={"monochrome"},
                quality=quality,
            )
            mc_id = _extract_monochrome_id(resolved)
    ```
  - **Recompute** `norm_mime = normalize_stream_mime(resolved.content_type)` and `ext = mime_to_extension(norm_mime)` **after** the readiness check (and run `sc_m3u8` pre-warm if `resolved` cascaded to `soundcloud`!).
  - **Result**:
    - When Monochrome is healthy (`mc:164376983856218112`), `/info` returns in `~0.8–1.1s` with 64 KiB already buffered on disk (`0ms` `/stream` TTFB) and magic-byte-verified `contentType`.
    - When Monochrome `429`s/404s/stalls, `/info` immediately cascades within `~1.8–3.2s` and returns the **true working fallback** (`streamSource`, `contentType`, `streamUrlWithExt`, `hlsUrl`/`directUrl`) so `/info` and `/stream` **NEVER disagree**!
- **Cap Cold Wait in `_serve_progressive_monochrome()` (`stream_service.py:1568`)**:
  - Replace `handle.wait_until_ready(timeout=20.0)` with:
    - `ready_timeout = 4.5 if handle.bytes_written == 0 else 12.0`
    - `handle.wait_until_ready(timeout=ready_timeout)`
    - If `not (handle.ready or handle.done)` and `handle.bytes_written == 0`, set `handle.cancel_event.set()` and return `False` so `/stream` never hangs 20 seconds on a dead connection.

---

### Pillar 2 — Strict Song Identity, Asymmetric Duration, & Anti-Remix/Chopped/Cover Gates

#### 2.1 `backend/lrc_parser.py` (Shared Canonical Matching Primitives)
1. **Fix `token_similarity(a, b)` Word-Boundary & Minimum-Length Guard**:
   ```python
   def _has_cjk(text: str) -> bool:
       return any(ord(ch) >= 0x2E80 for ch in text)

   def token_similarity(a: str, b: str) -> float:
       """Compute a fast normalized similarity score in [0.0, 1.0] without short-substring false positives."""
       na = normalize_key(a)
       nb = normalize_key(b)
       if not na or not nb:
           return 0.0
       if na == nb:
           return 1.0
       shorter = min(len(na), len(nb))
       longer = max(len(na), len(nb))
       pa, pb = f" {na} ", f" {nb} "
       if shorter >= 3 and (pa in pb or pb in pa):
           return 0.8 + 0.2 * (shorter / longer)
       if (_has_cjk(na) or _has_cjk(nb)) and shorter >= 2 and (na in nb or nb in na):
           return 0.8 + 0.2 * (shorter / longer)

       ta = set(na.split())
       tb = set(nb.split())
       if not ta or not tb:
           return 0.0
       inter = len(ta & tb)
       union = len(ta | tb)
       jaccard = inter / union
       overlap = inter / min(len(ta), len(tb))
       return 0.55 * jaccard + 0.45 * overlap
   ```
2. **Add Shared `is_duration_compatible(...)` (Asymmetric Duration Physics)**:
   - Official MVs/extended cuts can be **longer** (`+15..+65s`), whereas candidates **shorter** by `> 10–16s` (`> 5–6%`) are chopped clips (`1:59` for `2:57`) or sped-up copyright-dodge uploads (`4:20` for `4:39`):
   ```python
   def is_duration_compatible(
       cand_dur: int,
       target_duration: int = 0,
       relaxed: bool = False,
       mv_capable: bool = False,
   ) -> bool:
       if not is_valid_full_duration(cand_dur, target_duration):
           return False
       if cand_dur <= 0:
           return True
       if cand_dur > 720:
           return False
       if target_duration <= 30:
           return cand_dur >= (60 if relaxed else 75)
       if target_duration < 60:
           return abs(cand_dur - target_duration) <= (20 if relaxed else 12)

       delta = cand_dur - target_duration
       if not relaxed:
           max_shorter = max(10, int(target_duration * 0.05))
           max_longer = (
               max(35, int(target_duration * 0.20))
               if mv_capable
               else max(18, int(target_duration * 0.12))
           )
       else:
           max_shorter = max(12, int(target_duration * 0.06))
           max_longer = max(65, int(target_duration * 0.40))
       return (-max_shorter) <= delta <= max_longer
   ```
   - *Verified against all repros & tests*:
     - `119s` vs `177s` (*Như Một Người Rưng* `1:59` DJ cut): `False` in Pass 1 & Pass 2!
     - `180s` vs `177s` (*Như Một Người Rưng* `3:00` Bilibili studio): `True` in Pass 1!
     - `260s` vs `279s` (*Đừng Làm Trái Tim Anh Đau* `4:20` sped-up): `False` in Pass 1 & Pass 2!
     - `225s` vs `192s` (*Come My Way* MV in `test_stream_and_waterfall.py`): `True` in Pass 1 (`mv_capable=True`)!
     - `200s` vs `155s` (*Super Shy* HYBE MV in `test_zero_preview_elimination.py`): `False` in Pass 1, `True` in Pass 2!

3. **Add Shared `classify_unwanted_version(cand_text, target_text)` Gate in `lrc_parser.py`**:
   - Normalize both with `normalize_key()` (catches Unicode Fraktur `𝖗𝖊𝖒𝖎𝖝` -> `remix`) plus raw lowercase CJK checks:
   - **Hard-Reject in BOTH Pass 1 and Pass 2** (returns `"hard_reject"` when present in `cand_text` and absent from `target_text`):
     - `"sped up"`, `"speed up"`, `"slowed"`, `"slower"`, `"nightcore"`, `"daycore"`, `"8d"`, `"chopped"`, `"screwed"`, `"karaoke"`, `"instrumental"`, `"backing track"`, `"off vocal"`, `"mashup"`, `"bootleg"`, `"vinahouse"`, `"nonstop"`, `"megamix"`, `"cha cha"`, `"chachacha"`, `"nhac song"`, `"freedownload"`, `"free download"`, `"ringtone"`, `"snippet"`, `"teaser"`, `"preview"`, `"demo"`, `"trailer"`, `"parody"`, `"reaction"`, `"1 hour"`, `"10 hours"`, plus CJK `"伴奏"`, `"纯音乐"`, `"教学"`.
   - **Derivative-Reject in Pass 1 / Heavy `-0.35` Penalty in Pass 2** (returns `"derivative"` when present in `cand_text` and absent from `target_text`):
     - `"remix"`, `"cover"`, `"翻唱"`, `"rework"`, `"flip"`, `"vip mix"`, `"club mix"`, `"lofi"`, `"lo fi"`, `"piano"`, `"guitar"`, `"violin"`, `"cinematic"`, `"orchestral"`, `"lullaby"`, `"tribute"`, `"hop xuong"`, `"beat"`, `"dj"`, `"edit"` (ignore if `"radio edit"`), `"mix"` (ignore if `"original mix"`).
     - In **Pass 1 (`relaxed=False`)**: `"derivative"` -> **immediately return `-1.0`**! (Guarantees Pass 1 NEVER returns a remix, club edit, Cha Cha Cha version, piano version, or cover when an authentic track exists on Monochrome, JioSaavn, or Bilibili!)
     - In **Pass 2 (`relaxed=True`)**: `"hard_reject"` STILL returns `-1.0`; `"derivative"` applies a full **`-0.35`** penalty (NOT `0.25x`!), while `"live"` / `"acoustic"` applies `-0.12` so genuine live/acoustic performances can still rescue rare tracks without covers/remixes hijacking common songs.

#### 2.2 Apply Across All 5 Backend Providers (`soundcloud.py`, `monochrome.py`, `bilibili.py`, `jiosaavn.py`, `audius.py`) + `youtube_music.py`
1. **`backend/providers/soundcloud.py`**:
   - In `_UPLOAD_JUNK_RE` (line 35): **remove** `free\s*download|download|` so `"Free download"` / `"FREEDOWNLOAD"` is never stripped before `classify_unwanted_version()` rejects it!
   - In `is_full_allow_track(item, target_duration=0, relaxed=False)`: replace the old `max_diff = max(140, int(target_duration * 0.75))` and `relaxed: dur_sec <= 900` with `lrc_parser.is_duration_compatible(dur_sec, target_duration, relaxed=relaxed, mv_capable=True)` (and keep `dur_ms >= 90_000` when `not relaxed and (target_duration == 0 or target_duration >= 90)`).
   - In `score_candidate(...)`:
     - Enforce `lrc_parser.classify_unwanted_version(f"{raw_title} {username}", target_context)`: return `-1.0` on `"hard_reject"` (both passes) and on `"derivative"` when `not relaxed`.
     - **Close the zero-artist loophole (lines 356–371)**: remove the `abs(dur_sec - target_duration) <= 45` zero-artist boost! When `target_has_artist` is True and `artist_sim < 0.40`:
       - In Pass 1 (`not relaxed`): return `-1.0`.
       - In Pass 2 (`relaxed=True`): only accept if `artist_sim >= 0.30` OR `lrc_parser.has_primary_artist_token(primary_artist, haystack)` OR (`len(norm_clean_title) >= 12 and len(norm_clean_title.split()) >= 3 and title_sim >= 0.95 and target_duration > 60 and dur_sec > 0 and abs(dur_sec - target_duration) <= 5`). Otherwise return `-1.0`!
2. **`backend/providers/monochrome.py`, `bilibili.py`, `jiosaavn.py`, `audius.py`**:
   - Enforce `lrc_parser.is_duration_compatible(cand_dur, target_duration, relaxed=relaxed, mv_capable=(provider == "bilibili"))` in both Pass 1 and Pass 2 (`monochrome` exact ISRC match in Pass 1 still allows `is_duration_compatible(..., relaxed=False, mv_capable=False)` or `<= 20s`).
   - Enforce `lrc_parser.classify_unwanted_version(...)` (`"hard_reject"` -> `-1.0` / `continue` in both passes; `"derivative"` -> `-1.0` / `continue` in Pass 1, `-0.35` penalty in Pass 2).
   - Remove the `title_sim >= 0.85 and abs(cand_dur - target_duration) <= 45` zero-artist bypass in `monochrome.py:730–736`, `bilibili.py:289–291`, `jiosaavn.py:369–371`, and `audius.py:248–250` (replace with the same strict multi-word `>= 3 tokens`, `title_sim >= 0.95`, `abs(delta) <= 5s` rule in `relaxed=True` only).
   - Raise Pass-2 `title_sim` floor from `0.52` to `0.60` so title-only rescue queries never match loosely overlapping titles.
3. **`backend/providers/youtube_music.py`**:
   - In `find_best_video_id()`: use `lrc_parser.is_duration_compatible(cand_dur, duration, relaxed=False, mv_capable=True)` and `lrc_parser.classify_unwanted_version(...)`.

---

### Pillar 3 — Web/PWA Audio Engine (`frontend/js/audioEffects.js`, `frontend/js/player.js`, `frontend/js/main.js`)

#### 3.1 `frontend/js/audioEffects.js` & `frontend/js/main.js` — Eliminate Suspended `AudioContext` Silence
- **Never attach `createMediaElementSource` outside an active user gesture or while `AudioContext` is suspended**:
  - In `audioEffects.js`:
    - Add `let gestureUnlocked = false;`.
    - Add `export function unlockAudioContextFromGesture(audioEl = null)`:
      - Marks `gestureUnlocked = true`.
      - If `audioCtx && audioCtx.state === 'suspended'`, calls `audioCtx.resume().catch(() => {})`.
      - If `!isEqGraphAttached() && isEqNonFlat(currentBands) && audioEl`, attaches `ensureEqGraph(audioEl)` **right inside the synchronous user gesture** and immediately calls `audioCtx.resume().catch(() => {})`!
    - In `applyEqBands(audioEl, bands)`:
      - Always update `currentBands`.
      - If `!isEqGraphAttached()`:
        - If `!isEqNonFlat(currentBands)` -> `return;`
        - Check `const hasGesture = gestureUnlocked || Boolean(navigator.userActivation?.isActive);`
        - **If `!hasGesture`, DO NOT call `ensureEqGraph(audioEl)` yet** — return cleanly so `bootstrap()` -> `syncAudioSettings()` (`main.js:516`) NEVER locks `#audio-engine` into a suspended `AudioContext` on page load! As soon as the user clicks Play / presses Space, `unlockAudioContextFromGesture(audioEl)` runs synchronously and attaches + resumes the EQ graph with audible output!
- **Register Gesture Unlockers & Synchronous Pre-`await` Unlock in `frontend/js/player.js`**:
  - In `initPlayer()`: register passive capture listeners on `document` for `'pointerdown'`, `'touchend'`, and `'keydown'` that call `unlockAudioContextFromGesture(audioEl)` when `isEqGraphAttached() || isEqNonFlat(getState().eqBands)` (and always resume a suspended `audioCtx`).
  - At the **very top** of `playTrack()`, `playList()`, `togglePlay()`, `playNextTrack()`, `playPrevTrack()`, `jumpToQueueIndex()`, `playFromUserQueue()`, and `applyEqSettings()` — **synchronously BEFORE any `await`** — call `unlockAudioContextFromGesture(audioEl)`.
  - In `applyEqSettings(bands, preset)` (`player.js:235–260`): when switching `activeUsingDirect` -> `activeProxyUrl` for non-flat EQ, set `audioEl.src = activeProxyUrl` **BEFORE** calling `applyEqBands(audioEl, normalized)` and call `unlockAudioContextFromGesture(audioEl)` + `resumeEqContext()` before returning!

#### 3.2 `frontend/js/player.js` — Restrict Raw `hlsUrl` to Native Apple WebKit/Safari
- Replace line 558:
  ```js
  const ua = navigator.userAgent || '';
  const isAppleNativeHls =
    Boolean(audioEl.canPlayType && audioEl.canPlayType('application/vnd.apple.mpegurl')) &&
    (/iPad|iPhone|iPod/.test(ua) || (/Safari/i.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Firefox/i.test(ua)));
  ```
  - Use `info?.hlsUrl` ONLY when `isAppleNativeHls` is `true`!
  - On Desktop Chrome / Chromium / Edge / Firefox / Brave / Android Chrome, SoundCloud tracks automatically play via `proxyUrl` (`/api/stream/{id}?s={seq}`), which serves Sonify's pre-warmed assembled `audio/mp4` with exact `Content-Length`, `Accept-Ranges: bytes`, instant scrubbing, Web Audio EQ support, and zero Chromium HLS demuxer bugs!

#### 3.3 `frontend/js/player.js` — Fix Crossfade Stuck Volume (`isCrossfadeRampingOut`)
- Add module state `let isCrossfadeRampingOut = false;`.
- Reset `isCrossfadeRampingOut = false` in `startTrackPlayback()` (and ensure `syncAudioSettings()` always sets `audioEl.volume` from `st.volume` before optional `startFadeIn()`).
- In `timeupdate` (`player.js:333–354`):
  ```js
  let cfActive = false;
  if (
    cf > 0 &&
    !audioEl.paused &&
    cur.repeat !== 'one' &&
    cur.sleepTimer?.mode !== 'end-of-track' &&
    dur > 15 &&
    hasUpcomingTrack(cur)
  ) {
    const fadeSec = Math.min(cf, 6);
    const rem = dur - curTime;
    if (rem > 0 && rem <= fadeSec && !cur.muted) {
      cfActive = true;
      isCrossfadeRampingOut = true;
      cancelFadeIn();
      const baseVol = Math.max(0, Math.min(1, Number(cur.volume) || 0));
      audioEl.volume = Math.max(0.05, baseVol * (rem / fadeSec));
      if (rem <= 0.45 && crossfadeTriggeredForSeq !== playbackSeq) {
        crossfadeTriggeredForSeq = playbackSeq;
        playNextTrack(true, true);
      }
    }
  }
  if (!cfActive && isCrossfadeRampingOut) {
    isCrossfadeRampingOut = false;
    audioEl.volume = cur.muted ? 0 : Math.max(0, Math.min(1, Number(cur.volume) || 0));
  }
  ```
- Also in `ended` -> `if (cur.repeat === 'one' && cur.currentTrack)` (`player.js:371`) and in `seekTo()` (`player.js:754`): reset `isCrossfadeRampingOut = false` and restore `audioEl.volume` if `!fadeInRaf`.

#### 3.4 `frontend/js/player.js` — Strict Identity Verification on 1-Shot Alternate-Track Rescue & Badge Sync
- Replace the blind `.find((t) => t && t.id && t.id !== track.id)` at line 494 with `isSameSongCandidate(t, track)`:
  - Normalize strings (`s.replace(/[đĐ]/g, 'd').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9\u00c0-\uffff\s]/g, ' ').trim()`), strip `(feat...)` / `(official...)`.
  - Reject if `alt.title` contains unrequested `remix`, `cover`, `karaoke`, `instrumental`, `sped up`, `speed up`, `slowed`, `slower`, `nightcore`, `mashup`, `edit`, `live` not present in `track.title`.
  - Require normalized title token overlap `>= 0.80`, primary artist match (shared primary artist token of len `>= 3` or exact match), and asymmetric duration compatibility (`alt.duration >= track.duration - 12 && alt.duration <= track.duration + 35` when both `> 30`).
- In `attemptProxyFallback(seq)`: after falling back from direct/HLS to `activeProxyUrl`, fire a non-blocking `api.getStreamInfo(curTrack.id, undefined, getState().audioQuality || 'auto', false)` to keep `streamSource` / `streamMode` in `state` accurate if the backend cascaded.

---

### Pillar 4 — Native SwiftUI iOS (`AudioPlayerService.swift`, `OfflineDownloadManager.swift`, `YouTubeDirectResolver.swift`, `APIClient.swift`)

#### 4.1 `ios/Sonify/Services/AudioPlayerService.swift` & `APIClient.swift`
1. **Never Force `AVURLAssetOverrideMIMETypeKey` on `.proxy` Routes (`AudioPlayerService.swift:482–486`)**:
   ```swift
   if !route.isHLS && !route.isLocal && !route.isProxy {
       if #available(iOS 17.0, *) {
           assetOptions[AVURLAssetOverrideMIMETypeKey] = StreamInfoResponse.normalizedMIME(contentType)
       }
   }
   ```
   - Keeps `AVURLAssetHTTPHeaderFieldsKey` intact (preserving `test_universal_full_track.py:379`!) and keeps `AVURLAssetOverrideMIMETypeKey` for `.direct(...)` routes (e.g. `YouTubeDirectResolver` `itag=18` `video/mp4`), while allowing `.proxy(...)` routes to honor the backend HTTP response's actual `Content-Type` header (`audio/flac` or `audio/mp4`) even on mid-request fallback!
2. **Add `refresh: Bool = false` to `APIClient.getStreamInfo(...)` + Proxy Re-Resolve Fallback**:
   - In `APIClient.swift`: add default parameter `refresh: Bool = false` appending `URLQueryItem(name: "refresh", value: "1")` when `true`.
   - In `AudioPlayerService.swift`: add `private var didRetryFreshProxy: Bool = false` (reset in `startPlayback`). In `observePlayerItem` `.failed`, if `route.isProxy` failed, try `YouTubeDirectResolver.shared.resolveFullTrack(for: track)` first (existing residential-IP fast path), and if `YouTubeDirectResolver` returns `nil` and `!self.didRetryFreshProxy`, set `didRetryFreshProxy = true` and retry `api.getStreamInfo(trackId: track.id, quality: qual, refresh: true)` once before surfacing an error!
3. **Preserve Custom `playbackRate` Across Stalls & Track Changes**:
   - Add helper `private func playAtConfiguredRate()`:
     ```swift
     private func playAtConfiguredRate() {
         player.defaultRate = playbackRate
         player.playImmediately(atRate: playbackRate)
     }
     ```
     Wait: on `AVPlayer` with `automaticallyWaitsToMinimizeStalling = true`, calling `player.defaultRate = playbackRate` followed by `player.play()` (and `if abs(playbackRate - 1.0) > 0.01 { player.rate = playbackRate }`) preserves stall-minimizing buffering AND ensures `AVPlayer` resumes at `playbackRate` instead of resetting to `1.0x`! Use `player.defaultRate = playbackRate` in `setPlaybackRate(_:)`, `resume()`, `startPlayback(for:)`, and `observePlayerItem` recovery paths.

#### 4.2 `ios/Sonify/Services/OfflineDownloadManager.swift`
1. **Fallback to `YouTubeDirectResolver` When Backend Proxy Download Fails + Sniff Actual Magic Bytes**:
   - In `downloadTrack(_:api:quality:)`:
     - Build an ordered list of download candidates: if `backendInfo` returned a non-preview `proxyURL`, try `bridge.download(from: proxyURL, ...)` inside a `do/catch`; if `bridge.download` throws (e.g., HTTP 502/timeout), automatically resolve `YouTubeDirectResolver.shared.resolveFullTrack(for: track)` and download from `yt.url` with `yt.headers`!
     - Enforce minimum downloaded file size `>= 350_000` bytes on network downloads (`OfflineDownloadError.emptyPayload` if smaller, preventing truncated error bodies from being saved as offline tracks; note: `storeOfflineAsset` test helper stays untouched so lightweight XCTest synthetic Data fixtures continue passing!).
     - Add `static func sniffAudioMIME(head: Data, fallback: String) -> String` (`fLaC` -> `"audio/flac"`, `ftyp` at bytes `4..<8` -> `"audio/mp4"`, `ID3` or `0xFF 0xE0` -> `"audio/mpeg"`, `RIFF....WAVE` -> `"audio/wav"`). After `bridge.download` writes to a temporary destination, inspect the first 16 bytes: if the sniffed MIME's extension differs from the initial `audioFilename` extension (e.g. `.flac` -> `.m4a`), rename the file to the true extension and store the true `contentType` in `DownloadedTrackRecord` so Airplane-Mode `AVPlayer` playback NEVER encounters an extension/codec mismatch!

#### 4.3 `ios/Sonify/Services/YouTubeDirectResolver.swift`
- Mirror Pillar 2's exact guards in `scoreCandidate(...)`:
  - **Asymmetric duration**: when `targetDuration >= 60 && cand.duration > 0`:
    - Pass 1 (`!relaxed`): require `cand.duration >= targetDuration - max(10, Int(Double(targetDuration) * 0.05))` and `cand.duration <= targetDuration + max(35, Int(Double(targetDuration) * 0.20))`.
    - Pass 2 (`relaxed`): require `cand.duration >= targetDuration - max(12, Int(Double(targetDuration) * 0.06))` and `cand.duration <= min(720, targetDuration + max(65, Int(Double(targetDuration) * 0.40)))`.
  - **Hard-reject alteration keywords** in BOTH Pass 1 and Pass 2 (unless present in target): `"sped up"`, `"speed up"`, `"slowed"`, `"slower"`, `"nightcore"`, `"8d"`, `"karaoke"`, `"instrumental"`, `"mashup"`, `"reaction"`, `"1 hour"`, `"ringtone"`, `"snippet"`, `"teaser"`, `"demo"`, `"parody"`.
  - **Reject derivative keywords in Pass 1 / `-0.35` penalty in Pass 2** (unless present in target): `"remix"`, `"cover"`, `"piano"`, `"lofi"`, `"lo fi"`, `"edit"` (unless `"radio edit"`).
  - **Close zero-artist `±45s` loophole**: in `relaxed` mode, replace `closeTitleAndDur` (`abs <= 45`) with strict multi-word `>= 3` tokens, `titleSim >= 0.95`, and `cand.duration > 0 && targetDuration > 60 && abs(cand.duration - targetDuration) <= 5`.

---

## 3. QA & Acceptance Criteria (`tests/test_playback_correctness.py` + `ios/SonifyTests/`)

1. **Zero Existing Test Regressions**:
   - All `96/96` existing Python tests (`python3 -m unittest discover -s tests -v`) MUST pass (`0` failures), and all 31 Swift files under `ios/` MUST remain brace/import-valid with Apple-only frameworks.
2. **New Automated Suite `tests/test_playback_correctness.py`**:
   - **Suite 1 (`/info` ↔ `/stream` Codec & Source Consistency)**:
     - Simulate Monochrome `429` / 0-byte stall on `/track/{id}`: verify `get_stream_info()` fails fast (`< 3.5s`), invalidates Monochrome, and returns `streamSource: "soundcloud"` (or `"bilibili"`), `contentType: "audio/mp4"`, `streamUrlWithExt: "/api/stream/{id}.m4a"` — NEVER returning `.flac` when Monochrome is unreachable!
     - Simulate healthy Monochrome `64 KiB` `fLaC` head: verify `get_stream_info()` returns `streamSource: "monochrome"`, `contentType: "audio/flac"` in `< 1.0s` and subsequent `/api/stream/{id}` serves `206` immediately.
   - **Suite 2 (`token_similarity`, Asymmetric Duration, & Anti-Remix/Chopped/Cover Gates)**:
     - `lrc_parser.token_similarity("a", "osad") == 0.0` and `token_similarity("den", "golden hour") == 0.0`, while `token_similarity("Đen", "Đen, MIN") > 0.85` and CJK/Hangul exact/phrase matches (`"아이유 밤편지"`, `"周杰伦"`) score `>= 0.88`.
     - Verify `119s` (`1:59` `AYDEN EDIT`) for a `177s` (`2:57` *Như Một Người Rưng*) track scores `< 0` in BOTH Pass 1 and Pass 2, and `260s` (`4:20` sped-up) for a `279s` (`4:39` *Đừng Làm Trái Tim Anh Đau*) track scores `< 0` in BOTH Pass 1 and Pass 2, while `180s` Bilibili studio match (`OSAD × HÀ AN HUY | NHƯ MỘT NGƯỜI RƯNG`) scores `>= 0.90` and wins!
     - Verify generic title `"Stay"` (`141s`, artist `"The Kid LAROI"`) NEVER matches `"Stay"` (`150s`, artist `"Random Stranger"`) in Pass 1 or Pass 2 (`score < 0`).
   - **Suite 3 (Web `audioEffects.js` + `player.js` Static & Logic Audit)**:
     - Verify `audioEffects.js` exports `unlockAudioContextFromGesture` and defers `ensureEqGraph` when `!hasGesture` (never attaching suspended `AudioContext` on cold page bootstrap).
     - Verify `player.js` restricts `hlsUrl` to Apple WebKit/Safari, restores `audioEl.volume` via `isCrossfadeRampingOut` when seeking/looping, and validates `isSameSongCandidate` before using an alternate-track search result.
   - **Suite 4 (iOS `AudioPlayerService.swift`, `OfflineDownloadManager.swift`, `YouTubeDirectResolver.swift`)**:
     - Verify `AudioPlayerService.swift` does NOT set `AVURLAssetOverrideMIMETypeKey` when `route.isProxy` is true, sets `player.defaultRate = playbackRate`, and supports `refresh: true` fallback.
     - Verify `OfflineDownloadManager.swift` sniffs downloaded magic bytes (`fLaC` vs `ftyp`) and falls back to `YouTubeDirectResolver` if proxy `bridge.download` throws.
     - Add unit tests in `ios/SonifyTests/YouTubeDirectResolverTests.swift` verifying asymmetric duration rejection (`119s` for `177s` rejected) and generic-title wrong-artist rejection.
3. **Live `RENDER=true` End-to-End Verification on Exact Repro Tracks**:
   - Verify `it:1749963740` (*Đừng Làm Trái Tim Anh Đau* — Sơn Tùng M-TP, `279s`) resolves cleanly to verified `279s` Lossless FLAC (`monochrome`, `206`, `fLaC` magic bytes, `< 2s` TTFB, matching `/info` and `/stream` MIME 1:1).
   - Verify `it:6827404203` (*Như Một Người Rưng* — OSAD & Hà An Huy, `177s`) rejects the `1:59` (`119s`) SoundCloud DJ edit and resolves cleanly to the authentic `~180s` full track (`bilibili`, `206`, `audio/mp4` `ftyp` magic bytes, matching `/info` and `/stream` MIME 1:1).
