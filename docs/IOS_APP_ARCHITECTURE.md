# Sonify iOS Native App & iOS PWA Architecture Specification

**Target Platform**: iOS 16.0+ (built with latest Xcode / iOS SDK on `macos-latest` for 2026 App Store Connect compliance)  
**Backend API**: Live Render backend `https://sonify-music.onrender.com` (configurable in-app to any local/LAN/custom server URL)  
**Guarantees**: 100% Ad-Free, Zero Third-Party Swift Dependencies (pure Apple frameworks: `SwiftUI`, `AVFoundation`, `MediaPlayer`, `AVKit`, `Combine`, `Foundation`), Full-Track Streaming (`FLAC`, `M4A/MP4`, `MP3`), Synced `.lrc` Lyrics, Background Audio + Lock Screen Controls, plus iOS PWA Upgrade for `frontend/`.

---

## 1. Exact Backend API Contract Audit

All JSON schemas below were audited directly against `backend/server.py`, `backend/db.py`, `backend/lrc_parser.py`, `backend/providers/{deezer,lyrics}.py`, `backend/services/{catalog_service,library_service,stream_service}.py`, and `frontend/js/api.js`.

### 1.1 Error Envelope (All 4xx / 5xx Responses)
```json
{
  "error": {
    "code": "STREAM_UNAVAILABLE",
    "message": "Unable to stream '...' right now."
  }
}
```

### 1.2 Endpoints & Exact JSON Schemas

| Method & Path | Query / Body | Response Schema |
|---|---|---|
| `GET /api/health` | — | `{"status":"ok","version":"1.0.0","adFree":true,"dbReady":true,"ytDlpAvailable":true,"ytDlpConfigured":false,"cloudMode":true,"renderRegion":"singapore","offlineMode":false,"timestamp":1764000000}` |
| `GET /api/home` | `?region=global\|vn&refresh=0\|1` | `{"mode":"online\|offline","region":"global\|vn","generatedAt":Int,"sections":[HomeSection]}` |
| `GET /api/search` | `?q=...&type=all\|tracks\|artists\|albums&limit=20` | `{"query":String,"mode":"online\|offline","topResult":TopResult?,"tracks":[Track],"artists":[Artist],"albums":[Album]}` |
| `GET /api/artist/{artistId}` | URL-encoded `artistId` (e.g. `dz%3A27`) | `{"artist":Artist,"topTracks":[Track],"albums":[Album]}` |
| `GET /api/album/{albumId}` | URL-encoded `albumId` | `{"album":Album,"tracks":[Track]}` |
| `GET /api/stream/{trackId}/info` | Optional `?debug=1` | `{"trackId":String,"streamUrl":"/api/stream/{trackId}","streamUrlWithExt":"/api/stream/{trackId}.{ext}","streamMode":"full\|preview\|offline-synth","streamSource":String,"contentType":String,"adFree":true}` |
| `GET / HEAD /api/stream/{trackId}` & `/api/stream/{trackId}.{ext}` | Optional `Range: bytes=start-end` | Raw audio stream (`200 OK` or `206 Partial Content`) with `Content-Type`, `Accept-Ranges: bytes`, `Content-Length`, `Content-Range`, `Content-Disposition: inline; filename="stream.{ext}"`, `X-Stream-Mode`, `X-Stream-Source`, `X-Ad-Free: true` |
| `GET /api/lyrics` | `?trackId=...&title=...&artist=...&album=...&duration=...&refresh=0\|1` | `{"trackId":String,"title":String,"artist":String,"status":"synced\|plain\|instrumental\|not_found","source":String,"instrumental":Bool,"synced":Bool,"lines":[LyricLine],"plainLyrics":String,"rawLrc":String}` |
| `GET /api/library/likes` | — | `{"tracks":[Track],"likedIds":[String]}` |
| `POST /api/library/likes` | `{"track": Track}` | `201`: `{"liked":true,"trackId":String}` |
| `DELETE /api/library/likes/{trackId}` | — | `{"liked":false,"trackId":String}` |
| `GET /api/playlists` | — | `{"playlists":[Playlist]}` |
| `POST /api/playlists` | `{"name":String,"description":String}` | `201`: `{"playlist":Playlist}` |
| `GET /api/playlists/{id}` | — | `{"playlist":Playlist,"tracks":[Track]}` |
| `PATCH /api/playlists/{id}` | `{"name":String?,"description":String?}` | `{"playlist":Playlist}` |
| `DELETE /api/playlists/{id}` | — | `{"deleted":true,"id":String}` |
| `POST /api/playlists/{id}/tracks` | `{"track": Track}` | `201`: `{"playlistId":String,"trackId":String,"position":Int,"trackCount":Int,"alreadyInPlaylist":Bool}` |
| `DELETE /api/playlists/{id}/tracks/{trackId}` | — | `{"removed":true,"trackCount":Int}` |
| `PATCH /api/playlists/{id}/tracks/reorder` | `{"trackIds":[String]}` | `{"reordered":true}` |
| `GET /api/history` | `?limit=50` | `{"items":[{"playedAt":Int,"track":Track}]}` |
| `POST /api/history` | `{"track": Track}` | `201`: `{"recorded":true,"trackId":String}` |
| `DELETE /api/history` | — | `{"cleared":true}` |
| `GET /api/settings` | — | `{"volume":Double,"muted":Bool,"shuffle":Bool,"repeat":"off\|all\|one","region":"global\|vn","lastTrackId":String?,"lastPosition":Double}` |
| `PATCH /api/settings` | Partial settings JSON | Full `Settings` JSON |

### 1.3 Swift `Codable` Struct Contracts (Exact Field Mapping)

Important Swift decoding notes from inspecting the Python source:
- **`LyricLine`**: `backend/lrc_parser.py` emits `{"time": FloatSeconds, "text": String}` and optionally `"interlude": true`. Use `time: Double`, `text: String`, `interlude: Bool?`.
- **`Settings`**: `"repeat"` is a Swift keyword -> map with `enum CodingKeys: String, CodingKey { case volume, muted, shuffle, repeatMode = "repeat", region, lastTrackId, lastPosition }`.
- **`HomeSection`**: `type` is `"tracks"`, `"albums"`, or `"artists"` -> decode polymorphic `items` inside custom `init(from decoder:)` into typed properties `tracks: [Track]`, `albums: [Album]`, `artists: [Artist]`.
- **`TopResult`**: `kind` is `"track"`, `"artist"`, or `"album"` -> decode `item` based on `kind` into `enum TopResult: Equatable { case track(Track), artist(Artist), album(Album) }`.
- **`Track`**: Always decode `id`, `title`, `artist` as required; default/optionalize `source`, `artistId`, `album`, `albumId`, `duration` (default `0`), `coverSmall`, `coverLarge`, `explicit` (default `false`), `releaseDate`, `previewUrl`, `streamUrl`, `liked` (default `false`), `likedAt`, `position`, `addedAt` so partial payloads never crash decoding.
- **URL Path Encoding**: Track/Artist/Album IDs contain colons (e.g. `dz:908604612`, `it:6829122414`). Always percent-encode path segments using `CharacterSet.urlPathAllowed.subtracting(CharacterSet(charactersIn: "/:?#"))` (or `.addingPercentEncoding` on the ID segment before joining `/api/stream/\(encodedId)/info`).
- **Artwork URLs**: Online covers are full `https://` JPEG/PNG URLs; offline demo tracks use relative `/api/artwork/<slug>` (SVG). Since SwiftUI `AsyncImage` cannot render SVG, `ArtworkView` should treat relative `/api/artwork/` or nil URLs as a stylish gradient placeholder (`#1DB954` / `#282828` with SF Symbol `music.note`).

---

## 2. Critical iOS `AVPlayer` Streaming Compatibility & Backend Enhancement

### 2.1 Why Extensionless URLs Break `AVPlayer` on iOS 16–18+
Our backend streams four container/codec formats:
- `audio/flac` (Monochrome Lossless FLAC — Tier 1 on Render)
- `audio/mp4` / `audio/x-m4p` (SoundCloud HLS-assembled fMP4 AAC / iTunes preview)
- `video/mp4` (YouTube progressive `itag=18` MP4 + AAC)
- `audio/mpeg` (SoundCloud progressive / Audius / Deezer preview)
- `audio/wav` (Local offline showcase)

On **iOS 16**, `AVURLAsset` heavily depends on a **URL path extension** (`.flac`, `.m4a`, `.mp4`, `.mp3`, `.wav`) when opening remote HTTPS streams — extensionless URLs like `/api/stream/dz:908604612` frequently fail with `AVErrorFileFormatNotRecognized` (`-11828`), especially for raw `audio/flac`! On **iOS 17+**, Apple introduced `AVURLAssetOverrideMIMETypeKey`, which fixes extensionless URLs *when specified*. Additionally, iOS `AVPlayer` always initiates playback with a 2-byte probe request (`GET` with **`Range: bytes=0-1`**) and expects:
1. `HTTP 206 Partial Content`
2. Exact `Content-Range: bytes 0-1/{total_bytes}` and `Content-Length: 2`
3. Standard MIME type (`audio/flac`, `audio/mp4`, `video/mp4`, `audio/mpeg`, `audio/wav` — **NOT** `audio/x-m4p`, which iTunes preview occasionally sends!)
4. `Accept-Ranges: bytes`

### 2.2 Backwards-Compatible Backend Enhancement (`backend/server.py` + `backend/services/stream_service.py`)

Implement a clean, 100% backwards-compatible enhancement so **BOTH iOS 16 AND iOS 17–26 `AVPlayer`** play every format natively without `AVAssetResourceLoaderDelegate` hacks:

1. **Extension Mapping Helper (`backend/services/stream_service.py`)**:
   ```python
   ALLOWED_STREAM_EXTENSIONS = frozenset({
       ".flac", ".m4a", ".mp4", ".mp3", ".wav", ".aac", ".ogg", ".webm"
   })

   def normalize_stream_mime(content_type: str) -> str:
       ctype = (content_type or "audio/mpeg").split(";", 1)[0].strip().lower()
       if ctype in ("audio/x-flac", "application/flac"):
           return "audio/flac"
       if ctype in ("audio/x-m4a", "audio/x-m4p", "audio/m4a"):
           return "audio/mp4"
       if ctype in ("audio/mp3", "audio/x-mpeg"):
           return "audio/mpeg"
       if ctype in ("audio/x-wav", "audio/wave"):
           return "audio/wav"
       return ctype

   def mime_to_extension(content_type: str) -> str:
       ctype = normalize_stream_mime(content_type)
       if "flac" in ctype:
           return ".flac"
       if ctype.startswith("video/mp4"):
           return ".mp4"
       if "mp4" in ctype or "aac" in ctype:
           return ".m4a"
       if "mpeg" in ctype or "mp3" in ctype:
           return ".mp3"
       if "wav" in ctype:
           return ".wav"
       if "webm" in ctype:
           return ".webm"
       if "ogg" in ctype:
           return ".ogg"
       return ".m4a"
   ```

2. **Route Extension Stripping (`backend/server.py`, `/api/stream/` router)**:
   - Keep `GET /api/stream/{track_id}/info` and `GET/HEAD /api/stream/{track_id}` working identically.
   - For stream playback paths (`/api/stream/{rest}` where not ending in `/info`), strip a trailing known media extension **only if** the lowercase suffix is in `ALLOWED_STREAM_EXTENSIONS`:
     - `/api/stream/dz:908604612.flac` -> `track_id = "dz:908604612"`
     - `/api/stream/dz%3A908604612.m4a` -> `track_id = "dz:908604612"`
     - `/api/stream/dz:908604612` -> `track_id = "dz:908604612"` (unchanged!)

3. **Enhanced `/api/stream/{track_id}/info` JSON (`backend/services/stream_service.py`)**:
   - Normalize `contentType` via `normalize_stream_mime(resolved.content_type)`.
   - Keep `"streamUrl": f"/api/stream/{track_id}"` unchanged.
   - Add `"streamUrlWithExt": f"/api/stream/{track_id}{mime_to_extension(norm_mime)}"`.

4. **Enhanced Stream Response Headers (`_send_common_stream_headers`)**:
   - Send normalized `Content-Type` (so `audio/x-m4p` becomes `audio/mp4`).
   - Add `Content-Disposition: inline; filename="stream{ext}"` alongside `Accept-Ranges: bytes`, `Cache-Control: private, max-age=1800`, `X-Stream-Mode`, `X-Stream-Source`, `X-Ad-Free: true`.
   - Confirm `Range: bytes=0-1` works cleanly on `_serve_progressive_monochrome()`, `_serve_file_path()`, and `_serve_local_bytes()` (returning `206 Partial Content`, `Content-Range: bytes 0-1/{total}`, `Content-Length: 2`, and sharing the single background producer without spawning a second Monochrome request).

### 2.3 Belt-and-Suspenders iOS `AVURLAsset` Initialization
In `AudioPlayerService.swift`:
1. Call `GET /api/stream/{trackId}/info` first (also warms Monochrome's progressive cache on Render!).
2. Resolve stream URL using `info.streamUrlWithExt` (or if connected to an older backend lacking `streamUrlWithExt`, append mapped extension from `info.contentType` onto `info.streamUrl`).
3. Build `AVURLAsset`:
   ```swift
   var options: [String: Any] = [:]
   if #available(iOS 17.0, *) {
       options[AVURLAssetOverrideMIMETypeKey] = Self.normalizedMIME(info.contentType)
   }
   let asset = AVURLAsset(url: streamURL, options: options)
   let item = AVPlayerItem(asset: asset)
   item.preferredForwardBufferDuration = 5.0
   ```
4. Set `player.automaticallyWaitsToMinimizeStalling = true`.

---

## 3. 2026 Apple App Store Connect & Native iOS Requirements

### 3.1 Deployment Target & Xcode SDK
- **Minimum Deployment Target**: `IPHONEOS_DEPLOYMENT_TARGET: 16.0` (universal iPhone + iPad: `TARGETED_DEVICE_FAMILY: "1,2"`).
- **Swift Version**: `SWIFT_VERSION: "5.9"` (compatible with Xcode 15, 16, and 26 on `macos-latest`).
- **2026 App Store Connect SDK Mandate**: Apps uploaded to App Store Connect must be compiled with the latest Xcode/iOS SDK available on GitHub Actions `macos-latest` (CI selects the newest installed `/Applications/Xcode*.app`).
- **Bundle Identifier**: Configurable via `PRODUCT_BUNDLE_IDENTIFIER` (`com.sonify.music` default, overridable via CI secret `IOS_BUNDLE_ID`).
- **Marketing Version & Build Number**: `MARKETING_VERSION: "1.0.0"`, `CURRENT_PROJECT_VERSION: "1"` (CI auto-injects `github.run_number` on release archives).

### 3.2 Mandatory `PrivacyInfo.xcprivacy` (`ios/Sonify/Resources/PrivacyInfo.xcprivacy`)
Must be included in the `Sonify` app target's **Copy Bundle Resources** phase:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>NSPrivacyTracking</key>
    <false/>
    <key>NSPrivacyTrackingDomains</key>
    <array/>
    <key>NSPrivacyCollectedDataTypes</key>
    <array/>
    <key>NSPrivacyAccessedAPITypes</key>
    <array>
        <dict>
            <key>NSPrivacyAccessedAPIType</key>
            <string>NSPrivacyAccessedAPICategoryUserDefaults</string>
            <key>NSPrivacyAccessedAPITypeReasons</key>
            <array>
                <string>CA92.1</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
```
*(Zero third-party SDKs, zero tracking, zero ads; `UserDefaults` is used strictly for app-only settings such as backend server URL, catalog region, volume/shuffle/repeat preferences -> reason `CA92.1`.)*

### 3.3 Mandatory `Info.plist` (`ios/Sonify/Info.plist`)
- `CFBundleDisplayName`: `Sonify`
- `UIBackgroundModes`: `["audio"]` (enables lock-screen & background audio playback)
- `ITSAppUsesNonExemptEncryption`: `false` (streamlines App Store Connect export compliance)
- `UILaunchScreen`: `<dict><key>UIColorName</key><string>LaunchBackground</string></dict>` (pure `#000000` dark launch screen matching Spotify)
- `UIUserInterfaceStyle`: `Dark`
- `NSAppTransportSecurity`:
  - `NSAllowsArbitraryLoads`: `false` (strict HTTPS enforced for `https://sonify-music.onrender.com` and all external cover CDNs)
  - `NSAllowsLocalNetworking`: `true` (allows users to point Settings at a local `http://192.168.x.x:8080` or `http://localhost:8080` Sonify backend on LAN without breaking App Store ATS rules!)
- `UIApplicationSceneManifest` / `UISupportedInterfaceOrientations`: Portrait on iPhone; all orientations on iPad.

### 3.4 App Icon Rules (`ios/Sonify/Resources/Assets.xcassets/AppIcon.appiconset/`)
- **Single-size universal iOS AppIcon**: `1024×1024` PNG (`AppIcon.png`) declared in `Contents.json` (`idiom: "universal"`, `platform: "ios"`, `size: "1024x1024"`).
- **CRITICAL APPLE RULE**: **NO alpha channel** — PNG MUST be 8-bit RGB (`color_type = 2`, opaque `#000000` background with Sonify's `#1DB954` green circle and bold `#000000` curved soundwaves matching `frontend/favicon.svg`). Apple App Store Connect rejects any AppIcon containing an alpha channel!
- Also include `LaunchBackground.colorset` (`#000000`) and `AccentColor.colorset` (`#1DB954`).

### 3.5 Native iOS Audio Session, Lock Screen, & AirPlay Integration
In `AudioPlayerService.swift` (`@MainActor final class AudioPlayerService: ObservableObject`):
1. **`AVAudioSession`**:
   - Configure `.setCategory(.playback, mode: .default, options: [])` and `.setActive(true)` before starting playback.
   - Observe `AVAudioSession.interruptionNotification`:
     - `.began` -> pause playback & update `isPlaying = false`.
     - `.ended` with `.shouldResume` -> reactivate session & resume playback if previously playing.
   - Observe `AVAudioSession.routeChangeNotification`:
     - `.oldDeviceUnavailable` (headphones/AirPods unplugged) -> immediately pause playback (Apple HIG compliance!).
2. **`MPNowPlayingInfoCenter`**:
   - Publish `MPMediaItemPropertyTitle`, `MPMediaItemPropertyArtist`, `MPMediaItemPropertyAlbumTitle`, `MPMediaItemPropertyPlaybackDuration`, `MPNowPlayingInfoPropertyElapsedPlaybackTime`, `MPNowPlayingInfoPropertyPlaybackRate` (`1.0` when playing, `0.0` when paused), and asynchronously loaded `MPMediaItemArtwork` from `coverLarge`.
   - Keep elapsed time & rate synchronized on play/pause/seek/track-change.
3. **`MPRemoteCommandCenter`**:
   - Register handlers for `playCommand`, `pauseCommand`, `togglePlayPauseCommand`, `nextTrackCommand`, `previousTrackCommand`, and `changePlaybackPositionCommand` (scrubbing from Lock Screen / Dynamic Island / Control Center).
4. **AirPlay Picker (`AirPlayButton.swift`)**:
   - `UIViewRepresentable` wrapping `AVRoutePickerView` (from `AVKit`) with `tintColor = .white`, `activeTintColor = UIColor(red: 29/255, green: 185/255, blue: 84/255, alpha: 1)`, `prioritizesVideoDevices = false`.

---

## 4. Native SwiftUI App Architecture & Spotify-Like UX

### 4.1 Folder Layout (`ios/`)
```text
ios/
├── project.yml                              # XcodeGen specification (canonical)
├── Sonify.xcodeproj/
│   ├── project.pbxproj                      # Committed valid Xcode project (opens directly in Xcode)
│   └── xcshareddata/
│       └── xcschemes/
│           └── Sonify.xcscheme              # Shared scheme with Sonify + SonifyTests
├── Sonify/
│   ├── Info.plist
│   ├── SonifyApp.swift                      # @main entry point, dark theme, EnvironmentObjects
│   ├── Models/
│   │   ├── Track.swift                      # Track, Artist, Album, HistoryItem
│   │   ├── CatalogModels.swift              # HomeResponse, HomeSection, SearchResponse, TopResult, ArtistDetailResponse, AlbumDetailResponse
│   │   ├── LibraryModels.swift              # Playlist, LikesResponse, PlaylistsResponse, PlaylistDetailResponse, Settings
│   │   └── StreamAndLyricsModels.swift      # HealthResponse, StreamInfoResponse, LyricsResponse, LyricLine, APIErrorEnvelope
│   ├── Services/
│   │   ├── APIClient.swift                  # URLSession async/await REST client + configurable baseURL + error mapping
│   │   ├── AudioPlayerService.swift         # AVPlayer + AVAudioSession + NowPlaying + RemoteCommands + Queue/Shuffle/Repeat + periodic time observer
│   │   ├── LyricsService.swift              # Fetch /api/lyrics + binary-search active line index + local LRC parser helper
│   │   └── LibraryStore.swift               # ObservableObject syncing likes, playlists, history, region & settings with backend
│   ├── Theme/
│   │   └── SonifyTheme.swift                # Spotify dark tokens (#000000, #121212, #181818, #282828, #1DB954, #B3B3B3)
│   ├── Components/
│   │   ├── ArtworkView.swift                # AsyncImage + SVG/error gradient fallback + corner radius
│   │   ├── TrackRowView.swift               # Cover, title, artist, duration, like heart, context menu (Add to Playlist, Add to Queue, Go to Artist/Album)
│   │   ├── MiniPlayerBar.swift              # Floating Spotify-style mini player above TabView with progress bar, play/pause, next, tap-to-expand
│   │   ├── AirPlayButton.swift              # AVRoutePickerView wrapper
│   │   └── AddToPlaylistSheet.swift         # Modal sheet to add any track to an existing or newly created playlist
│   ├── Views/
│   │   ├── MainTabView.swift                # 3-tab Spotify navigation (Home, Search, Your Library) + floating MiniPlayerBar + fullScreenCover NowPlayingView
│   │   ├── HomeView.swift                   # Greeting header, Global/VN region segmented pill, Settings gear, horizontal carousels (Trending, VN, Albums, Indie, Artists), pull-to-refresh
│   │   ├── SearchView.swift                 # Debounced live search (300ms), Top Result hero card, filter chips (All/Tracks/Artists/Albums), browse mood/genre cards when empty
│   │   ├── LibraryView.swift                # Liked Songs card, Recently Played link, Custom Playlists list, "+" Create Playlist sheet, swipe-to-delete
│   │   ├── DetailViews.swift                # ArtistDetailView, AlbumDetailView, PlaylistDetailView (rename/delete/reorder/remove), LikedSongsView, HistoryView
│   │   ├── NowPlayingView.swift             # Full-screen Spotify player: dynamic artwork backdrop, stream badge (FLAC/Full/30s Preview + Ad-Free), interactive scrubber, Shuffle/Prev/Play-Pause/Next/Repeat, Like, AirPlay, Queue sheet, Lyrics toggle
│   │   ├── LyricsView.swift                 # Real-time synced lyrics view (auto-scroll to active line via ScrollViewReader, bold white active line, dimmed past/upcoming, tap-any-line-to-seek, plain lyrics & instrumental fallback)
│   │   ├── QueueSheetView.swift             # Now Playing + Up Next reorderable/removable queue sheet
│   │   └── SettingsView.swift               # Server URL configuration (preset: Live Render `https://sonify-music.onrender.com` or custom LAN URL) + Connection Health Test + Region + Clear History
│   └── Resources/
│       ├── PrivacyInfo.xcprivacy
│       └── Assets.xcassets/
│           ├── Contents.json
│           ├── AccentColor.colorset/Contents.json
│           ├── LaunchBackground.colorset/Contents.json
│           └── AppIcon.appiconset/
│               ├── Contents.json
│               └── AppIcon.png              # 1024x1024 RGB-only PNG (zero alpha channel)
└── SonifyTests/
    ├── ModelDecodingTests.swift             # Verifies decoding of exact backend JSON fixtures (Home, Search, StreamInfo, Lyrics, Playlist, Settings, Error)
    ├── LyricsSyncTests.swift                # Verifies activeLineIndex binary search, interlude handling, and local LRC timestamp parsing
    └── QueueLogicTests.swift                # Verifies queue play/next/prev, repeat off/all/one, shuffle order, and stream URL extension resolution
```

### 4.2 Design Tokens (`SonifyTheme.swift`)
Match our web app's dark Spotify aesthetic (`frontend/css/tokens.css`):
- `bgBase`: `#000000`
- `bgSurface`: `#121212`
- `bgElevated`: `#181818`
- `bgHighlight`: `#282828`
- `accentGreen`: `#1DB954`
- `textPrimary`: `#FFFFFF`
- `textSecondary`: `#B3B3B3`
- `textSubdued`: `#727272`
- `danger`: `#F15E6C`

---

## 5. Free GitHub Actions macOS Xcode CI / TestFlight Pipeline

**File**: `.github/workflows/ios-build.yml`

Uses GitHub's free `macos-latest` runner (for public repos like `vinhtran211/sonify`, macOS Actions minutes are free!) with two jobs:

1. **Job 1 — `build-and-test`** (runs on `push` to `main` touching `ios/**` or `.github/workflows/ios-build.yml`, on `pull_request`, and on `workflow_dispatch`):
   - Selects the latest installed Xcode on `macos-latest` (`ls -d /Applications/Xcode*.app | sort -V | tail -n 1`) and prints `xcodebuild -version`.
   - Installs `xcodegen` via Homebrew (`brew install xcodegen`) and runs `xcodegen generate --spec ios/project.yml` to guarantee `project.yml` stays valid and builds cleanly.
   - Resolves an available iOS Simulator dynamically (`xcrun simctl list devices available -j` -> first available iPhone simulator) so the job never breaks when GitHub updates simulator runtimes!
   - Runs **`xcodebuild test`** on `Sonify.xcodeproj` (`-scheme Sonify -destination "platform=iOS Simulator,id=$SIM_ID" CODE_SIGNING_ALLOWED=NO`).
   - Builds an **unsigned device `.app`** (`-sdk iphoneos -configuration Release CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO`) and packages it into `Payload/Sonify.app` -> **`Sonify-unsigned.ipa`** uploaded as a GitHub Actions artifact (ready for personal sideloading via AltStore / SideStore / Sideloadly!).
   - Validates `PrivacyInfo.xcprivacy` is present inside `Sonify.app` and `AppIcon.png` is `1024×1024` RGB without alpha.

2. **Job 2 — `release-testflight`** (runs ONLY on `workflow_dispatch` when `upload_to_testflight == true`, guarded cleanly with secret checks):
   - Required GitHub Repository Secrets (documented in `docs/IOS_RELEASE_GUIDE.md` / `README.md`):
     - `APPLE_CERTIFICATE_BASE64` (base64 `.p12` Distribution certificate)
     - `APPLE_CERTIFICATE_PASSWORD`
     - `APPLE_PROVISIONING_PROFILE_BASE64` (base64 App Store `.mobileprovision`)
     - `APPLE_TEAM_ID`
     - `IOS_BUNDLE_ID` (optional override, defaults to `com.sonify.music`)
     - `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_PRIVATE_KEY_BASE64` (App Store Connect API Key `.p8`)
   - Creates ephemeral keychain, imports `.p12` + provisioning profile, archives (`xcodebuild archive` with `CURRENT_PROJECT_VERSION=${{ github.run_number }}`), exports signed App Store `.ipa` (`method: app-store-connect`), and uploads directly to TestFlight / App Store Connect via Apple's official built-in `xcrun altool --upload-app --type ios --file ... --apiKey ... --apiIssuer ...` (zero fragile third-party actions!).
   - Always cleans up temporary keychain and `.p8` key in `if: always()`.

---

## 6. iOS PWA Upgrade Specification (`frontend/`)

Gives iPhone/iPad Safari users an immediate zero-install home-screen app experience ("Share -> Add to Home Screen") alongside the native SwiftUI app:

1. **Web App Manifest (`frontend/manifest.json`)**:
   - `name`: `"Sonify — Ad-Free Music"`
   - `short_name`: `"Sonify"`
   - `start_url`: `"/"`, `scope`: `"/"`, `display`: `"standalone"`
   - `background_color`: `"#000000"`, `theme_color`: `"#000000"`
   - `icons`: `/icons/icon-192.png` (192×192), `/icons/icon-512.png` (512×512, `purpose: "any maskable"`)
   - Note: `backend/server.py` already serves `.json` and `.png` from `frontend/` with proper MIME types! Add `.webmanifest` -> `application/manifest+json` just in case.

2. **Generated PNG Icons (`frontend/icons/`)**:
   - `apple-touch-icon.png` (180×180 RGB PNG)
   - `icon-192.png` (192×192 RGB PNG)
   - `icon-512.png` (512×512 RGB PNG)
   - Matching Sonify's `#1DB954` green emblem + dark `#000000` waves.

3. **iOS Meta Tags (`frontend/index.html`)**:
   - Update viewport: `<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />`
   - Add:
     ```html
     <link rel="manifest" href="/manifest.json" />
     <link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png" />
     <meta name="apple-mobile-web-app-capable" content="yes" />
     <meta name="mobile-web-app-capable" content="yes" />
     <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
     <meta name="apple-mobile-web-app-title" content="Sonify" />
     ```

4. **iPhone Notch / Dynamic Island / Home Indicator Safe-Area CSS (`frontend/css/layout.css`)**:
   - Use `height: 100dvh;` (with `100vh` fallback) on `.app-shell` so iOS Safari toolbar collapse never clips the bottom player/nav!
   - Under `@media (max-width: 767px)`:
     - `.topbar`: `padding-top: env(safe-area-inset-top, 0px); height: calc(56px + env(safe-area-inset-top, 0px));`
     - `.mobile-nav`: `padding-bottom: env(safe-area-inset-bottom, 0px); height: calc(var(--mobile-nav-height) + env(safe-area-inset-bottom, 0px));`
     - `.app-shell`: `grid-template-rows: minmax(0, 1fr) var(--player-height) calc(var(--mobile-nav-height) + env(safe-area-inset-bottom, 0px));`
     - `.right-drawer`: `top: env(safe-area-inset-top, 0px); bottom: calc(var(--player-height) + var(--mobile-nav-height) + env(safe-area-inset-bottom, 0px));`
   - Add `-webkit-tap-highlight-color: transparent;` for native app touch feel.

5. **Lightweight Service Worker (`frontend/sw.js` + registration in `frontend/js/main.js`)**:
   - Caches static shell assets ONLY (`/`, `/index.html`, `/manifest.json`, `/favicon.svg`, `/css/*`, `/js/*`, `/icons/*`) with stale-while-revalidate / network-first for HTML/JS/CSS.
   - **STRICT RULE**: NEVER intercept or cache `/api/stream/*` (or any `/api/*` request) — return early without calling `event.respondWith()` so HTTP `Range` audio streaming and live API mutations bypass the Service Worker with zero overhead or iOS Safari Range bugs!
   - Register safely in `frontend/js/main.js` (`if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/sw.js').catch(() => {}); }`).

---

## 7. QA & Validation Checklist (For `@tester`)

1. **Backend & Streaming Compatibility Tests (`tests/`)**:
   - `GET /api/stream/{track_id}/info` returns both `streamUrl` and `streamUrlWithExt` with accurate extension (`.flac`, `.m4a`, `.mp4`, `.mp3`, `.wav`) and normalized `contentType` (`audio/x-m4p` -> `audio/mp4`).
   - `GET /api/stream/{track_id}.flac`, `.m4a`, `.mp4`, `.mp3`, `.wav` all strip the extension cleanly and serve valid `200`/`206` audio with `Content-Disposition: inline; filename="stream.{ext}"`.
   - `Range: bytes=0-1` (iOS `AVPlayer` probe) returns `206 Partial Content`, `Content-Range: bytes 0-1/{total}`, `Content-Length: 2`, and triggers **only 1** upstream Monochrome fetch when followed by `bytes=0-65535`.
   - Existing 56/56 backend unit/integration tests continue passing 100%.
2. **iOS PWA Verification**:
   - `GET /manifest.json`, `GET /sw.js`, `GET /icons/apple-touch-icon.png`, `/icons/icon-192.png`, `/icons/icon-512.png` all return `HTTP 200` with exact MIME types and valid PNG dimensions.
   - `index.html` contains all required iOS PWA meta tags & `viewport-fit=cover`; `layout.css` contains `env(safe-area-inset-top)` and `env(safe-area-inset-bottom)`; `sw.js` explicitly skips `/api/`.
3. **Native iOS Project & App Store Readiness Verification**:
   - Validate `ios/Sonify/Info.plist` & `ios/Sonify/Resources/PrivacyInfo.xcprivacy` via Python `plistlib.loads()` (valid XML plist, required keys `UIBackgroundModes=["audio"]`, `ITSAppUsesNonExemptEncryption=False`, `NSAllowsLocalNetworking=True`, `NSPrivacyTracking=False`, `NSPrivacyAccessedAPICategoryUserDefaults` + `CA92.1`).
   - Validate `ios/Sonify/Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png` via PNG IHDR header: exact `1024×1024` AND `color_type == 2` (RGB, zero alpha channel!).
   - Validate `ios/project.yml`, `ios/Sonify.xcodeproj/project.pbxproj`, `ios/Sonify.xcodeproj/xcshareddata/xcschemes/Sonify.xcscheme`, and `.github/workflows/ios-build.yml` (all referenced Swift files exist on disk, zero missing file refs, valid YAML/XML/PBXProj syntax, zero third-party Swift package dependencies, zero `import` of non-Apple modules).
   - Static Swift contract audit: confirm all `Codable` struct field names in `ios/Sonify/Models/*.swift` match live backend JSON responses 1:1.
