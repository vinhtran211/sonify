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
3. Render automatically detects `render.yaml` (`sonify-music`, Docker, **Free** plan, `/api/health`) — click **Apply** to deploy live at `https://sonify-music.onrender.com`.

### Option B — Manual Web Service
1. On [dashboard.render.com](https://dashboard.render.com/), click **New +** -> **Web Service** -> select your GitHub repo.
2. Choose **Language / Runtime: Docker**, **Instance Type: Free ($0/month)**, and **Health Check Path: `/api/health`**.
3. Click **Create Web Service** — Render builds `Dockerfile` automatically and binds dynamically to `$PORT` (default `10000`).

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
  - Backend `/api/stream/{track_id}` proxy with full HTTP `Range` (`206 Partial Content`) support and a **resilient ad-free resolution waterfall**:
    1. Native full-length stream (`Audius` / `JioSaavn` / `YouTube`)
    2. YouTube Music full-length stream (InnerTube `ANDROID` fast-start MP4/AAC -> `yt-dlp` -> Piped/Invidious pool), verified with open-ended `Range: bytes=0-`
    3. JioSaavn 320kbps cross-match (strict title/artist/duration gate)
    4. Audius full-track cross-match (strict title/artist/duration gate)
    5. Official 30-second preview of the exact track (`X-Stream-Mode: preview`), or honest `502 Stream Unavailable` if unreachable online (offline synth WAV is strictly reserved for explicit `--offline` / `local:*` showcase tracks)
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
