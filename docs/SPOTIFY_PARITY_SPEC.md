# Sonify — Complete Spotify Feature Parity Architecture Specification

**Scope**: Full end-to-end Spotify feature parity across **Backend (`backend/`)**, **Web App / PWA (`frontend/`)**, and **Native SwiftUI iOS App (`ios/`)**, preserving 100% Ad-Free playback, all streaming speed optimizations, zero third-party Swift dependencies, and all `74/74` existing tests.

---

## 1. Non-Breaking SQLite Schema Migrations (`backend/db.py`)

Add idempotent migrations inside `Database.init_db()` (`CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info(playlists)` check before `ALTER TABLE`):

```sql
CREATE TABLE IF NOT EXISTS followed_artists (
    artist_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    artwork_url TEXT DEFAULT '',
    fans        INTEGER DEFAULT 0,
    followed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_albums (
    album_id     TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL,
    artist_id    TEXT DEFAULT '',
    artwork_url  TEXT DEFAULT '',
    release_date TEXT DEFAULT '',
    track_count  INTEGER DEFAULT 0,
    saved_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pinned_items (
    item_type TEXT NOT NULL,   -- 'playlist' | 'artist' | 'album' | 'liked'
    item_id   TEXT NOT NULL,
    pinned_at INTEGER NOT NULL,
    PRIMARY KEY (item_type, item_id)
);
```

Idempotent `playlists` column additions (check `PRAGMA table_info(playlists)`):
- `cover_color TEXT DEFAULT ''` (e.g. `#1DB954`, `#7C3AED`, `#E11D48`)
- `cover_emoji TEXT DEFAULT ''` (e.g. `🔥`, `🌙`, `🎧`)
- `cover_url   TEXT DEFAULT ''` (optional custom HTTPS cover URL)

Include `"coverColor"`, `"coverEmoji"`, `"coverUrl"` on `_playlist_row_to_dict()` (default `""`).

### Extended `DEFAULT_SETTINGS` (`backend/config.py` + `backend/services/library_service.py`)
Add backwards-compatible defaults (all existing keys unchanged):
```python
DEFAULT_SETTINGS = {
    "volume": 0.8,
    "muted": False,
    "shuffle": False,
    "repeat": "off",
    "region": "global",
    "lastTrackId": None,
    "lastPosition": 0,
    "autoplay": True,
    "crossfade": 0,                # int seconds 0..12
    "playbackRate": 1.0,           # float 0.5..2.0
    "audioQuality": "auto",        # "auto" | "lossless" | "fast"
    "eqPreset": "flat",            # "flat"|"bass_boost"|"vocal"|"pop"|"rock"|"electronic"|"acoustic"|"late_night"|"custom"
    "eqBands": [0, 0, 0, 0, 0],    # 5 ints/floats in [-12, 12] dB for 60Hz, 230Hz, 910Hz, 3.6kHz, 14kHz
}
```
Validate ranges cleanly in `LibraryService.update_settings()` (clamp `crossfade` to `0..12`, `playbackRate` to `0.5..2.0`, `eqBands` to length 5 clamped `-12..12`, `audioQuality` in `("auto", "lossless", "fast")`, `eqPreset` string).

---

## 2. New Backend REST API Contracts (`backend/server.py`, `catalog_service.py`, `library_service.py`, `stream_service.py`, `providers/deezer.py`)

### 2.1 Personalized Discovery, Radio, Genres & Deep Links

| Method & Path | Query / Body | Response JSON | Implementation Notes |
|---|---|---|---|
| `GET /api/radio` | `?trackId=...` or `?artistId=...&limit=25` | `{"seedType":"track\|artist","seedTitle":String,"seedArtist":String,"tracks":[Track]}` | Uses new `deezer.get_artist_related(raw_id)` (`/artist/{id}/related`) + `deezer.get_artist_top(raw_id)` + related artists' top tracks; falls back to iTunes/Deezer artist/genre search and offline catalog when offline. Excludes the seed track itself, deduplicates, annotates `liked`. |
| `GET /api/made-for-you` | `?region=global\|vn` | `{"mixes":[Mix]}` where `Mix = {"id":String,"title":String,"subtitle":String,"description":String,"gradient":[String,String],"cover":String,"tracks":[Track]}` | Dynamically computes 4 Spotify mixes from `play_history`, `liked_tracks`, and `followed_artists` (with graceful live global/VN chart fallback for fresh users): **`daily-mix-1`** (top played/liked artists + radio), **`daily-mix-2`** (secondary vibe/region mix), **`discover-weekly`** (related artists the user hasn't played yet), **`release-radar`** (fresh releases from followed/recent artists + new releases). Cached 10 min per region/library fingerprint. |
| `GET /api/genre/{slug}` | `?region=global\|vn` | `{"genre":{"slug":String,"name":String,"color":String,"description":String},"tracks":[Track],"albums":[Album],"artists":[Artist]}` | Supports 12 canonical Spotify Browse hubs: `pop`, `vpop`, `hiphop`, `kpop`, `indie`, `electronic`, `chill`, `workout`, `focus`, `rnb`, `acoustic`, `lofi`. Fetches live tracks/albums/artists via curated query/genre mapping (Deezer + iTunes) with 10-min TTL cache & offline fallback. Returns `404` for unknown slug. |
| `GET /api/playlists/{id}/recommendations` | `?limit=10` | `{"playlistId":String,"tracks":[Track]}` | Inspects artists/tracks inside playlist `{id}`, fetches related tracks via radio/search, excludes tracks already in the playlist, returns up to `limit` fresh recommendations (or trending fallback if playlist is empty). |
| `GET /api/track/{id}` | — | `{"track":Track,"relatedTracks":[Track]}` | Loads track from SQLite cache or live provider (`deezer.get_track` / `apple_itunes.get_track` / `audius` / `jiosaavn` / `offline_catalog`), returns track + up to 10 radio tracks. Powers `#/track/{id}` shareable deep link. |
| `GET /api/stats` | — | `{"totalPlays":Int,"totalMinutes":Int,"likedCount":Int,"playlistCount":Int,"followedArtistsCount":Int,"savedAlbumsCount":Int,"streakDays":Int,"mostActiveHour":Int?,"hourlyCounts":[Int],"topTracks":[{"track":Track,"playCount":Int}],"topArtists":[{"artist":String,"artistId":String,"cover":String,"playCount":Int}]}` | Computes real Spotify "Sound Capsule" insights directly from SQLite `play_history`, `liked_tracks`, `playlists`, `followed_artists`, `saved_albums`. `hourlyCounts` is a 24-element int array (`0..23`). |

### 2.2 Library Organization, Following, Saved Albums, Pins & Playlist Power Actions

| Method & Path | Body | Response JSON |
|---|---|---|
| `GET /api/library/artists` | — | `{"artists":[FollowedArtist],"artistIds":[String]}` (`FollowedArtist = {id, name, pictureSmall, pictureLarge, fans, followedAt}`) |
| `POST /api/library/artists` | `{"artist": {id, name, pictureSmall?, pictureLarge?, fans?}}` | `201`: `{"followed":true,"artistId":String}` |
| `DELETE /api/library/artists/{artistId}` | — | `{"followed":false,"artistId":String}` |
| `GET /api/library/albums` | — | `{"albums":[SavedAlbum],"albumIds":[String]}` (`SavedAlbum = {id, title, artist, artistId, coverSmall, coverLarge, releaseDate, trackCount, savedAt}`) |
| `POST /api/library/albums` | `{"album": {id, title, artist, artistId?, coverSmall?, coverLarge?, releaseDate?, trackCount?}}` | `201`: `{"saved":true,"albumId":String}` |
| `DELETE /api/library/albums/{albumId}` | — | `{"saved":false,"albumId":String}` |
| `GET /api/library/pins` | — | `{"pins":[{"itemType":String,"itemId":String,"pinnedAt":Int}]}` |
| `POST /api/library/pins` | `{"itemType":"playlist\|artist\|album\|liked","itemId":String}` | `201`: `{"pinned":true,"itemType":String,"itemId":String}` |
| `DELETE /api/library/pins/{itemType}/{itemId}` | — | `{"pinned":false,"itemType":String,"itemId":String}` |
| `POST /api/playlists/{id}/duplicate` | Optional `{"name":String}` | `201`: `{"playlist":Playlist,"tracks":[Track]}` (clones playlist metadata + ordered tracks as `"<Name> (Copy)"`) |
| `POST /api/playlists/import` | `{"name":String,"description":String?,"format":"json\|m3u"?,"tracks":[Track]?,"m3uText":String?}` | `201`: `{"playlist":Playlist,"importedCount":Int}` (creates playlist from JSON tracks array or `#EXTINF` M3U text via catalog match/upsert) |
| `PATCH /api/playlists/{id}` *(extended)* | `{"name"?,"description"?,"coverColor"?,"coverEmoji"?,"coverUrl"?}` | `{"playlist":Playlist}` |

### 2.3 Audio Quality Preference Support (`backend/services/stream_service.py`)
- Accept optional `?quality=auto|lossless|fast` on `GET /api/stream/{track_id}/info` (default `"auto"`).
- In `_resolve_stream_unlocked(..., quality="auto")`:
  - `"auto"`: existing optimized parallel cloud race (`2.8s` Monochrome grace -> SoundCloud -> JioSaavn -> Audius).
  - `"lossless"`: prioritize Monochrome FLAC (`4.5s` Monochrome grace).
  - `"fast"`: in parallel cloud race, if SoundCloud or JioSaavn resolves first with a high-confidence match, return it immediately without waiting for 30–80 MB FLAC; fall back to Monochrome if neither matches.
- Use `cache_key = track_id if quality == "auto" else f"{track_id}::{quality}"` so `"auto"` cache behavior stays 100% identical to existing tests.

---

## 3. Web Frontend Architecture (`frontend/`)

### 3.1 New Routes (`frontend/js/router.js`)
Add to `parseHash()`:
- `#/genre/:slug` -> `{ name: 'genre', params: { slug } }`
- `#/track/:id` -> `{ name: 'track', params: { id } }`
- `#/mix/:id` -> `{ name: 'mix', params: { id } }` (opens a Made For You mix detail page!)
- `#/stats` -> `{ name: 'stats' }` (Sound Capsule / Listening Stats)
- `#/downloads` -> `{ name: 'downloads' }` (Offline Downloaded tracks)

### 3.2 Store Extensions (`frontend/js/store.js`)
Extend `state` + `localStorage` backup (`sonify_library_backup_v1` & `sonify_prefs_v1`):
- `drawerMode`: `null | 'now-playing' | 'queue' | 'lyrics'`
- `queueSubTab`: `'queue' | 'recent'`
- `libraryFilter`: `'all' | 'playlists' | 'artists' | 'albums' | 'downloads'`
- `librarySort`: `'recents' | 'alpha'`
- `librarySearch`: `''`
- `followedArtists: []`, `followedArtistIds: new Set()`
- `savedAlbums: []`, `savedAlbumIds: new Set()`
- `pinnedItems: []` (array of `{ itemType, itemId, pinnedAt }`)
- `madeForYou: []` (cached mixes for Home + `#/mix/:id`)
- `downloadedIds: new Set()` (populated from `offlineDownloads.js`)
- Audio settings synced with `/api/settings` & `localStorage`: `autoplay`, `crossfade`, `playbackRate`, `audioQuality`, `eqPreset`, `eqBands`
- `sleepTimer`: `{ mode: 'off'|'minutes'|'end_of_track', endsAt: null|number, remainingSec: 0 }`
- `fullScreenMode`: `false`

### 3.3 Audio Engine, 5-Band Equalizer, Crossfade, Sleep Timer & Offline Downloads (`frontend/js/`)
1. **`frontend/js/audioEffects.js` (5-Band Web Audio Equalizer — Zero-Silence Safe!)**:
   - **CRITICAL CORS RULE**: Never call `audioCtx.createMediaElementSource(audioEl)` until the user activates a non-flat EQ preset or adjusts a band (`isEqActive()`).
   - Once attached (`source -> 5x BiquadFilterNode [60Hz lowshelf, 230Hz peaking, 910Hz peaking, 3600Hz peaking, 14000Hz highshelf] -> destination`), export `isEqGraphAttached()` so `player.js` routes playback strictly through **same-origin `/api/stream/{id}`** (never cross-origin `directUrl`/`hlsUrl`) -> **0% chance of CORS-muted silence!**
   - Built-in Spotify presets (`EQ_PRESETS`): `flat`, `bass_boost`, `vocal`, `pop`, `rock`, `electronic`, `acoustic`, `late_night`, `custom`.
   - Calls `audioCtx.resume()` safely on play gesture; falls back silently if `AudioContext` is unsupported.
2. **`frontend/js/player.js` Upgrades**:
   - **Autoplay**: When queue reaches end with `repeat === 'off'` and `st.autoplay === true`, call `api.getRadio({ trackId: cur.id })`, append returned tracks to `queue`, show subtle toast `"Autoplay queued similar songs"`, and seamlessly play next!
   - **Crossfade (`0–12s`)**: When `crossfade > 0`, during the final `min(crossfade, 6)` seconds of a track (not `repeat === 'one'`), smoothly ramp `audioEl.volume` down, transition to the pre-warmed next track, and ramp volume back up to `st.volume` (respecting `muted`).
   - **Playback Speed (`0.5×–2.0×`)**: Apply `audioEl.playbackRate = st.playbackRate` on `loadedmetadata` and immediately on setting change.
   - **Audio Quality (`auto` | `lossless` | `fast`)**: Pass `quality` to `api.getStreamInfo(track.id, signal, st.audioQuality)`.
   - **Sleep Timer**: Interval ticker (1s) updating `sleepTimer.remainingSec`; on `0` (or track `ended` when `mode === 'end_of_track'`), pauses playback cleanly and shows toast `"Sleep timer paused playback"`.
   - **Offline Downloads Playback**: Before calling `/api/stream/{id}/info`, check `offlineDownloads.getBlobUrl(track.id)` — if downloaded locally, play instantly from Cache API Blob URL (`streamMode: 'offline-download'`, `streamSource: 'offline-cache'`) even with zero internet!
3. **`frontend/js/offlineDownloads.js` (Spotify Premium-Style Cache API Offline Storage)**:
   - Stores audio in `caches.open('sonify-offline-audio-v1')` under `/offline-audio/{trackId}` and metadata in `localStorage` (`sonify_downloads_v1`).
   - Exports `downloadTrack(track)`, `downloadPlaylist(tracks, onProgress)`, `removeDownload(trackId)`, `isDownloaded(trackId)`, `getAllDownloads()`, `getBlobUrl(trackId)`. Does NOT interfere with `sw.js` `/api/*` bypass!

### 3.4 Spotify Signature UI Views & Components (`frontend/js/`)
1. **3rd-Pane "Now Playing View" + Upgraded Right Drawer (`frontend/js/components/nowPlayingDrawer.js`, `queuePanel.js`, `main.js`)**:
   - Right drawer header gets Spotify's 3 clean tab pills: **`Now Playing`** | **`Queue`** | **`Lyrics`**.
   - **Now Playing tab**: Large rounded artwork, track title + Like heart + Share/Download buttons, **"About the Artist"** card (fetches `api.getArtist(track.artistId)` lazily with artwork, monthly fans, **Follow/Following** toggle, link to `#/artist/{id}`), **Track Credits** (Artist, Album, Quality/Codec badge, 100% Ad-Free), and clickable **"Next in Queue"** preview card.
   - **Queue tab**: Sub-tabs **`Queue | Recently Played`**, Clear Queue button, up/down + HTML5 drag-and-drop reorder (`draggable="true"`) and remove buttons.
2. **Full-Screen Mode (`frontend/js/components/fullScreenPlayer.js` + `F` shortcut)**:
   - Ambient dominant-color blurred backdrop (`colorExtractor.js`), large square artwork on left, real-time synced clickable lyrics on right, minimal bottom controls (Play/Pause, Prev/Next, Seek bar, Like, Sleep Timer badge, Exit `Esc`/`F`).
3. **Dynamic Dominant-Color Header Gradients (`frontend/js/colorExtractor.js`)**:
   - Deterministic rich Spotify palette hash + canvas sampling fallback (gracefully handles cross-origin artwork without throwing tainted-canvas errors!) applied as `--hero-tint` CSS variable on Artist, Album, Playlist, Liked Songs, Mix, Track, and Genre view headers.
4. **Home, Genre Hub, Track Deep Link, Sound Capsule Stats & Library Upgrades**:
   - **`homeView.js`**: Add **"Made For You"** horizontal carousel (`Daily Mix 1`, `Daily Mix 2`, `Discover Weekly`, `Release Radar`) + clickable **Genre & Mood** quick-jump pills on Home + link to **Sound Capsule (`#/stats`)**.
   - **`searchView.js`**: Clicking any of the 12 Browse Genre tiles navigates to `#/genre/{slug}` instead of plain text search!
   - **`genreView.js`**: New `#/genre/{slug}` view with vibrant header gradient, Play All, Top Tracks list, Albums carousel, and Artists carousel.
   - **`statsView.js`**: New `#/stats` **Sound Capsule** view showing Total Minutes, Total Plays, Listening Streak, Liked/Playlists/Following counts, 24-hour listening activity bar chart, Top Tracks (playable), and Top Artists (clickable).
   - **`detailView.js`**:
     - Artist page: **Follow / Following** button + **Artist Radio** button + **Share** button + dominant-color hero.
     - Album page: **Save Album (`♥`)** button + **Share** button + dominant-color hero.
     - Track page (`#/track/{id}`): Shareable single-track hero (Play, Like, Go to Song Radio, Download, Copy Link) + Related Tracks list.
     - Mix page (`#/mix/{id}`): Made For You mix detail with Play All, Save as Playlist, and track table.
   - **`libraryView.js`**:
     - Playlist page: **Sortable track table columns** (`#`, `Title`, `Album`, `Duration`), **HTML5 drag-and-drop track reorder** (calls `api.reorderPlaylistTracks`), **Duplicate Playlist**, **Export JSON / Export M3U**, **Import Playlist** button, **Download Playlist Offline**, **Edit Cover (Color + Emoji + URL)**, and Spotify-style **"Recommended — Based on what's in this playlist"** footer with `+ Add` and `Refresh`!
     - Downloads page (`#/downloads`): Lists all Cache-API-downloaded tracks with Play All and Remove actions.
   - **`sidebar.js`**:
     - Add **Sound Capsule (`#/stats`)** nav link; upgrade Your Library with Filter pills (`All | Playlists | Artists | Albums | Downloads`), Sort toggle (`Recents | A–Z`), inline Library filter search input, **Pin/Unpin** icon (pinned items always float to top with green pin badge!), and list Followed Artists + Saved Albums alongside Liked Songs & Playlists.
   - **`contextMenu.js`**:
     - Add **`Go to Song Radio`**, **`Go to Artist Radio`**, **`Download Offline / Remove Download`**, and **`Copy Song Link / Share`** (`#/track/{id}`).
   - **`playerBar.js` & `audioSettingsModal.js`**:
     - Add compact **Now Playing Drawer**, **EQ & Audio Settings** (opens modal with 5-band interactive EQ sliders + presets, Crossfade `0–12s` slider, Autoplay toggle, Audio Quality `Auto / Lossless / Fast`, Playback Speed `0.5×–2.0×`, and Sleep Timer selector with live countdown badge!), and **Full-Screen (`F`)** icon buttons in `.player-right`.

---

## 4. Native SwiftUI iOS App Parity (`ios/Sonify/`)

Keep **zero third-party Swift dependencies**, explicit `import Combine` / `import UIKit` wherever used, and update BOTH `ios/project.yml` (directory auto-discovery) AND `ios/Sonify.xcodeproj/project.pbxproj` for every new Swift file!

### 4.1 Model Updates
- **`ios/Sonify/Models/LibraryModels.swift`**:
  - Extend `Playlist` with optional `coverColor: String`, `coverEmoji: String`, `coverUrl: String` (default `""`).
  - Extend `AppSettings` with `autoplay: Bool` (default `true`), `crossfade: Int` (default `0`), `playbackRate: Double` (default `1.0`), `audioQuality: String` (default `"auto"`), `eqPreset: String` (default `"flat"`), `eqBands: [Double]` (default `[0,0,0,0,0]`).
  - Add `FollowedArtist`, `FollowedArtistsResponse`, `FollowMutationResponse`, `SavedAlbum`, `SavedAlbumsResponse`, `SaveAlbumMutationResponse`, `PinnedItem`, `PinsResponse`, `PinMutationResponse`, `PlaylistDuplicateResponse`.
- **`ios/Sonify/Models/CatalogModels.swift`**:
  - Add `RadioResponse`, `MadeForYouMix`, `MadeForYouResponse`, `GenreInfo`, `GenreHubResponse`, `PlaylistRecommendationsResponse`, `TrackDetailResponse`, `SoundCapsuleStats`, `TopTrackStat`, `TopArtistStat`.

### 4.2 Services Updates
- **`ios/Sonify/Services/APIClient.swift`**:
  - Add `getRadio(trackId:artistId:)`, `getMadeForYou(region:)`, `getGenre(slug:region:)`, `getPlaylistRecommendations(playlistId:)`, `getTrackDetail(trackId:)`, `getStats()`, `getFollowedArtists()`, `followArtist(_:)`, `unfollowArtist(artistId:)`, `getSavedAlbums()`, `saveAlbum(_:)`, `unsaveAlbum(albumId:)`, `getPins()`, `pinItem(type:id:)`, `unpinItem(type:id:)`, `duplicatePlaylist(id:)`, and pass optional `quality` query param to `getStreamInfo(trackId:quality:)`.
- **`ios/Sonify/Services/LibraryStore.swift`**:
  - Publish `followedArtists`, `followedArtistIds`, `savedAlbums`, `savedAlbumIds`, `pinnedItems`, `madeForYouMixes`, `stats`, and `settings` (syncing `autoplay`, `playbackRate`, `audioQuality`, `crossfade`). Add `toggleFollow(artist:)`, `toggleSaveAlbum(_:)`, `loadMadeForYou()`, `loadStats()`, `duplicatePlaylist(_:)`.
- **`ios/Sonify/Services/AudioPlayerService.swift`**:
  - Wire **Autoplay** (`onTrackDidEnd`: when `queueEngine.nextTrack(fromAutoEnd: true)` returns `nil` and `autoplayEnabled == true`, fetch `api.getRadio(trackId:)` and seamlessly continue playing!).
  - Wire **Go to Song Radio (`startRadio(for:)` / `startArtistRadio(artistId:)`)**.
  - Wire **Sleep Timer** (`setSleepTimer(minutes:)` / `setSleepTimerEndOfTrack()` / `cancelSleepTimer()`, published `sleepTimerRemainingSec` & `sleepTimerEndOfTrack`).
  - Wire **Playback Speed** (`playbackRate: Float`, applied to `player.rate` when playing and on resume).
  - Wire **Audio Quality Preference** (`audioQuality`: passed to `api.getStreamInfo(trackId:quality:)`).

### 4.3 SwiftUI View Updates
- **`HomeView.swift`**: Add **Made For You** mixes horizontal carousel (`Daily Mix 1/2`, `Discover Weekly`, `Release Radar`) navigating to `MixDetailView`, plus **Sound Capsule Stats** button in the header toolbar.
- **`SearchView.swift`**: Wire all 12 Genre/Mood browse cards to navigate to **`GenreHubView(slug:)`**!
- **`LibraryView.swift`**: Add filter pills (`All | Playlists | Artists | Albums`), sort picker (`Recents | A–Z`), inline search, and sections for Followed Artists & Saved Albums + Sound Capsule quick link.
- **`DetailViews.swift`**: Add **Follow** button & **Artist Radio** button on `ArtistDetailView`; **Save Album** button on `AlbumDetailView`; **Duplicate** & **Recommended Songs** (`+ Add`) on `PlaylistDetailView`; add `MixDetailView`, `GenreHubView`, and `StatsView` (Sound Capsule sheet/page).
- **`TrackRowView.swift`**: Add `"Go to Song Radio"` & `"Share Link"` to track context menu.
- **`NowPlayingView.swift`**: Add **Sleep Timer** menu button (with live countdown pill), **Playback Speed** (`0.5×–2.0×`) menu, **Go to Song Radio** action, and **Audio Quality** indicator.
- **`SettingsView.swift`**: Add **Autoplay** toggle, **Audio Quality** picker (`Auto`, `Lossless FLAC`, `Fast Standard`), **Playback Speed** picker, **Crossfade** stepper, and **View Sound Capsule Stats** button.

---

## 5. QA & Acceptance Criteria (For `@tester`)

1. **Backend API & SQLite Tests (`tests/test_spotify_parity.py`)**:
   - SQLite non-breaking migration on both fresh DB and pre-existing v1 DB (`followed_artists`, `saved_albums`, `pinned_items`, `playlists.cover_color/cover_emoji/cover_url`).
   - Follow/Unfollow Artist CRUD, Save/Unsave Album CRUD, Pin/Unpin Item CRUD, Playlist Duplicate & JSON/M3U Import.
   - `GET /api/radio` (track & artist seeds, excludes seed track, offline fallback), `GET /api/made-for-you` (returns 4 mixes with tracks), `GET /api/genre/{slug}` (valid slug 200 + unknown slug 404), `GET /api/playlists/{id}/recommendations` (excludes existing playlist tracks), `GET /api/track/{id}` (track + relatedTracks), `GET /api/stats` (accurate `totalPlays`, `totalMinutes`, `topTracks`, `topArtists`, `hourlyCounts` length 24).
   - Extended `/api/settings` validation (`autoplay`, `crossfade` 0..12, `playbackRate` 0.5..2.0, `audioQuality`, `eqPreset`, `eqBands` length 5).
   - `/api/stream/{id}/info?quality=fast|lossless|auto` routing without breaking existing `"auto"` cache behavior.
2. **Web Frontend & ES Module Integrity**:
   - All new JS modules (`audioEffects.js`, `offlineDownloads.js`, `colorExtractor.js`, `nowPlayingDrawer.js`, `fullScreenPlayer.js`, `audioSettingsModal.js`, `genreView.js`, `statsView.js`) pass ES import/export integrity, zero-tracker audit, and `audioEffects.js` enforces lazy EQ attach + same-origin `/api/stream/` when active.
3. **Native iOS Parity & CI Readiness**:
   - All new/updated Swift files registered in `ios/Sonify.xcodeproj/project.pbxproj`, Apple-only imports (`Combine`/`UIKit` explicit), balanced syntax, and updated `ModelDecodingTests.swift` covering `RadioResponse`, `MadeForYouResponse`, `GenreHubResponse`, `SoundCapsuleStats`, `FollowedArtistsResponse`, `SavedAlbumsResponse`, and extended `AppSettings`.
4. **Zero Regression**: All `74` existing tests + all new Spotify parity tests pass `100%` (`python3 -m unittest discover -s tests -v`).
