# Sonify — Spotify-Like 100% Ad-Free Music Streaming Web App

A polished, zero-install, **100% ad-free** Spotify-inspired music streaming web application with live global & Vietnam/V-Pop catalogs, resilient multi-source full-track audio streaming proxy (HTTP `Range` / `206 Partial Content`), real-time synced `.lrc` lyrics, custom playlists, liked songs, play history, and queue management.

---

## Quick Start (Zero-Install Local Run)

Requires only **Python 3.10+** (tested on Python 3.14; zero `npm install` and zero `pip install` needed).

```bash
cd "/mnt/e/lam lai tu dau/music"
python3 app.py
```

Open your browser at:

**http://127.0.0.1:8080/**

### CLI Options & Environment Variables

```bash
# Custom host / port
python3 app.py --host 0.0.0.0 --port 8080

# Custom SQLite database file
python3 app.py --db data/music.db

# Force Offline Showcase mode (curated local catalog, synced lyrics, studio stereo WAV synth)
python3 app.py --offline
```

Supported environment variables: `SONIFY_HOST`, `SONIFY_PORT`, `PORT` (standard cloud port; auto-binds `0.0.0.0` when `PORT` is set), `SONIFY_DB`, `SONIFY_OFFLINE=1`.

*(Optional)* If [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) is available on your `PATH`, Sonify automatically detects and uses it inside the YouTube Music streaming tier for even higher full-track stream coverage.

---

## Deploy Free ($0) on Render (via GitHub — No Credit Card)

This repository includes a Render Blueprint (`render.yaml`) and production `Dockerfile` (`PORT=10000`, non-root `UID 1000`, `/api/health` health check, `SIGTERM` graceful shutdown, `yt-dlp` + `cryptography` pre-installed, and browser `localStorage` auto-sync across free-tier restarts):

### Option A — One-Click Blueprint (`render.yaml`)
1. Push this repository (`main` branch) to GitHub.
2. Open [dashboard.render.com](https://dashboard.render.com/) -> **New +** -> **Blueprint** -> connect your GitHub repo.
3. Render automatically detects `render.yaml` (`sonify-music`, Docker, **Free** plan, **`region: singapore`**, `/api/health`) — click **Apply** to deploy live.

### Option B — Manual Web Service
1. On [dashboard.render.com](https://dashboard.render.com/), click **New +** -> **Web Service** -> select your GitHub repo.
2. Choose **Language / Runtime: Docker**, **Region: Singapore (Southeast Asia)**, **Instance Type: Free ($0/month)**, and **Health Check Path: `/api/health`**.
3. Click **Create Web Service** — Render builds `Dockerfile` automatically and binds dynamically to `$PORT` (default `10000`).

> **Region, Speed & Keep-Alive Note (Important for Existing Oregon Services):**
> `render.yaml` specifies `region: singapore` because SoundCloud Go+ 30-second `SNIP` paywalls apply in the US (Oregon) but **not** in Singapore, unlocking full-track coverage for brand-new Global/V-Pop releases absent from Monochrome and cutting Southeast Asia latency ~6x (`~220ms -> ~35ms`). Render does **not** migrate an existing live service across regions in-place when `render.yaml` changes — to switch an existing US-Oregon service to Singapore, create a fresh Render Blueprint/Web Service in **Singapore** (~2 minutes on Free tier), **or** keep your existing region and set optional `YTDLP_COOKIES_B64` (base64 Netscape cookies) or `YTDLP_PROXY` in Render Environment Variables for 100% YouTube Music catalog coverage.
> **Prevent Render Free 15-Minute Sleep (`22.5s` cold wake):** Point a free external uptime monitor (e.g. UptimeRobot or cron-job.org) to `GET https://<your-app>.onrender.com/api/health` every **14 minutes**. You can inspect live provider traces via `GET /api/stream/{trackId}/info?debug=1` and deployment flags via `GET /api/health`.

---

## iOS App (Native SwiftUI + App Store / TestFlight + iOS PWA)

### 1. Share & Direct iPhone Install (From the Website)
Sonify includes an **Install App** button (`#btn-install-app`), a **Share** button (`#btn-share-app`, using native iOS Web Share API with clipboard fallback), and a sidebar **Install on Phone** shortcut right on the website:
- **Direct iPhone Profile Install (Free, No PC, Never Expires)**: Tapping **Install Directly to iPhone** downloads `GET /api/ios/install.mobileconfig` (Apple `com.apple.webClip.managed` profile with Sonify's icon and full-screen mode) -> **Allow** -> **Settings -> Profile Downloaded -> Install**.
- **2-Tap Safari PWA**: Or in Safari tap **Share -> Add to Home Screen** (`frontend/manifest.json`, 180×180 / 192×192 / 512×512 RGB icons, notch safe-area CSS, static-shell `sw.js`).
- **Optional Direct Native iOS Distribution (Render Environment Variables)**:
  - `SONIFY_TESTFLIGHT_URL`: set your public `https://testflight.apple.com/join/...` link to enable a **1-Tap Install via TestFlight** button inside the website Install modal.
  - `SONIFY_IOS_IPA_URL`: set an HTTPS URL to a signed `.ipa` to enable Apple OTA `itms-services://` (`GET /api/ios/manifest.plist`) and populate `GET /api/ios/altstore-source.json`.
  - `SONIFY_IOS_BUNDLE_ID` (optional, default `com.sonify.music`) & `SONIFY_GITHUB_REPO` (optional, default `vinhtran211/sonify`).

### 2. Native SwiftUI iOS App (`ios/`, iOS 16.0+, Zero Third-Party Dependencies)
A complete Spotify-like native SwiftUI iOS application lives in `ios/`:
- **Native Playback & Lock Screen**: `AVPlayer` / `AVURLAsset` with extension-hinted streams (`/api/stream/{id}.{ext}` + iOS 17+ `AVURLAssetOverrideMIMETypeKey`), `AVAudioSession(.playback)` background audio, headphone-unplug & call interruption handling, `MPNowPlayingInfoCenter`, `MPRemoteCommandCenter`, and AirPlay (`AVRoutePickerView`).
- **Full Feature Set**: Home (Global / Vietnam charts), debounced Search, Artist/Album/Playlist/Likes/History details, floating Mini Player, full-screen Now Playing, real-time synced `.lrc` Lyrics with tap-to-seek, reorderable Queue, and Settings (defaults to `https://sonify-music.onrender.com`, or point to your local LAN server via `NSAllowsLocalNetworking`).
- **App Store Ready**: Includes `PrivacyInfo.xcprivacy` (`NSPrivacyTracking=false`, `UserDefaults` `CA92.1`), `Info.plist` (`UIBackgroundModes:[audio]`, `ITSAppUsesNonExemptEncryption=false`), `1024×1024` RGB-only `AppIcon.png` (zero alpha), `ios/project.yml` (XcodeGen), committed `ios/Sonify.xcodeproj`, and `SonifyTests` (`XCTest`).

### 3. Free GitHub Actions macOS Xcode CI, Sideload `.ipa` & TestFlight Upload
`.github/workflows/ios-build.yml` runs on GitHub's free `macos-latest` runner:
- **Every Push / PR to `ios/**`**: Runs `xcodegen` validation, `xcodebuild test` (`SonifyTests` on iOS Simulator), and builds **`Sonify-unsigned.ipa`** as a downloadable Actions artifact (sideloadable via AltStore / SideStore / Sideloadly).
- **One-Click TestFlight / App Store Connect Upload**: Add these GitHub Repository Secrets, then run **Actions -> Sonify iOS Build, Test & TestFlight -> Run workflow -> `upload_to_testflight: true`**:
  - `APPLE_CERTIFICATE_BASE64`, `APPLE_CERTIFICATE_PASSWORD` (base64 Apple Distribution `.p12`)
  - `APPLE_PROVISIONING_PROFILE_BASE64` (base64 App Store `.mobileprovision`)
  - `APPLE_TEAM_ID`, optional `IOS_BUNDLE_ID` (defaults to `com.sonify.music`)
  - `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_PRIVATE_KEY_BASE64` (App Store Connect API Key `.p8`)

---

## Key Features

- **Spotify-Inspired Dark UI & Responsive Design**
  - Authentic 3-pane dark desktop shell (Left Navigation + Library Sidebar, Center Workspace, Toggleable Queue/Lyrics Right Drawer, Persistent 88px Bottom Player Bar), plus Tablet and Mobile layouts.
  - **Home / Discover:** Dynamic greeting, 6-card Quick Picks, horizontal carousels for *Trending Worldwide*, *Top Vietnam / V-Pop*, *New Releases & Hot Albums*, *Full-Track Indie & Electronic (Audius)*, *Popular Artists*, and *Recently Played*.
  - **Live Multi-Source Search:** Debounced live search with Vietnamese diacritic support, Recent Searches chips, Genre/Mood discovery tiles, Spotify-style Top Result card, and `All / Songs / Artists / Albums` filter tabs.
  - **Your Library & Full Playlist CRUD:** Create, rename/edit description, delete custom playlists (with automatic 2x2 cover art mosaic), reorder tracks up/down, remove tracks, inline *"Find songs to add"* mini-search, Liked Songs with instant filter, and chronological Listening History.
  - **Queue & Playback Controls:** Persistent bottom player bar with Play/Pause, Prev/Next, scrubbable Seek bar with buffered progress, Shuffle, Repeat (`off` / `all` / `one`), Volume & Mute, Browser Media Session API integration, and right-click / `...` track context menus.
- **Up-to-Date Live Catalog + 100% Ad-Free Streaming Proxy**
  - Live metadata aggregation across **Deezer Public API**, **Apple Music RSS Charts (VN & US) + iTunes Search/Lookup**, **JioSaavn API** (DES-ECB 320kbps decryptor), **Audius Decentralized API**, and **YouTube Music InnerTube**.
  - **Zero ads, zero sponsored slots, zero trackers** (`X-Ad-Free: true` on every stream response).
  - Backend `/api/stream/{track_id}` proxy with full HTTP `Range` (`206 Partial Content`) support and a **cloud-aware ad-free resolution waterfall**:
    1. Native full-length stream (`Audius` / `JioSaavn` / `YouTube` / `Monochrome`)
    2. **Monochrome Lossless FLAC** (`https://tracks.monochrome.st`, primary on cloud/Render with decoupled progressive `/tmp` single-flight cache streaming immediately after 64 KiB + proactive pre-warm on `/info`, ISRC + strict title/artist/duration matching, 2-step ASCII fallback search, and magic-byte `fLaC -> audio/flac` sniffing) & **YouTube Music** (primary locally via InnerTube `ANDROID` fast-start MP4/AAC; on cloud enabled automatically when `YTDLP_COOKIES_B64`/`YTDLP_COOKIES_PATH`/`YTDLP_PROXY` is set and skipped cleanly otherwise)
    3. **SoundCloud Public API v2** (promoted ahead of JioSaavn on cloud; strict `policy=ALLOW` full-track gate, skipping `abr_sq`/`audio/mpegurl` & `snipped` transcodings, multi-query `limit=25` + `scored[:6]` traversal, lazy HLS fMP4/MP3 assembly)
    4. JioSaavn 320kbps cross-match (strict title/artist/duration gate)
    5. Audius full-track cross-match (strict title/artist/duration gate)
    6. Official 30-second preview of the exact track (`X-Stream-Mode: preview`, 12s short TTL so full-track recovery is never poisoned), or honest `502 Stream Unavailable` if unreachable online (offline synth WAV is strictly reserved for explicit `--offline` / `local:*` showcase tracks)
- **Real-Time Synced & Plain Lyrics**
  - 5-stage lyrics waterfall (**LRCLIB** `/api/get` & `/api/search` -> **YouTube Music InnerTube** -> **Lyrics.ovh** -> built-in local `.lrc`) backed by a 7-day SQLite cache.
  - Available both in the **Right Side Drawer** and as an **Immersive Full-Stage Lyrics View** (`#/lyrics`).
  - Binary-search active line highlighting synced via `requestAnimationFrame`, smooth auto-scroll centering, automatic 4-second pause with floating **Re-sync** button on manual scroll, **click any lyric line to seek**, plus clean **Unsynced**, **Instrumental**, and **Manual Search Override** states.
- **SQLite Persistence (`data/music.db`) + Ephemeral-Cloud Auto-Restore**
  - Automatically initialized in WAL mode on first start (with automatic `/tmp/sonify/music.db` fallback if `data/` is non-writable); persists Playlists, Liked Songs, Listening History, Track Metadata Cache, Lyrics Cache, and Player Settings across restarts, plus browser `localStorage` backup & auto-restore on ephemeral free-tier cloud containers.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Space` | Play / Pause |
| `ArrowRight` / `ArrowLeft` | Seek `+5s` / `-5s` |
| `Shift + ArrowRight` / `Shift + ArrowLeft` | Next / Previous track |
| `ArrowUp` / `ArrowDown` | Volume `+5%` / `-5%` |
| `M` | Mute / Unmute |
| `S` | Toggle Shuffle |
| `R` | Cycle Repeat (`off` -> `all` -> `one`) |
| `L` | Toggle Synced Lyrics Drawer |
| `Q` | Toggle Queue Drawer |
| `/` | Focus Global Search |
| `Escape` | Close Modals & Context Menus |

---

## Project Structure

```text
.
├── app.py                      # Entry point (python3 app.py)
├── render.yaml                 # Render Free Web Service Blueprint (Docker, /api/health)
├── Dockerfile                  # Production Docker image (non-root UID 1000, default port 10000)
├── requirements.txt            # Optional cloud accelerators (cryptography, yt-dlp)
├── .dockerignore               # Docker build context exclusions
├── .gitignore                  # Git exclusions (data/*.db*, __pycache__, etc.)
├── backend/
│   ├── config.py               # Settings, cloud PORT/HOST detection, timeouts, cache TTLs
│   ├── db.py                   # SQLite WAL persistence layer (+ /tmp fallback)
│   ├── http_client.py          # Stdlib HTTP/HTTPS + Range streaming client
│   ├── crypto_des.py           # JioSaavn DES-ECB decryptor (+ pure-Python fallback)
│   ├── lrc_parser.py           # Multi-timestamp / offset .lrc parser & normalizer
│   ├── offline_catalog.py      # Offline fallback catalog, synced LRC, SVG art & stereo WAV synth
│   ├── server.py               # ThreadingHTTPServer API router, SIGTERM shutdown & static SPA server
│   ├── providers/              # deezer, apple_itunes, jiosaavn, audius, youtube_music, lyrics
│   └── services/               # catalog_service, stream_service, library_service
├── frontend/
│   ├── index.html
│   ├── favicon.svg
│   ├── css/                    # tokens.css, layout.css, components.css, lyrics.css
│   └── js/                     # ES2022+ modules (api, store, player, router, lrc, components, views)
├── data/                       # Auto-created SQLite database (music.db)
├── docs/                       # Architecture & engineering specs
└── tests/                      # Automated test suite (python3 -m unittest discover -s tests -v)
```

---

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```
