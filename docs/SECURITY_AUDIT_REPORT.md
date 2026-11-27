# Sonify Security Audit, White-Box Threat Model & Remediation Spec

**Target Codebase**: `/mnt/e/lam lai tu dau/music` (Backend Python stdlib/SQLite, Vanilla ES2022+ Web/PWA, Native SwiftUI iOS, `Dockerfile`, `render.yaml`, `.github/workflows/ios-build.yml`)  
**Methodology**: Line-by-line white-box code review against OWASP Top 10 (2021) and common rapid-development ("vibe-code") flaws. Every finding below is backed by verified `file:line` evidence in the current repository.  
**Posture**: Strictly defensive analysis — conceptual scenarios only, zero exploit scripts or weaponized payloads.

---

## 1. Confirmed Vulnerability Table

| ID | Vulnerability & OWASP Category | Severity | Exact Evidence (`file:line`) | Conceptual Abuse Scenario | Detailed Remediation |
|---|---|---|---|---|---|
| **VULN-01** | **SSRF & Local File Read via Client-Controlled `tracks_cache._extra` / `previewUrl` + Unvalidated Redirects** *(A10: SSRF)* | **Critical** | `backend/services/library_service.py:25-28, 145-153`; `backend/db.py:213-225, 262, 303`; `backend/services/stream_service.py:937-959, 1291-1325`; `backend/providers/jiosaavn.py:232-234`; `backend/http_client.py:253-296, 347-382` | An unauthenticated client calls `POST /api/library/likes`, `POST /api/playlists/{id}/tracks`, `POST /api/playlists/import`, or `POST /api/history` with a crafted `track` JSON object whose `id` uses the `js:` prefix and includes attacker-supplied `_extra.direct_urls` (or `previewUrl` when `SONIFY_ALLOW_PREVIEWS=1`). `LibraryService` forwards the raw dict to `db.upsert_tracks()`, which merges client `_extra` directly into `tracks_cache.extra_json`. A subsequent `GET /api/stream/js:<id>` triggers `http_client.probe_url()` (`urllib.request.urlopen`) and `open_stream()`, allowing server-side requests to internal/loopback/cloud-metadata IPs (`169.254.169.254`, `127.0.0.1`, RFC1918) or `file://` local paths (and unvalidated HTTP `301–308` redirect hops to internal hosts), potentially proxying readable responses back to the caller. | **Defense in depth (2 layers):**<br>1. **Ingestion (`library_service.py`)**: Add `_sanitize_client_track()` that **strips `_extra` completely**, discards untrusted `previewUrl`, validates `coverSmall`/`coverLarge` to safe `https://`, `http://`, or `/api/artwork/` URLs only, and caps all string lengths.<br>2. **Egress (`http_client.py`)**: Add `validate_outbound_url(url)` enforcing scheme `in ("http", "https")` (blocking `file://`, `ftp://`, `gopher://`, `data://`), resolving DNS via `socket.getaddrinfo()`, and rejecting loopback, private (RFC1918/ULA), link-local (`169.254.0.0/16`, `fe80::/10`), multicast, reserved, and unspecified IPs. Enforce it before every initial request **and** every redirect hop in `probe_url()` (switching from default auto-redirect `urlopen` to `_NO_REDIRECT_OPENER` + manual validated redirect loop), `open_stream()`, and `fetch_bytes()`. |
| **VULN-02** | **Broken Access Control — Unauthenticated Global Shared Library & Cross-User IDOR** *(A01: Broken Access Control)* | **Critical** | `backend/server.py:454-668`; `backend/db.py:35-78, 139-163, 414-836, 905-1132`; `frontend/js/store.js:295-371` | Every mutable library endpoint (`playlists`, `likes`, `followed_artists`, `saved_albums`, `pinned_items`, `history`, `settings`, `stats`) has zero authentication and zero per-user/session ownership scoping in SQLite. On public Render (`https://sonify-music.onrender.com`), any anonymous visitor can read, alter, or delete every other visitor's playlists, likes, listening history, and audio settings — and `store.js:317-371` automatically merges one visitor's browser `localStorage` playlists into the shared server database visible to everyone. | Add automatic **HMAC-SHA256 signed session-cookie isolation** (`sonify_sid`, `HttpOnly; SameSite=Lax; Path=/` + `Secure` over HTTPS, signed with `SONIFY_SESSION_SECRET` or per-boot CSPRNG key) plus `X-Sonify-Session` header support for Native iOS `APIClient` and optional `SONIFY_API_TOKEN` for private single-owner mode. Migrate `playlists`, `liked_tracks`, `play_history`, `settings`, `followed_artists`, `saved_albums`, and `pinned_items` to include `owner_id TEXT NOT NULL DEFAULT 'default'` so every browser/iOS session only reads, modifies, and deletes its own records (returning `404` on cross-owner `playlist_id` access). Keep `owner_id = 'default'` fallback when `SONIFY_REQUIRE_SESSION` is unset for non-cookie local CLI/unit-test compatibility. |
| **VULN-03** | **Stored CSS Injection & Unsafe URL Scheme Injection (`coverColor`, `coverUrl`, `coverLarge`/`coverSmall`)** *(A03: Injection / XSS)* | **High** | `backend/services/library_service.py:93-101`; `backend/db.py:258-259, 970-976, 1041-1046`; `frontend/js/components/trackList.js:25-32`; `frontend/js/views/libraryView.js:328-329`; `frontend/js/lrc.js:136-144`; `frontend/js/components/lyricsPanel.js:256` | `library_service.py:94` stores arbitrary 32-character strings in `playlist.coverColor` with zero format validation; `trackList.js:31` interpolates `bg` into `style="background:linear-gradient(135deg, ${escapeHtml(bg)}, #121212);"` (`escapeHtml` does not block `;`, `(`, `)`, or CSS functions) and `libraryView.js:329` assigns it directly into `heroEl.style.background`. Likewise, `library_service.py:99` permits protocol-relative `//evil.example/...` URLs (starts with `/`) and CSS-breaking characters, `db.py` stores unvalidated `coverSmall`/`coverLarge`/`artwork_url` (allowing `javascript:`/`data:` schemes), `lrc.js:136-144` returns them verbatim, and `lyricsPanel.js:256` embeds `art` inside `style="background-image: url('${escapeHtml(art)}');"` where HTML entity decoding restores `'` before CSS parsing. | 1. **Backend (`library_service.py`, `db.py`)**: Strictly validate `coverColor` against `^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$` (empty or 3/6-digit hex only). Validate all image/cover URLs (`coverUrl`, `coverSmall`, `coverLarge`, `artwork_url`) with a shared `sanitize_media_url()` permitting ONLY `https://...`, `http://...`, or single-slash relative `/api/artwork/...` (rejecting `//`, `javascript:`, `data:`, control chars, quotes, parentheses, and whitespace).<br>2. **Frontend (`lrc.js`, `trackList.js`, `libraryView.js`, `lyricsPanel.js`)**: Add `sanitizeCssColor()` and `sanitizeSafeUrl()` in `lrc.js`; apply them inside `coverUrlFor()`, `renderPlaylistCoverHtml()`, `libraryView.js:329`, and replace inline `style="background-image: url(...)"` in `lyricsPanel.js:256` with safe DOM property assignment on a validated URL. |
| **VULN-04** | **Host / `X-Forwarded-Host` Header Spoofing in iOS `.mobileconfig`, OTA Manifest & AltStore Source** *(A05: Security Misconfiguration)* | **High** | `backend/services/ios_install_service.py:16-39, 71-152, 155-209`; `backend/server.py:236-273` | `resolve_public_origin()` trusts any `X-Forwarded-Host` or `Host` header matching `[a-zA-Z0-9._:\-\[\]]+` without host allowlisting. If a reverse proxy or user-supplied header supplies an attacker domain, `GET /api/ios/install.mobileconfig`, `/api/ios/install-info`, `/api/ios/manifest.plist`, and `/api/ios/altstore-source.json` embed the attacker's origin into Apple iOS Home Screen Web Clip profiles (`URL` key) and install manifests, enabling convincing iPhone Home Screen phishing impersonating Sonify. | Add `SONIFY_PUBLIC_ORIGIN` and `SONIFY_ALLOWED_HOSTS` (plus automatic `RENDER_EXTERNAL_HOSTNAME` allowlisting on Render) in `backend/config.py`. When `SONIFY_PUBLIC_ORIGIN` is configured, always return it verbatim; when `SONIFY_ALLOWED_HOSTS` / `RENDER_EXTERNAL_HOSTNAME` is present, validate the incoming host against the allowlist (ignoring untrusted `X-Forwarded-Host` when outside the allowlist and falling back safely). |
| **VULN-05** | **Unvalidated Provider IDs Passed into `yt-dlp` Subprocess & Upstream URL Paths** *(A03: Injection)* | **High** | `backend/providers/youtube_music.py:398-426, 682-770`; `backend/services/stream_service.py:368, 910-998`; `backend/providers/monochrome.py:253-254`; `backend/providers/audius.py:161-163` | URL-decoded `raw_id` values from `/api/stream/{prefix}:{raw_id}` are interpolated directly into `yt-dlp` arguments (`youtube_music.py:419`, missing `--` end-of-options separator) and upstream REST paths (`{inst}/streams/{video_id}`, `{inst}/api/v1/videos/{video_id}`). Crafted IDs containing `&`, `?`, `#`, `../`, or leading dashes can smuggle extra URL query parameters into `yt-dlp` (e.g., triggering playlist/other behavior) or traverse upstream API endpoints. Also `YTDLP_PROXY` / `YTDLP_COOKIES_PATH` lack leading-`-` option guards. | Enforce strict regex validation per provider prefix in a central helper (`yt`: `^[A-Za-z0-9_-]{6,20}$`; `dz`/`it`: `^\d{1,20}$`; `mc`: `^[A-Za-z0-9_-]{1,40}$`; `au`/`js`: `^[A-Za-z0-9_-]{1,64}$`; `local`: `^[a-z0-9_-]{1,64}$`), reject malformed IDs early with `400 INVALID_TRACK`, insert `"--"` before the URL in `youtube_music._resolve_via_yt_dlp()` (`cmd.extend(["--", f"https://www.youtube.com/watch?v={video_id}"])`), and always URL-encode path segments with `urllib.parse.quote(raw_id, safe="")`. |
| **VULN-06** | **Resource Exhaustion / DoS: Unbounded `ThreadingHTTPServer` Threads, Missing Rate Limits, & `import_playlist` Upstream Amplification** *(A04: Insecure Design / Availability)* | **High** | `backend/server.py:46-48, 70-115, 294-399, 548-558`; `backend/services/library_service.py:143-204`; `backend/db.py:252-265, 969-976, 1038-1047` | 1. `SonifyHTTPServer(ThreadingHTTPServer)` spawns unbounded OS threads with no per-socket timeout (`request.settimeout` unset), allowing slow-client / concurrency exhaustion on Render's 512MB container.<br>2. Zero per-IP rate limiting exists on expensive endpoints (`/api/stream/*`, `/api/search`, `/api/home?refresh=1`, `/api/lyrics?refresh=1`, `/api/playlists/import`).<br>3. A single `POST /api/playlists/import` with 200 tracks without `id` synchronously executes up to 200 multi-provider live searches (~1,000+ outbound HTTP calls), starving threads and triggering upstream IP rate-limit bans.<br>4. Uncapped track/artist/album string fields in `db.py` allow megabyte payloads to bloat SQLite and client DOMs. | 1. Add socket timeout (`setup()` sets `self.request.settimeout(30.0)`) and a bounded concurrency semaphore (`MAX_CONCURRENT_REQUESTS = 64`, returning HTTP `503` when saturated) in `backend/server.py`.<br>2. Add lightweight in-memory per-IP token-bucket rate limiter (`backend/security.py`, configurable via `SONIFY_RATE_LIMIT_RPM`, returning `429` + `Retry-After`) covering search, stream resolution, refresh, import, and write endpoints.<br>3. In `library_service.import_playlist()`: cap `m3uText` to 64 KB, cap live catalog search lookups to `20` per import request with a `10s` deadline, and enforce strict length limits on all stored text fields. |
| **VULN-07** | **Information Disclosure via Raw Exception Strings (`str(exc)`) & Public Production `?debug=1` Diagnostics** *(A05: Security Misconfiguration)* | **High** | `backend/server.py:354-366, 678-679`; `backend/services/stream_service.py:1430-1431` | `server.py:679` returns `f"Unexpected server error: {exc}"` in HTTP 500 JSON responses, leaking internal filesystem paths, SQLite error details, or upstream URLs to external callers. In addition, `GET /api/stream/{id}/info?debug=1` is publicly accessible on production cloud deployments without authorization, revealing provider failure codes, latencies, and internal waterfall topology. | 1. In `server.py:678-679`: log the full exception to `sys.stderr` and return generic `"Internal server error"` to clients.<br>2. Gate HTTP `?debug=1` in `server.py:354` so when `config.is_cloud_env()` is `True`, `debug` is forced `False` unless `SONIFY_ALLOW_DEBUG=1` or a matching `X-Sonify-Debug-Token` (`SONIFY_DEBUG_TOKEN`) header is provided (local non-cloud dev & direct Python calls continue working seamlessly). |
| **VULN-08** | **Cross-Site Request Forgery (CSRF) via Missing `Origin` Validation & Missing `Content-Type: application/json` Check on Mutations** *(A01 / A05)* | **High** | `backend/server.py:95-115, 462-668` | `_read_json_body()` parses JSON regardless of the HTTP `Content-Type` header (so a cross-site simple `POST` with `Content-Type: text/plain` bypasses CORS preflight completely!), and `POST`/`PATCH`/`DELETE` handlers perform zero `Origin` or `Sec-Fetch-Site` checks. Any malicious website visited by a Sonify user can silently mutate/delete playlists, likes, history, or settings on either `https://sonify-music.onrender.com` (once session cookies exist) or a developer's local `http://127.0.0.1:8080` instance. | 1. On all `POST`/`PATCH`/`DELETE` API requests, inspect `Origin` and `Sec-Fetch-Site`: if `Origin` is present and does not match the request's own host (or `SONIFY_ALLOWED_ORIGINS`), or `Sec-Fetch-Site == "cross-site"`, immediately return `403 FORBIDDEN`.<br>2. Reject `POST`/`PATCH` requests that have a non-empty body and explicitly send a non-JSON `Content-Type` (e.g., `text/plain`, `application/x-www-form-urlencoded`, `multipart/form-data`) with `415 UNSUPPORTED_MEDIA_TYPE` (preserving existing Python `urllib` tests that send JSON bodies with `application/json` or omit `Content-Type`). |
| **VULN-09** | **Missing HTTP Security Headers (`CSP`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `HSTS`)** *(A05: Security Misconfiguration)* | **Medium** | `backend/server.py:81-91, 158-163, 283-288`; `backend/services/stream_service.py:1434-1448` | No static, API, SVG artwork, or audio stream response includes `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `Content-Security-Policy`, or `Strict-Transport-Security`. Without these headers, the app is vulnerable to clickjacking, MIME-sniffing, and unrestricted script execution if any DOM sink is ever introduced, and `/api/artwork/{slug}` serves inline SVG without `Content-Security-Policy: sandbox`. | Add a central `_send_security_headers()` helper in `SonifyRequestHandler` sending `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Strict-Transport-Security: max-age=31536000; includeSubDomains` (when `X-Forwarded-Proto == https`), a tailored `Content-Security-Policy` on HTML responses (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data: blob:; media-src 'self' https: blob:; connect-src 'self' https:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'`), and `Content-Security-Policy: sandbox` on `/api/artwork/*` SVGs. |
| **VULN-10** | **Predictable `/tmp` Cookie File Path & Creation Permission Race** *(A02 / A05)* | **Medium** | `backend/providers/youtube_music.py:348, 370-383` | `_COOKIES_FILE_PATH = Path("/tmp/sonify/yt_cookies.txt")` writes decoded `YTDLP_COOKIES_B64` session cookies via `write_bytes(raw)` **before** calling `os.chmod(..., 0o600)`, creating a brief window with default umask permissions on a predictable path in shared `/tmp`. | Create `/tmp/sonify` with `mode=0o700` and write cookies atomically using `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)` so permissions are `0600` from creation and symlinks are refused. |
| **VULN-11** | **Native iOS Cleartext HTTP Remote Server Support & Missing Session Header** *(A02: Cryptographic Failures / A01)* | **Medium** | `ios/Sonify/Services/APIClient.swift:46-66, 104-120`; `ios/Sonify/Views/SettingsView.swift:107-121`; `ios/Sonify/Info.plist:27-33` | `APIClient.sanitizeBaseURL()` allows non-local `http://` URLs (Settings accepts arbitrary `http://` hosts) and sends requests without a per-install `X-Sonify-Session` token, so iOS clients fall into the shared default bucket if not isolated. | Update `APIClient.sanitizeBaseURL()` to allow `http://` ONLY for local/LAN addresses (`localhost`, `127.0.0.1`, `10.*`, `192.168.*`, `172.16-31.*`, `*.local`), automatically upgrading public hosts to `https://`. Generate and persist a random per-install UUID in `APIClient` (Keychain or `UserDefaults`) and attach it as `X-Sonify-Session` on all API requests. |
| **VULN-12** | **Unpinned Floating Python Dependencies & Mutable CI Action/Brew Installs** *(A08: Software & Data Integrity Failures)* | **Medium** | `requirements.txt:2-3`; `Dockerfile:10-11`; `.github/workflows/ios-build.yml:28, 39, 114` | `requirements.txt` specifies unpinned lower bounds (`cryptography>=43.0.0`, `yt-dlp>=2025.1.15`) and `Dockerfile` runs `pip install --upgrade pip` on every build, allowing unexpected upstream breaking or compromised package versions to be pulled automatically on Render auto-deploys. | Pin upper bounds or exact tested versions in `requirements.txt` (e.g., `cryptography>=43.0.0,<46.0.0`, `yt-dlp>=2025.1.15,<2027.0.0`) and avoid `pip install --upgrade pip` drift; in `.github/workflows/ios-build.yml`, add explicit minimal `permissions: { contents: read }` at workflow top level. |
| **VULN-13** | **Non-Deterministic `hash()` for Imported Track IDs & Hardcoded Legacy JioSaavn Protocol Key Documentation** *(A02 / Code Quality)* | **Low** | `backend/services/library_service.py:194`; `backend/config.py:105` | `library_service.py:194` uses Python's per-process salted `hash()` to derive `imp:<id>`, producing different IDs after container restarts and colliding across imports. Separately, `config.py:105` defines `JIOSAAVN_DES_KEY = b"38346591"` (note: this is JioSaavn's public protocol obfuscation constant, **not** an app secret, so it is Low/Informational — worth allowing env override via `JIOSAAVN_DES_KEY` for hygiene). | Replace `abs(hash(...)) % 10_000_000` in `library_service.py:194` with `hashlib.sha256(f"{art.lower()}|{ttl.lower()}".encode("utf-8")).hexdigest()[:12]`. Allow optional `os.environ.get("JIOSAAVN_DES_KEY")` override in `config.py:105` with a clarifying comment. |

*(Verified Clean / No Vulnerability Found: SQL queries across `backend/db.py` use parameterized `?` placeholders everywhere — line 173 iterates a hardcoded 3-element tuple; `server.py:135-141` enforces `target.relative_to(self.server.frontend_dir)` blocking static path traversal; `Dockerfile:15,27` already runs as non-root UID 1000; `offline_catalog.py:423` escapes SVG labels with `html.escape()`; `server.py` does NOT set wildcard `Access-Control-Allow-Origin: *`.)*

---

## 2. White-Box Threat Model — Top 5 Realistic Attack Scenarios

### Scenario 1: Cloud Metadata SSRF & Local File Read via Poisoned Track Cache (`VULN-01`)
- **Attacker Profile**: Remote unauthenticated internet attacker targeting `https://sonify-music.onrender.com`.
- **Weaknesses Chained**:
  1. `POST /api/library/likes` (`backend/server.py:466-468`) requires no authentication and passes the client-supplied `track` object straight through `library_service.py:27-28` into `db.upsert_tracks()` (`db.py:213-225`).
  2. `db.upsert_tracks()` merges client-supplied `_extra` (including `direct_urls`) and `previewUrl` into `tracks_cache`.
  3. `GET /api/stream/js:<attacker_id>` (`stream_service.py:937-959`) loads `extra["direct_urls"]` via `jiosaavn.resolve_candidate_urls()` (`jiosaavn.py:232-234`) and feeds them directly to `http_client.probe_url()` (`http_client.py:270-276`), which calls `urllib.request.urlopen()` (supporting `file://` and unvalidated redirects to internal/link-local cloud IPs).
- **Business Impact**: Internal port scanning, probing internal cloud metadata endpoints (`169.254.169.254`), or reading local container files / SQLite database (`file://` or loopback endpoints) proxied back through `/api/stream/*`.
- **How Remediation Closes It**: `_sanitize_client_track()` strips `_extra` and untrusted `previewUrl` at the API boundary so clients can never supply server-fetched URLs, while `http_client.validate_outbound_url()` independently blocks non-HTTP(S) schemes and private/loopback/link-local IPs across both initial requests and every redirect hop.

### Scenario 2: Cross-User Library Takeover, Sabotage & Persistent Graffiti on Shared Render Deployment (`VULN-02` + `VULN-03`)
- **Attacker Profile**: Opportunistic external vandal or malicious user on the public Render URL.
- **Weaknesses Chained**:
  1. All library tables in `backend/db.py:35-78, 139-163` are globally shared across all visitors with zero session isolation (`backend/server.py:454-668`).
  2. Any visitor can list (`GET /api/playlists`), rename/deface (`PATCH /api/playlists/{id}`), or wipe (`DELETE /api/playlists/{id}`, `DELETE /api/history`) every other user's library, and inject fake tracks or abusive playlist names/covers that render on every visitor's Home Quick Picks and Sidebar.
  3. `frontend/js/store.js:317-371` exacerbates this by auto-pushing one user's `localStorage` backup into the shared DB after container restarts.
- **Business Impact**: Complete loss of privacy (exposing listening history & tastes across users), total loss of library integrity (anyone can wipe everyone's playlists), and persistent defacement of the live app for all visitors.
- **How Remediation Closes It**: Automatic HMAC-signed `HttpOnly; SameSite=Lax` session cookies (`sonify_sid`) and `X-Sonify-Session` (iOS) scope every library table row by `owner_id`, ensuring each visitor sees and modifies only their own isolated library and receives `404` on any foreign `playlist_id`.

### Scenario 3: Stored CSS Injection & UI Redressing via Unvalidated `coverColor` and Artwork URLs (`VULN-03` + `VULN-09`)
- **Attacker Profile**: Remote attacker injecting malicious playlist/track metadata (amplified to all users if combined with `VULN-02`, or self/shared-link targeted via imported JSON playlists).
- **Weaknesses Chained**:
  1. `library_service.py:93-94` accepts arbitrary strings up to 32 chars in `coverColor`, interpolated into inline `style="..."` at `trackList.js:31` and `libraryView.js:329`.
  2. `db.py:258-259` stores unvalidated `coverLarge`/`coverSmall` URLs that `lrc.js:136-144` passes directly into `lyricsPanel.js:256` (`style="background-image: url('${escapeHtml(art)}');"`), where HTML attribute decoding unescapes `&#39;` back into `'` before CSS parsing.
  3. Missing `Content-Security-Policy` and `X-Frame-Options` (`backend/server.py:158-163`) provide no browser-level containment against style/exfiltration or clickjacking.
- **Business Impact**: CSS exfiltration / tracking beacons to attacker-controlled domains, full-screen UI redressing/overlay phishing inside the player, and broken/defaced UI rendering.
- **How Remediation Closes It**: Strict hex regex validation (`^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$`) on `coverColor` (server + client), strict `sanitize_media_url()` (`https://`, `http://`, or `/api/artwork/` only, no quotes/parens/newlines/`//`), safe DOM `.style.backgroundImage` handling in `lyricsPanel.js`, and strict CSP + `X-Frame-Options: DENY` headers.

### Scenario 4: iPhone Home Screen Phishing via `X-Forwarded-Host` Poisoning (`VULN-04`)
- **Attacker Profile**: Attacker targeting iPhone users who click Sonify's "Install Directly to iPhone" `.mobileconfig` button (`frontend/js/components/installModal.js:185`) via a spoofed proxy header or crafted link/cache poisoning.
- **Weaknesses Chained**:
  1. `ios_install_service.py:21-25` accepts any syntactically valid `X-Forwarded-Host` / `Host` header without checking an allowed hostname list.
  2. `build_mobileconfig()` (`:71-113`) generates an Apple Web Clip profile pointing `URL` directly at `https://<spoofed-host>/`, branded with Sonify's real icon and name (`Sonify`), and `build_ota_manifest()` / `build_altstore_source()` embed `<spoofed-host>` in all asset URLs.
- **Business Impact**: Victims install a full-screen iOS Home Screen icon titled "Sonify" that launches an attacker-controlled phishing/malware site without an address bar (`FullScreen: True`).
- **How Remediation Closes It**: Pinning the public origin via `SONIFY_PUBLIC_ORIGIN` / `SONIFY_ALLOWED_HOSTS` / `RENDER_EXTERNAL_HOSTNAME` ensures `.mobileconfig`, OTA manifests, and AltStore JSON always point exclusively to the legitimate Sonify origin.

### Scenario 5: Cross-Site Request Forgery (CSRF) & Unbounded Upstream Amplification DoS (`VULN-06` + `VULN-08` + `VULN-05`)
- **Attacker Profile**: Malicious third-party webpage visited by a Sonify user (targeting either public Render or local `http://127.0.0.1:8080`), or an automated script aiming to take down the free-tier Render instance.
- **Weaknesses Chained**:
  1. `_read_json_body()` (`server.py:95-115`) ignores `Content-Type`, allowing a cross-site `text/plain` `POST` request (no CORS preflight) to mutate playlists/likes/settings or trigger `POST /api/playlists/import` (`server.py:548-557`).
  2. `import_playlist()` (`library_service.py:145-190`) fires up to 200 synchronous multi-provider catalog searches per request, while `SonifyHTTPServer` (`server.py:46-48`) spawns unbounded threads with no socket timeout and no per-IP rate limit.
  3. Unvalidated provider IDs in `/api/stream/yt:*` or `/api/stream/au:*` (`stream_service.py:910-984`) let callers fan out malformed upstream requests.
- **Business Impact**: A few concurrent requests exhaust Render's 512MB RAM / thread pool and trigger 429 IP bans from Deezer/iTunes/SoundCloud/Monochrome, causing total denial of service for all users; cross-site pages can silently tamper with a user's library.
- **How Remediation Closes It**: Cross-site `Origin`/`Sec-Fetch-Site` + `Content-Type` enforcement kills CSRF; bounded request concurrency (`Semaphore(64)`), 30s socket timeouts, per-IP token-bucket rate limiting (`429`), strict `import_playlist` lookup caps (`<= 20`, `10s` budget), and strict provider ID regex validation neutralize both local and cloud amplification DoS.

---

## 3. Surgical Remediation Plan for ALL `Critical` & `High` Issues + `.env.example`

> **Zero-Regression Guarantee**: Every change below is designed to preserve all existing features and keep all **104 existing automated tests** passing without breaking public API response shapes.

### 3.1 New Security Utilities Module (`backend/security.py`)
Create a focused stdlib-only security module exporting:
1. **`validate_outbound_url(url: str, *, allow_loopback: bool = False) -> Tuple[bool, str]`**:
   - Reject empty strings, control chars (`\r`, `\n`, `\x00`), or schemes outside `("http", "https")` (blocks `file://`, `ftp://`, `gopher://`, `data://`).
   - Require non-empty `hostname`; reject literal `"localhost"` / `"*.localhost"` unless `allow_loopback=True`.
   - Check literal IP or resolve DNS via `socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)`: reject any resolved address where `ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified` (or IPv4-mapped IPv6 `ip.ipv4_mapped` matching the same!). Only allow `is_loopback` when `allow_loopback=True` (explicit test opt-in or `SONIFY_ALLOW_LOCAL_UPSTREAMS=1`).
2. **`sanitize_media_url(url: str, *, max_len: int = 512) -> str`**:
   - Strip whitespace; reject control chars, quotes (`'`, `"`), parentheses (`(`, `)`), backslashes, and protocol-relative `//`.
   - Allow ONLY `https://...`, `http://...` (valid netloc), or `/api/artwork/...` (single leading `/`, no `..`). Return `""` otherwise.
3. **`is_valid_hex_color(value: str) -> bool`**:
   - Matches `^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$`.
4. **`validate_provider_id(prefix: str, raw_id: str) -> bool`**:
   - `dz` / `it`: `^\d{1,20}$`
   - `yt`: `^[A-Za-z0-9_-]{6,20}$`
   - `mc`: `^[A-Za-z0-9_-]{1,40}$`
   - `au` / `js`: `^[A-Za-z0-9_-]{1,64}$`
   - `local` / `imp`: `^[a-zA-Z0-9._:-]{1,80}$`
5. **HMAC-SHA256 Session Signing (`mint_session_id`, `verify_session_token`, `extract_owner_id`)**:
   - Secret sourced from `config.get_session_secret()` (`SONIFY_SESSION_SECRET` env var, or process-level `secrets.token_bytes(32)` CSPRNG fallback — never a hardcoded string!).
   - Token format: `<24-hex-sid>.<32-hex-hmac-sha256>`, verified with `hmac.compare_digest()`.
   - Accepts either `Cookie: sonify_sid=<token>` (web browsers) or `X-Sonify-Session: <token>` (Native iOS `APIClient`; also accepts unsigned 32-char UUID from iOS when `X-Sonify-Session` matches `^[A-Za-z0-9_-]{20,80}$` hashed via `hmac(secret, raw)` so iOS works immediately without an extra handshake!).
6. **`RateLimiter` (Thread-Safe In-Memory Token Bucket per Client IP)**:
   - Buckets by route class (`write`, `stream`, `search`, `import`, `general`) with generous defaults (e.g., `180 req/min` general/stream, `60 req/min` search/write, `10 req/min` import; overridable via `SONIFY_RATE_LIMIT_RPM`, `0` disables in automated load tests) and automatic stale-IP pruning (`max 4,096` tracked IPs so the limiter itself cannot be OOM'd).

### 3.2 File-by-File Surgical Code Changes

1. **`backend/config.py`**:
   - Add helpers:
     - `get_session_secret() -> bytes` (`SONIFY_SESSION_SECRET`, fallback to per-process `os.urandom(32)`).
     - `get_api_token() -> Optional[str]` (`SONIFY_API_TOKEN`, optional bearer token for private single-owner instances).
     - `is_debug_allowed() -> bool` (`SONIFY_ALLOW_DEBUG=1` or `not is_cloud_env()`).
     - `get_debug_token() -> Optional[str]` (`SONIFY_DEBUG_TOKEN`).
     - `get_public_origin() -> Optional[str]` (`SONIFY_PUBLIC_ORIGIN`, validated `https://` or `http://`).
     - `get_allowed_hosts() -> Set[str]` (`SONIFY_ALLOWED_HOSTS` comma-separated + auto-include `RENDER_EXTERNAL_HOSTNAME` when set).
     - `get_allowed_origins() -> Set[str]` (`SONIFY_ALLOWED_ORIGINS` comma-separated).
     - `get_rate_limit_rpm() -> int` (`SONIFY_RATE_LIMIT_RPM`, default `180`).
     - `JIOSAAVN_DES_KEY`: allow optional `os.environ.get("JIOSAAVN_DES_KEY")` override (with clarifying comment that `"38346591"` is JioSaavn's public protocol obfuscation key, not a secret).

2. **`backend/http_client.py` (Closes VULN-01 at Egress Layer)**:
   - Add `_ALLOW_LOOPBACK_FOR_TESTS: bool = False` + `set_allow_loopback_for_tests(enabled: bool)` helper (mirroring `set_offline_mode()`).
   - Call `security.validate_outbound_url(url, allow_loopback=...)` at the top of `fetch_bytes()`, `probe_url()`, and `open_stream()`.
   - In `probe_url()`: switch from `urllib.request.urlopen()` (which auto-follows unvalidated redirects and accepts `file://`) to `_NO_REDIRECT_OPENER.open()` with a bounded `max_redirects=5` loop that calls `security.validate_outbound_url(next_url)` on **every** `Location` redirect before following it!
   - In `open_stream()`: call `security.validate_outbound_url(current_url)` **both** before the initial request and after `urllib.parse.urljoin(current_url, location)` on every `301–308` redirect hop!
   - In `fetch_bytes()` when `requests.Session` is used: set `allow_redirects=False` and manually follow up to 5 validated `301–308` redirects through `validate_outbound_url()` so `requests` can NEVER redirect to a private/metadata IP either!

3. **`backend/services/library_service.py` (Closes VULN-01 at Ingestion, VULN-03 `coverColor`/`coverUrl`, VULN-06 `import_playlist`, VULN-13 `hash()`)**:
   - Add `_sanitize_client_track(track: Dict[str, Any]) -> Dict[str, Any]`:
     - Explicitly allowlist safe metadata fields only: `id` (`<=128`), `source` (`<=32`), `title` (`<=240`), `artist` (`<=200`), `artistId` (`<=128`), `album` (`<=200`), `albumId` (`<=128`), `duration` (`0..86400`), `coverSmall` (`sanitize_media_url`), `coverLarge` (`sanitize_media_url`), `explicit` (`bool`), `releaseDate` (`<=32`).
     - **NEVER pass client `_extra` or client `previewUrl`** to `db.upsert_track()`!
   - Call `_sanitize_client_track()` inside `_resolve_track_payload()` and `import_playlist()` (lines 151–153), and sanitize artist/album payloads (`name <= 200`, `artwork_url = sanitize_media_url(...)`) in `follow_artist()` and `save_album()`.
   - In `update_playlist()` (`:93–101`):
     - If `cover_color` is non-empty and not `security.is_valid_hex_color(cover_color)`: raise `LibraryError("INVALID_PAYLOAD", "coverColor must be a hex color like #1DB954", 400)`.
     - If `cover_url` is non-empty: validate via `security.sanitize_media_url(cover_url)` (rejecting `//evil.com`, `javascript:`, quotes, parens, control chars) or raise `400 INVALID_PAYLOAD`.
   - In `import_playlist()` (`:143–204`):
     - Cap `m3uText` to `65_536` chars; cap live `catalog_service.search()` lookups across both `tracks` and `m3uText` to `max_live_lookups = 20` with a `10.0s` monotonic deadline!
     - Replace `hash((art.lower(), ttl.lower()))` (`:194`) with `hashlib.sha256(...)`.
   - Thread `owner_id: str = "default"` through all `LibraryService` methods (`get_likes`, `like_track`, `unlike_track`, `list_playlists`, `create_playlist`, `get_playlist`, `update_playlist`, `duplicate_playlist`, `import_playlist`, `delete_playlist`, `add_track_to_playlist`, `remove_track_from_playlist`, `reorder_playlist_tracks`, `get_history`, `record_history`, `clear_history`, `get_settings`, `update_settings`, `get_followed_artists`, `follow_artist`, `unfollow_artist`, `get_saved_albums`, `save_album`, `unsave_album`, `get_pins`, `pin_item`, `unpin_item`, `get_stats`).

4. **`backend/db.py` (Closes VULN-02 Multi-User Data Isolation & VULN-06 String Caps)**:
   - Add safe schema migration in `init_db()` adding `owner_id TEXT NOT NULL DEFAULT 'default'` (migrating single-user tables cleanly without losing existing data):
     - `playlists`: add `owner_id TEXT NOT NULL DEFAULT 'default'` + index `idx_playlists_owner`.
     - `play_history`: add `owner_id TEXT NOT NULL DEFAULT 'default'` + index `idx_history_owner_played`.
     - `liked_tracks`, `settings`, `followed_artists`, `saved_albums`, `pinned_items`: recreate-and-copy safely during `init_db()` if `owner_id` column is absent so their primary keys become `(owner_id, track_id)`, `(owner_id, key)`, `(owner_id, artist_id)`, `(owner_id, album_id)`, `(owner_id, item_type, item_id)`!
   - Update all `Database` library CRUD queries to accept `owner_id: str = "default"` and filter every `SELECT`, `UPDATE`, and `DELETE` by `WHERE owner_id = ?` (defaulting to `"default"` preserves 100% compatibility with all existing direct `db.*` unit tests!).
   - In `upsert_tracks()` (`:250–305`): cap string lengths defensively (`title[:240]`, `artist[:200]`, `album[:200]`, `cover_small[:512]`, `cover_large[:512]`).

5. **`backend/services/ios_install_service.py` (Closes VULN-04 Host Header Spoofing)**:
   - In `resolve_public_origin(headers)`:
     1. If `config.get_public_origin()` is set, return it directly.
     2. Read `allowed_hosts = config.get_allowed_hosts()`.
     3. Only accept `X-Forwarded-Host` / `Host` if it matches `_SAFE_HOST_RE` **AND** (either `not allowed_hosts` or `raw_host.lower()` / host-without-port is in `allowed_hosts`). If `allowed_hosts` is configured and `raw_host` is not in it, fall back to the first allowed host (`https://<primary_host>`) instead of reflecting the attacker's host!

6. **`backend/providers/youtube_music.py` & `backend/services/stream_service.py` (Closes VULN-05 Injection & VULN-10 Temp File Race)**:
   - In `youtube_music.py`:
     - Validate `video_id` with `security.validate_provider_id("yt", video_id)` at the start of `resolve_audio_stream()`, `_resolve_via_yt_dlp()`, `_resolve_via_innertube_player()`, `_resolve_via_piped()`, `_resolve_via_invidious()`, and `fetch_plain_lyrics()`.
     - In `_resolve_via_yt_dlp()` (`:419`): append `"--"` before `f"https://www.youtube.com/watch?v={video_id}"`.
     - In `_get_ytdlp_extra_args()` (`:370–394`): reject `YTDLP_PROXY` / `YTDLP_COOKIES_PATH` starting with `"-"`, create `_COOKIES_FILE_PATH.parent` with `0o700`, and write cookie bytes atomically via `os.open(..., os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)`.
   - In `stream_service.py`:
     - Validate `track_id` prefix/raw_id via `security.validate_provider_id(prefix, raw_id)` in `resolve_stream()` before Tier-0 native URL construction (raising `StreamUnavailableError` / `400` on malformed IDs).

7. **`backend/server.py` (Closes VULN-02 Sessions, VULN-06 Concurrency/Rate-Limit, VULN-07 Error/Debug Disclosure, VULN-08 CSRF, VULN-09 Security Headers)**:
   - Add `request.settimeout(30.0)` in `SonifyRequestHandler.setup()` and a bounded `threading.Semaphore(64)` in `SonifyHTTPServer` (returning `503 SERVICE_UNAVAILABLE` if saturated).
   - Add `_send_security_headers()` called in `_send_json()`, `_serve_static()`, `/api/artwork/*` (with `Content-Security-Policy: sandbox`), and stream responses.
   - Add session cookie resolution (`_resolve_owner_and_cookie()`):
     - If request has valid signed `sonify_sid` cookie or `X-Sonify-Session` header -> use verified `owner_id`.
     - If browser request (`Accept` contains `text/html` or `Sec-Fetch-Mode` present, or `SONIFY_REQUIRE_SESSION=1` on API requests) lacks a valid cookie -> mint a fresh HMAC-signed `sonify_sid` cookie (`HttpOnly; SameSite=Lax; Path=/` + `; Secure` when HTTPS) and emit `Set-Cookie` on the response!
     - If `SONIFY_API_TOKEN` is configured -> require `Authorization: Bearer <token>` (or `X-Sonify-Token`) with `hmac.compare_digest()` on all `/api/*` write endpoints (and optionally all `/api/*` endpoints).
     - Non-browser API calls (like existing `urllib` unit tests) without cookies/headers continue defaulting to `owner_id="default"` when `SONIFY_REQUIRE_SESSION` and `SONIFY_API_TOKEN` are unset.
   - Add CSRF check `_check_csrf()` on `POST`/`PATCH`/`DELETE`:
     - Reject cross-origin `Origin` (not matching request `Host` or `SONIFY_ALLOWED_ORIGINS`) or `Sec-Fetch-Site: cross-site` with `403 FORBIDDEN`.
     - In `_read_json_body()`: if `Content-Type` header is present and non-empty and does NOT contain `application/json` (e.g. `text/plain`, `application/x-www-form-urlencoded`, `multipart/form-data`), raise `LibraryError("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json", 415)`.
   - Add per-IP `RateLimiter.allow()` check in `_dispatch()` returning `429 RATE_LIMITED` + `Retry-After` on burst abuse.
   - Gate `?debug=1` at `:354`: `debug = raw_debug and (config.is_debug_allowed() or valid_debug_token)`.
   - Sanitize `server.py:678-679`: log `exc` to `sys.stderr` and return generic `"Internal server error"`.

8. **Frontend (`frontend/js/lrc.js`, `trackList.js`, `libraryView.js`, `lyricsPanel.js`, `store.js`) & Native iOS (`ios/Sonify/Services/APIClient.swift`)**:
   - In `frontend/js/lrc.js`: export `sanitizeSafeUrl(url)` (allows only `https://`, `http://`, or `/api/artwork/` with no quotes/parens/newlines/`//`) and `sanitizeCssColor(color)` (allows only `^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$`), and enforce `sanitizeSafeUrl` inside `coverUrlFor()`.
   - In `frontend/js/components/trackList.js:25-32` & `frontend/js/views/libraryView.js:328-329`: apply `sanitizeSafeUrl(playlist.coverUrl)` and `sanitizeCssColor(playlist.coverColor)`.
   - In `frontend/js/components/lyricsPanel.js:256`: remove inline `style="background-image: url('${escapeHtml(art)}');"` from HTML string; set `ambientEl.style.backgroundImage = ...` via DOM property ONLY after `sanitizeSafeUrl(art)`!
   - In `ios/Sonify/Services/APIClient.swift`: generate/persist a per-install UUID session token and attach `X-Sonify-Session` on requests; tighten `sanitizeBaseURL()` to upgrade non-local `http://` hosts to `https://`.

9. **`.env.example` & `render.yaml` Hygiene**:
   - Create `.env.example` documenting all security & deployment env vars (zero real secrets!):
     - `SONIFY_HOST`, `SONIFY_PORT`, `SONIFY_DB`
     - `SONIFY_SESSION_SECRET` (32+ byte random hex for signing `sonify_sid` cookies across restarts)
     - `SONIFY_REQUIRE_SESSION` (`1` for strict multi-user isolation on public cloud deployments)
     - `SONIFY_API_TOKEN` (optional single-owner bearer token)
     - `SONIFY_PUBLIC_ORIGIN` (e.g. `https://sonify-music.onrender.com` — pins iOS `.mobileconfig` & OTA origin)
     - `SONIFY_ALLOWED_HOSTS` (comma-separated allowed `Host` / `X-Forwarded-Host` values)
     - `SONIFY_ALLOWED_ORIGINS` (optional comma-separated cross-origin allowlist)
     - `SONIFY_RATE_LIMIT_RPM` (default `180`, `0` to disable)
     - `SONIFY_ALLOW_DEBUG` (`0` in production), `SONIFY_DEBUG_TOKEN` (optional header token for `?debug=1`)
     - `SONIFY_ALLOW_PREVIEWS` (`0` default), `SONIFY_OFFLINE` (`0` default)
     - `SONIFY_TESTFLIGHT_URL`, `SONIFY_IOS_IPA_URL`, `SONIFY_IOS_BUNDLE_ID`, `SONIFY_GITHUB_REPO`
     - `YTDLP_COOKIES_B64`, `YTDLP_COOKIES_PATH`, `YTDLP_PROXY`
   - Update `render.yaml` to include `SONIFY_PUBLIC_ORIGIN: https://sonify-music.onrender.com` and `SONIFY_ALLOW_DEBUG: "0"` (and `SONIFY_SESSION_SECRET` with `generateValue: true` so Render auto-generates a cryptographically strong secret!).

---

## 4. Security QA Verification Checklist (`tests/test_security_hardening.py`)

`@tester` should verify:
1. **All 104 existing automated tests pass (`0` regressions)** — note: in `tests/test_stream_and_waterfall.py`, `TestProbeUrlAndStrictMatchers` and `StreamProxyServerTest` test against local `127.0.0.1` mock CDN servers and should call `http_client.set_allow_loopback_for_tests(True)` in `setUp()` / `False` in `tearDown()`!
2. **New `tests/test_security_hardening.py` covering every Critical & High fix**:
   - **SSRF Layer 1 & 2 (`VULN-01`)**:
     - `POST /api/library/likes` with `_extra.direct_urls` & `previewUrl` does NOT store `_extra` or untrusted `previewUrl` in `tracks_cache`.
     - `http_client.validate_outbound_url()` blocks `file:///etc/passwd`, `http://169.254.169.254/latest/meta-data/`, `http://127.0.0.1:8080/`, `http://10.0.0.1/`, `http://192.168.1.1/`, `http://[::1]/`, and redirects from a public URL to `127.0.0.1`/`169.254.169.254`.
   - **Session Isolation & IDOR (`VULN-02`)**:
     - Session A creates a playlist, likes a track, and sets volume `0.3`; Session B sees empty playlists/likes, default volume `0.8`, and gets `404` when attempting `GET`/`PATCH`/`DELETE` on Session A's `playlist_id`.
     - Tampered `sonify_sid` cookies are rejected and replaced with a fresh isolated session; `SONIFY_API_TOKEN` rejects missing/wrong tokens with `401` and accepts valid tokens.
   - **CSS / URL Injection (`VULN-03`)**:
     - `PATCH /api/playlists/{id}` rejects malicious `coverColor` (`red);background:url(//x)`) and `coverUrl` (`javascript:alert(1)`, `//evil.com/x.png`, `https://x/' )`) with `400`, while accepting valid `#7C3AED` and `https://cdn.example.com/cov.jpg`.
     - Static frontend checks verify `sanitizeSafeUrl` & `sanitizeCssColor` in `lrc.js`, `trackList.js`, `libraryView.js`, and DOM-safe `lyricsPanel.js`.
   - **Host Header Spoofing (`VULN-04`)**:
     - With `SONIFY_PUBLIC_ORIGIN` or `SONIFY_ALLOWED_HOSTS` / `RENDER_EXTERNAL_HOSTNAME` set, `X-Forwarded-Host: evil.attacker.com` is ignored and `.mobileconfig` / `/api/ios/install-info` contain ONLY the pinned legitimate origin.
   - **Provider ID / `yt-dlp` Validation (`VULN-05`)**:
     - Malformed `yt:` IDs (`--exec=foo`, `abc&list=123`, `../../etc`) are rejected before `yt-dlp` or Piped/Invidious URL construction, and `youtube_music._resolve_via_yt_dlp()` includes `"--"` before the watch URL.
   - **CSRF, Rate Limiting, Concurrency, Error Sanitization & Security Headers (`VULN-06`–`VULN-09`)**:
     - Cross-origin `Origin: https://evil.example` on `POST`/`PATCH`/`DELETE` returns `403`, and `Content-Type: text/plain` on `POST` returns `415`.
     - Burst exceeding configured `SONIFY_RATE_LIMIT_RPM` returns `429` with `Retry-After`.
     - `import_playlist()` caps live search fan-out to `<= 20`.
     - Unhandled `500` errors return `"Internal server error"` (never leaking `str(exc)`), cloud `?debug=1` omits `diagnostics` unless `SONIFY_ALLOW_DEBUG=1`, and responses include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, and `Content-Security-Policy`.
   - **Swift / iOS Hygiene (`VULN-11`)**:
     - All 31 Swift files remain balanced & Apple-only, and `APIClient.swift` includes `X-Sonify-Session` and upgrades non-local `http://` to `https://`.
