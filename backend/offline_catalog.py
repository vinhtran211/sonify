"""Curated offline fallback catalog, synced LRC lyrics, SVG covers, and stereo WAV synthesizer."""

from __future__ import annotations

import array
import hashlib
import html
import io
import math
import threading
import wave
from typing import Dict, List, Optional, Tuple

_WAV_CACHE: Dict[str, bytes] = {}
_WAV_LOCK = threading.Lock()

# Curated offline fallback catalog used ONLY when upstream networks are unreachable
# or for explicit local:* demo tracks.
OFFLINE_ARTISTS: List[Dict] = [
    {
        "id": "local:artist-nova",
        "source": "local",
        "name": "Nova Pulse",
        "pictureSmall": "/api/artwork/artist-nova",
        "pictureLarge": "/api/artwork/artist-nova",
        "fans": 2450000,
    },
    {
        "id": "local:artist-linh",
        "source": "local",
        "name": "Minh Linh",
        "pictureSmall": "/api/artwork/artist-linh",
        "pictureLarge": "/api/artwork/artist-linh",
        "fans": 1890000,
    },
    {
        "id": "local:artist-velvet",
        "source": "local",
        "name": "Velvet Static",
        "pictureSmall": "/api/artwork/artist-velvet",
        "pictureLarge": "/api/artwork/artist-velvet",
        "fans": 1340000,
    },
    {
        "id": "local:artist-solaris",
        "source": "local",
        "name": "Solaris Drift",
        "pictureSmall": "/api/artwork/artist-solaris",
        "pictureLarge": "/api/artwork/artist-solaris",
        "fans": 980000,
    },
]

OFFLINE_ALBUMS: List[Dict] = [
    {
        "id": "local:album-afterglow",
        "source": "local",
        "title": "Neon Horizon",
        "artist": "Nova Pulse",
        "artistId": "local:artist-nova",
        "coverSmall": "/api/artwork/neon-horizon",
        "coverLarge": "/api/artwork/neon-horizon",
        "releaseDate": "2026-02-14",
        "trackCount": 3,
    },
    {
        "id": "local:album-saigon",
        "source": "local",
        "title": "Đêm Sài Gòn",
        "artist": "Minh Linh",
        "artistId": "local:artist-linh",
        "coverSmall": "/api/artwork/dem-saigon",
        "coverLarge": "/api/artwork/dem-saigon",
        "releaseDate": "2026-03-08",
        "trackCount": 3,
    },
    {
        "id": "local:album-midnight",
        "source": "local",
        "title": "Midnight Telemetry",
        "artist": "Velvet Static",
        "artistId": "local:artist-velvet",
        "coverSmall": "/api/artwork/midnight-telemetry",
        "coverLarge": "/api/artwork/midnight-telemetry",
        "releaseDate": "2026-01-20",
        "trackCount": 2,
    },
    {
        "id": "local:album-horizon",
        "source": "local",
        "title": "Golden Hour Tape",
        "artist": "Solaris Drift",
        "artistId": "local:artist-solaris",
        "coverSmall": "/api/artwork/golden-hour",
        "coverLarge": "/api/artwork/golden-hour",
        "releaseDate": "2026-04-01",
        "trackCount": 2,
    },
]

OFFLINE_TRACKS: List[Dict] = [
    {
        "id": "local:neon-horizon",
        "source": "local",
        "title": "Neon Horizon",
        "artist": "Nova Pulse",
        "artistId": "local:artist-nova",
        "album": "Neon Horizon",
        "albumId": "local:album-afterglow",
        "duration": 45,
        "coverSmall": "/api/artwork/neon-horizon",
        "coverLarge": "/api/artwork/neon-horizon",
        "explicit": False,
        "releaseDate": "2026-02-14",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:neon-horizon",
        "region": "global",
    },
    {
        "id": "local:midnight-drive",
        "source": "local",
        "title": "Midnight Drive",
        "artist": "Nova Pulse",
        "artistId": "local:artist-nova",
        "album": "Neon Horizon",
        "albumId": "local:album-afterglow",
        "duration": 45,
        "coverSmall": "/api/artwork/midnight-drive",
        "coverLarge": "/api/artwork/midnight-drive",
        "explicit": False,
        "releaseDate": "2026-02-14",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:midnight-drive",
        "region": "global",
    },
    {
        "id": "local:starlight-echo",
        "source": "local",
        "title": "Starlight Echo",
        "artist": "Nova Pulse",
        "artistId": "local:artist-nova",
        "album": "Neon Horizon",
        "albumId": "local:album-afterglow",
        "duration": 45,
        "coverSmall": "/api/artwork/starlight-echo",
        "coverLarge": "/api/artwork/starlight-echo",
        "explicit": False,
        "releaseDate": "2026-02-14",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:starlight-echo",
        "region": "global",
    },
    {
        "id": "local:dem-saigon",
        "source": "local",
        "title": "Đêm Sài Gòn Không Ngủ",
        "artist": "Minh Linh",
        "artistId": "local:artist-linh",
        "album": "Đêm Sài Gòn",
        "albumId": "local:album-saigon",
        "duration": 45,
        "coverSmall": "/api/artwork/dem-saigon",
        "coverLarge": "/api/artwork/dem-saigon",
        "explicit": False,
        "releaseDate": "2026-03-08",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:dem-saigon",
        "region": "vn",
    },
    {
        "id": "local:mua-thang-tu",
        "source": "local",
        "title": "Mưa Tháng Tư",
        "artist": "Minh Linh",
        "artistId": "local:artist-linh",
        "album": "Đêm Sài Gòn",
        "albumId": "local:album-saigon",
        "duration": 45,
        "coverSmall": "/api/artwork/mua-thang-tu",
        "coverLarge": "/api/artwork/mua-thang-tu",
        "explicit": False,
        "releaseDate": "2026-03-08",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:mua-thang-tu",
        "region": "vn",
    },
    {
        "id": "local:pho-cu-nang-vang",
        "source": "local",
        "title": "Phố Cũ Nắng Vàng",
        "artist": "Minh Linh",
        "artistId": "local:artist-linh",
        "album": "Đêm Sài Gòn",
        "albumId": "local:album-saigon",
        "duration": 45,
        "coverSmall": "/api/artwork/pho-cu",
        "coverLarge": "/api/artwork/pho-cu",
        "explicit": False,
        "releaseDate": "2026-03-08",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:pho-cu-nang-vang",
        "region": "vn",
    },
    {
        "id": "local:velvet-signal",
        "source": "local",
        "title": "Velvet Signal",
        "artist": "Velvet Static",
        "artistId": "local:artist-velvet",
        "album": "Midnight Telemetry",
        "albumId": "local:album-midnight",
        "duration": 45,
        "coverSmall": "/api/artwork/midnight-telemetry",
        "coverLarge": "/api/artwork/midnight-telemetry",
        "explicit": False,
        "releaseDate": "2026-01-20",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:velvet-signal",
        "region": "indie",
    },
    {
        "id": "local:analog-waves",
        "source": "local",
        "title": "Analog Waves (Instrumental)",
        "artist": "Velvet Static",
        "artistId": "local:artist-velvet",
        "album": "Midnight Telemetry",
        "albumId": "local:album-midnight",
        "duration": 45,
        "coverSmall": "/api/artwork/analog-waves",
        "coverLarge": "/api/artwork/analog-waves",
        "explicit": False,
        "releaseDate": "2026-01-20",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:analog-waves",
        "region": "indie",
    },
    {
        "id": "local:golden-hour",
        "source": "local",
        "title": "Golden Hour Drift",
        "artist": "Solaris Drift",
        "artistId": "local:artist-solaris",
        "album": "Golden Hour Tape",
        "albumId": "local:album-horizon",
        "duration": 45,
        "coverSmall": "/api/artwork/golden-hour",
        "coverLarge": "/api/artwork/golden-hour",
        "explicit": False,
        "releaseDate": "2026-04-01",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:golden-hour",
        "region": "indie",
    },
    {
        "id": "local:emerald-sky",
        "source": "local",
        "title": "Emerald Sky",
        "artist": "Solaris Drift",
        "artistId": "local:artist-solaris",
        "album": "Golden Hour Tape",
        "albumId": "local:album-horizon",
        "duration": 45,
        "coverSmall": "/api/artwork/emerald-sky",
        "coverLarge": "/api/artwork/emerald-sky",
        "explicit": False,
        "releaseDate": "2026-04-01",
        "previewUrl": "",
        "streamUrl": "/api/stream/local:emerald-sky",
        "region": "global",
    },
]

OFFLINE_LYRICS: Dict[str, Dict] = {
    "local:neon-horizon": {
        "instrumental": False,
        "syncedLrc": """[ar:Nova Pulse]
[ti:Neon Horizon]
[00:01.00]City lights are waking in the pouring rain
[00:05.20]Static on the dashboard calling out your name
[00:09.50]Every midnight signal painting violet skies
[00:13.80]Chasing the horizon till the morning arrives
[00:18.00]
[00:20.20]Hold on to the frequency, don't fade away
[00:24.60]Turning all the shadows into shades of day
[00:29.00]Dancing on the skyline where the echoes gleam
[00:33.50]We are wide awake inside a neon dream
[00:38.20]Running past the quiet, floating out of time
[00:41.80]Neon horizon burning bright tonight""",
    },
    "local:midnight-drive": {
        "instrumental": False,
        "syncedLrc": """[00:01.50]Headlights cutting through the silver haze
[00:06.00]Stereo is humming our favorite phrase
[00:10.50]Windows down to feel the ocean breeze
[00:15.00]Every mile is putting my mind at ease
[00:20.00]No destination on the glowing map
[00:24.80]Just the rhythm steady in the midnight lap
[00:29.60]Stars align above the coastal lane
[00:34.40]Washing out the noise and all the rain
[00:39.20]Keep the engine singing till the sunrise comes""",
    },
    "local:starlight-echo": {
        "instrumental": False,
        "syncedLrc": """[00:01.20]Across the glass a trail of amber gold
[00:06.40]Stories that the constellations never told
[00:11.60]Hear the starlight echo softly through the dark
[00:16.80]Every quiet heartbeat leaves a glowing spark
[00:22.40]Gravity is letting go beneath our feet
[00:27.60]Floating in the chorus of a weightless beat
[00:33.00]Stay here in the shimmer where the colors blend
[00:38.50]Starlight echoes never have to end""",
    },
    "local:dem-saigon": {
        "instrumental": False,
        "syncedLrc": """[ar:Minh Linh]
[ti:Đêm Sài Gòn Không Ngủ]
[00:01.00]Ánh đèn vàng trải dài qua từng góc phố quen
[00:05.50]Tiếng xe đi vội vã trong màn sương rất êm
[00:10.00]Ly cà phê còn ấm trên bàn tay lặng yên
[00:14.80]Đêm Sài Gòn không ngủ giữ lại bao nỗi niềm
[00:19.20]
[00:21.50]Gió sông thổi nhẹ qua hàng cây xanh nghiêng mình
[00:26.20]Những giai điệu ngân lên như lời ca chân tình
[00:31.00]Dẫu ngày mai còn nhiều bộn bề đang chờ ta
[00:35.80]Đêm nay cứ thả trôi theo nhịp tim thiết tha
[00:40.50]Sài Gòn rực sáng mãi trong giấc mơ ngân nga""",
    },
    "local:mua-thang-tu": {
        "instrumental": False,
        "syncedLrc": """[00:01.20]Cơn mưa tháng tư rơi nhẹ bên khung cửa nhỏ
[00:06.00]Hương hoa sữa thoảng bay theo làn mây bỏ ngỏ
[00:11.00]Từng giọt trong veo gõ nhịp trên mái hiên nhà
[00:16.20]Gợi nhớ kỷ niệm xưa như thước phim chưa nhòa
[00:21.50]Mình đứng dưới hiên nghe bản tình ca dịu êm
[00:26.80]Giữa phố đông người qua vẫn thấy lòng bình yên
[00:32.20]Chờ cầu vồng lên sau màn mưa tháng tư này
[00:37.80]Để nắng mới ùa về sưởi ấm đôi bàn tay""",
    },
    "local:pho-cu-nang-vang": {
        "instrumental": False,
        "syncedLrc": """[00:01.50]Nắng chiều buông lơi trên con phố cũ dịu dàng
[00:06.50]Hàng me xanh rì rào kể chuyện mùa thu sang
[00:11.80]Tiếng đàn guitar vang lên từ quán nhỏ quen
[00:17.20]Mọi âu lo hôm nay bỗng hóa thành êm đềm
[00:22.80]Bước chân thong dong đi qua từng nhịp cầu xưa
[00:28.20]Giữ lại chút hương thơm của nắng sau cơn mưa
[00:34.00]Phố cũ nắng vàng vẫn đợi ta quay về
[00:39.20]Nơi thanh xuân rực rỡ như lời hứa vẹn thề""",
    },
    "local:velvet-signal": {
        "instrumental": False,
        "syncedLrc": """[00:01.50]Warm tape hiss in a quiet room
[00:06.50]Velvet chords cut through the afternoon gloom
[00:11.80]Tuning dials until the signal clears
[00:17.00]Soft analog glow after all these years
[00:22.50]Spinning slow on a midnight reel
[00:28.00]Every harmonic says just what we feel
[00:34.00]Let the velvet signal carry us home
[00:39.50]Never out of tune, never alone""",
    },
    "local:analog-waves": {
        "instrumental": True,
        "syncedLrc": "",
    },
    "local:golden-hour": {
        "instrumental": False,
        "syncedLrc": """[00:01.40]Sunlight spills across the balcony floor
[00:06.60]Warm amber breeze drifts in through the door
[00:12.00]Golden hour pauses every ticking clock
[00:17.40]Footsteps slow down on the harbor dock
[00:23.00]Waves reflect the honey in the fading sky
[00:28.60]Watching crimson clouds go drifting by
[00:34.20]Hold this quiet glow before the stars appear
[00:39.60]Everything we need is already right here""",
    },
    "local:emerald-sky": {
        "instrumental": False,
        "syncedLrc": """[00:01.50]Underneath an emerald northern sky
[00:06.50]Ribbons of light begin to rise and fly
[00:11.80]Crisp night air and a melody so bright
[00:17.20]Guiding every dreamer through the velvet night
[00:22.80]Hear the bassline pulse like distant thunder low
[00:28.40]Shimmering waves in a steady emerald glow
[00:34.00]Reach up high where the aurora bends
[00:39.40]Music carries on where the skyline ends""",
    },
}


_ARTWORK_PALETTES: Dict[str, Tuple[str, str, str, str]] = {
    "neon-horizon": ("#1db954", "#0f2027", "#450af5", "NEON HORIZON"),
    "midnight-drive": ("#1e3c72", "#2a5298", "#1db954", "MIDNIGHT DRIVE"),
    "starlight-echo": ("#3a1c71", "#d76d77", "#ffaf7b", "STARLIGHT ECHO"),
    "dem-saigon": ("#f953c6", "#b91d73", "#121212", "ĐÊM SÀI GÒN"),
    "mua-thang-tu": ("#00b4db", "#0083b0", "#182848", "MƯA THÁNG TƯ"),
    "pho-cu": ("#f7971e", "#ffd200", "#2c1a00", "PHỐ CŨ"),
    "midnight-telemetry": ("#141e30", "#243b55", "#1ed760", "TELEMETRY"),
    "analog-waves": ("#4b6cb7", "#182848", "#1db954", "ANALOG WAVES"),
    "golden-hour": ("#ff512f", "#f09819", "#1f1c2c", "GOLDEN HOUR"),
    "emerald-sky": ("#11998e", "#38ef7d", "#0b1e1b", "EMERALD SKY"),
    "artist-nova": ("#1db954", "#121212", "#450af5", "NOVA PULSE"),
    "artist-linh": ("#ec008c", "#fc6767", "#1a1a1a", "MINH LINH"),
    "artist-velvet": ("#4776e6", "#8e54e9", "#121212", "VELVET STATIC"),
    "artist-solaris": ("#f2994a", "#f2c94c", "#1e1b18", "SOLARIS DRIFT"),
}


def render_artwork_svg(slug: str) -> bytes:
    """Render a crisp deterministic SVG album/artist cover for offline & fallback art."""
    clean = (slug or "sonify").strip().lower()
    if clean in _ARTWORK_PALETTES:
        c1, c2, c3, label = _ARTWORK_PALETTES[clean]
    else:
        digest = hashlib.sha256(clean.encode("utf-8")).digest()
        h1 = (digest[0] * 360) // 255
        h2 = (h1 + 55 + (digest[1] % 90)) % 360
        c1 = f"hsl({h1}, 72%, 44%)"
        c2 = f"hsl({h2}, 62%, 22%)"
        c3 = f"hsl({(h1 + 140) % 360}, 75%, 52%)"
        label = (slug or "SONIFY").replace("-", " ").replace("_", " ").upper()[:18]

    safe_label = html.escape(label)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 500" width="500" height="500">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="65%" stop-color="{c2}"/>
      <stop offset="100%" stop-color="#0b0b0c"/>
    </linearGradient>
    <radialGradient id="glow" cx="72%" cy="28%" r="55%">
      <stop offset="0%" stop-color="{c3}" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="{c3}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="500" height="500" fill="url(#bg)"/>
  <rect width="500" height="500" fill="url(#glow)"/>
  <circle cx="250" cy="220" r="118" fill="rgba(0,0,0,0.22)" stroke="rgba(255,255,255,0.18)" stroke-width="2"/>
  <circle cx="250" cy="220" r="82" fill="none" stroke="rgba(255,255,255,0.12)" stroke-width="1.5"/>
  <circle cx="250" cy="220" r="30" fill="rgba(255,255,255,0.88)"/>
  <circle cx="250" cy="220" r="9" fill="#121212"/>
  <path d="M168 354 Q250 328 332 354" fill="none" stroke="rgba(255,255,255,0.75)" stroke-width="6" stroke-linecap="round"/>
  <path d="M192 374 Q250 355 308 374" fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="5" stroke-linecap="round"/>
  <text x="250" y="438" text-anchor="middle" fill="#ffffff" font-family="system-ui, -apple-system, sans-serif" font-size="26" font-weight="800" letter-spacing="2">{safe_label}</text>
</svg>"""
    return svg.encode("utf-8")


def get_offline_track(track_id: str) -> Optional[Dict]:
    for t in OFFLINE_TRACKS:
        if t["id"] == track_id:
            return dict(t)
    return None


def get_offline_album(album_id: str) -> Optional[Tuple[Dict, List[Dict]]]:
    for alb in OFFLINE_ALBUMS:
        if alb["id"] == album_id:
            tracks = [dict(t) for t in OFFLINE_TRACKS if t["albumId"] == album_id]
            album_copy = dict(alb)
            album_copy["totalDuration"] = sum(t.get("duration", 0) for t in tracks)
            return album_copy, tracks
    return None


def get_offline_artist(artist_id: str) -> Optional[Tuple[Dict, List[Dict], List[Dict]]]:
    for art in OFFLINE_ARTISTS:
        if art["id"] == artist_id:
            tracks = [dict(t) for t in OFFLINE_TRACKS if t["artistId"] == artist_id]
            albums = [dict(a) for a in OFFLINE_ALBUMS if a["artistId"] == artist_id]
            return dict(art), tracks, albums
    return None


def get_offline_lyrics(track_id: str) -> Optional[Dict]:
    return OFFLINE_LYRICS.get(track_id)


# Musical chord progressions (MIDI note numbers) for rich offline audio synthesis
_PROGRESSIONS = [
    [(57, 60, 64, 67), (53, 57, 60, 64), (48, 52, 55, 60), (55, 59, 62, 67)],  # Am7 - Fmaj7 - C - G
    [(60, 64, 67, 71), (57, 60, 64, 67), (53, 57, 60, 64), (55, 59, 62, 65)],  # Cmaj7 - Am7 - Fmaj7 - G7
    [(62, 65, 69, 72), (55, 59, 62, 65), (60, 64, 67, 71), (57, 60, 64, 67)],  # Dm7 - G7 - Cmaj7 - Am7
    [(52, 55, 59, 62), (48, 52, 55, 59), (55, 59, 62, 66), (50, 54, 57, 62)],  # Em7 - Cmaj7 - Gmaj7 - D
    [(55, 58, 62, 65), (51, 55, 58, 62), (58, 62, 65, 69), (53, 57, 60, 65)],  # Gm7 - Ebmaj7 - Bbmaj7 - F
]

_WAVETABLE_SIZE = 2048
_SINE_TABLE = [math.sin(2.0 * math.pi * i / _WAVETABLE_SIZE) for i in range(_WAVETABLE_SIZE)]
_WARM_TABLE = [
    0.72 * math.sin(2.0 * math.pi * i / _WAVETABLE_SIZE)
    + 0.20 * math.sin(4.0 * math.pi * i / _WAVETABLE_SIZE)
    + 0.08 * math.sin(6.0 * math.pi * i / _WAVETABLE_SIZE)
    for i in range(_WAVETABLE_SIZE)
]


def _midi_to_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def synthesize_track_wav(track_id: str, duration: int = 45) -> bytes:
    """Generate a pleasant, deterministic 45-second stereo 16-bit PCM 44.1kHz WAV stream.

    Synthesizes a 7.5-second 4-chord progression with lush warm pad, bassline, and
    gentle stereo arpeggio seeded deterministically by hash(track_id), then stitches
    6 seamless cycles to 45 seconds for fast (<150ms) pure-Python generation.
    """
    cache_key = f"{track_id}:{duration}"
    with _WAV_LOCK:
        cached = _WAV_CACHE.get(cache_key)
        if cached is not None:
            return cached

    sample_rate = 44100
    seed = int.from_bytes(hashlib.sha256(track_id.encode("utf-8")).digest()[:8], "big")
    prog = _PROGRESSIONS[seed % len(_PROGRESSIONS)]
    transpose = ((seed >> 4) % 7) - 3  # -3 to +3 semitones

    # Render one 4-chord cycle (7.5s = 4 * 1.875s) at 22050Hz then 2x linear upsample to 44100Hz
    # -> silky warm low-pass character AND ~4x faster pure-Python rendering!
    chord_sec = 1.875
    half_sr = 22050
    chord_frames = int(chord_sec * half_sr)
    cycle_frames = chord_frames * 4
    arp_step_frames = chord_frames // 4  # 16th-bar arpeggio steps

    sine = _SINE_TABLE
    warm = _WARM_TABLE
    tsize = _WAVETABLE_SIZE
    tmask = tsize - 1

    cycle_left = array.array("h", [0] * cycle_frames)
    cycle_right = array.array("h", [0] * cycle_frames)

    phase_scale = tsize / half_sr

    for c_idx, chord in enumerate(prog):
        notes = [n + transpose for n in chord]
        bass_hz = _midi_to_hz(notes[0] - 12)
        pad_hz = [_midi_to_hz(n) for n in notes]
        arp_hz = [_midi_to_hz(notes[i % len(notes)] + 12) for i in range(4)]

        bass_inc = bass_hz * phase_scale
        p0_inc, p1_inc, p2_inc, p3_inc = [h * phase_scale for h in pad_hz]
        arp_incs = [h * phase_scale for h in arp_hz]

        base_offset = c_idx * chord_frames
        inv_chord = 1.0 / chord_frames
        inv_arp = 1.0 / arp_step_frames

        bp = p0 = p1 = p2 = p3 = 0.0
        ap = 0.0
        last_step = -1

        for i in range(chord_frames):
            # Smooth chord pad envelope
            t_norm = i * inv_chord
            if t_norm < 0.06:
                pad_env = t_norm / 0.06
            elif t_norm > 0.90:
                pad_env = (1.0 - t_norm) / 0.10
            else:
                pad_env = 1.0

            # Gentle pulsing bass
            bass_env = 0.65 + 0.35 * (1.0 - ((i % (chord_frames // 2)) / (chord_frames // 2)))

            # Plucky stereo arpeggio note
            step = i // arp_step_frames
            if step > 3:
                step = 3
            if step != last_step:
                ap = 0.0
                last_step = step
            arp_t = (i - step * arp_step_frames) * inv_arp
            arp_env = (arp_t / 0.03) if arp_t < 0.03 else math.exp(-3.8 * (arp_t - 0.03))

            b_val = sine[int(bp) & tmask] * 0.24 * bass_env
            pad_l = (warm[int(p0) & tmask] + 0.85 * warm[int(p2) & tmask]) * 0.16 * pad_env
            pad_r = (warm[int(p1) & tmask] + 0.85 * warm[int(p3) & tmask]) * 0.16 * pad_env
            a_val = sine[int(ap) & tmask] * 0.20 * arp_env

            pan_l = 0.72 if (step % 2 == 0) else 0.38
            pan_r = 0.38 if (step % 2 == 0) else 0.72

            left = b_val + pad_l + a_val * pan_l
            right = b_val + pad_r + a_val * pan_r

            idx = base_offset + i
            cycle_left[idx] = max(-32000, min(32000, int(left * 29000)))
            cycle_right[idx] = max(-32000, min(32000, int(right * 29000)))

            bp += bass_inc
            p0 += p0_inc
            p1 += p1_inc
            p2 += p2_inc
            p3 += p3_inc
            ap += arp_incs[step]

    # Upsample 2x (22050 -> 44100) and interleave stereo for one 7.5s cycle
    cycle_stereo = array.array("h", [0] * (cycle_frames * 4))
    for i in range(cycle_frames):
        l0 = cycle_left[i]
        r0 = cycle_right[i]
        nxt = (i + 1) % cycle_frames
        l1 = (l0 + cycle_left[nxt]) >> 1
        r1 = (r0 + cycle_right[nxt]) >> 1
        j = i * 4
        cycle_stereo[j] = l0
        cycle_stereo[j + 1] = r0
        cycle_stereo[j + 2] = l1
        cycle_stereo[j + 3] = r1

    cycle_bytes = cycle_stereo.tobytes()
    total_frames = int(duration * sample_rate)
    bytes_per_frame = 4  # 2 channels * 2 bytes
    target_bytes = total_frames * bytes_per_frame

    repeats = (target_bytes // len(cycle_bytes)) + 1
    pcm = bytearray((cycle_bytes * repeats)[:target_bytes])

    # Apply smooth 0.35s fade-in and 0.6s fade-out on full PCM buffer
    full_samples = array.array("h")
    full_samples.frombytes(pcm)
    fade_in_frames = min(int(0.35 * sample_rate), total_frames // 4)
    fade_out_frames = min(int(0.60 * sample_rate), total_frames // 4)
    for f in range(fade_in_frames):
        g = f / fade_in_frames
        full_samples[2 * f] = int(full_samples[2 * f] * g)
        full_samples[2 * f + 1] = int(full_samples[2 * f + 1] * g)
    for f in range(fade_out_frames):
        g = (fade_out_frames - f) / fade_out_frames
        pos = total_frames - fade_out_frames + f
        full_samples[2 * pos] = int(full_samples[2 * pos] * g)
        full_samples[2 * pos + 1] = int(full_samples[2 * pos + 1] * g)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(full_samples.tobytes())

    wav_bytes = buf.getvalue()
    with _WAV_LOCK:
        # Keep cache bounded to 16 tracks (~120MB max)
        if len(_WAV_CACHE) >= 16:
            _WAV_CACHE.pop(next(iter(_WAV_CACHE)))
        _WAV_CACHE[cache_key] = wav_bytes
    return wav_bytes
