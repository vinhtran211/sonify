"""Automated tests for Native SwiftUI iOS App, App Store readiness, iOS PWA upgrade, and AVPlayer backend streaming."""

from __future__ import annotations

import json
import os
import plistlib
import re
import struct
import tempfile
import threading
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from backend import http_client
from backend.providers import monochrome
from backend.server import create_server
from backend.services.stream_service import (
    ResolvedStream,
    mime_to_extension,
    normalize_stream_mime,
    strip_known_stream_extension,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
IOS_DIR = ROOT_DIR / "ios"
FRONTEND_DIR = ROOT_DIR / "frontend"


def _read_png_ihdr(path: Path):
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a valid PNG"
    assert raw[12:16] == b"IHDR", f"{path} missing IHDR chunk"
    return struct.unpack(">IIBB", raw[16:26])  # (width, height, bit_depth, color_type)


class TestIOSBackendAVPlayerCompatibility(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "ios_avplayer.db")
        self.srv = create_server(host="127.0.0.1", port=0, db_path=self.db_path, offline=False, quiet=True)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_mime_normalization_extension_mapping_and_strip_helpers(self):
        self.assertEqual(normalize_stream_mime("audio/x-m4p"), "audio/mp4")
        self.assertEqual(normalize_stream_mime("audio/x-m4a"), "audio/mp4")
        self.assertEqual(normalize_stream_mime("audio/x-flac; charset=binary"), "audio/flac")
        self.assertEqual(normalize_stream_mime("audio/x-wav"), "audio/wav")

        self.assertEqual(mime_to_extension("audio/flac"), ".flac")
        self.assertEqual(mime_to_extension("audio/x-m4p"), ".m4a")
        self.assertEqual(mime_to_extension("video/mp4"), ".mp4")
        self.assertEqual(mime_to_extension("audio/mpeg"), ".mp3")
        self.assertEqual(mime_to_extension("audio/wav"), ".wav")

        for ext in (".flac", ".m4a", ".mp4", ".mp3", ".wav", ".aac", ".ogg", ".webm"):
            self.assertEqual(strip_known_stream_extension(f"dz:908604612{ext}"), "dz:908604612")

        # Unknown dot suffixes must NOT be stripped from track IDs
        self.assertEqual(strip_known_stream_extension("local:song.v2"), "local:song.v2")

    def test_stream_info_with_ext_and_ios_avplayer_2byte_probe_single_upstream_fetch(self):
        head_chunk = b"fLaC" + bytes(i % 256 for i in range(65532))
        tail_chunk = bytes((i * 3) % 256 for i in range(196608))
        full_flac = head_chunk + tail_chunk
        open_calls = []

        class _MockFlacResp:
            def __init__(self):
                self._offset = 0

            def read(self, n: int = 65536) -> bytes:
                if self._offset >= len(full_flac):
                    return b""
                out = full_flac[self._offset : self._offset + n]
                self._offset += len(out)
                return out

            def close(self) -> None:
                pass

        def fake_open_stream(url, **kw):
            open_calls.append(url)
            return 200, {
                "content-type": "audio/x-flac",
                "content-length": str(len(full_flac)),
            }, _MockFlacResp()

        cache_dir = Path(self._tmp.name) / "mc_avplayer_cache"
        self.srv.stream._store_cache(
            ResolvedStream(
                track_id="dz:908604612",
                mode="full",
                source="monochrome",
                content_type="audio/x-flac",
                url=f"{monochrome.MONOCHROME_BASE}/track/776655",
                headers={"User-Agent": "SonifyTest"},
            )
        )

        with mock.patch.object(monochrome, "CACHE_DIR", cache_dir), \
             mock.patch.object(monochrome, "PACE_INTERVAL_SEC", 0.0), \
             mock.patch.dict(monochrome._ACTIVE_DOWNLOADS, {}, clear=True), \
             mock.patch.dict(monochrome._MIME_CACHE, {}, clear=True), \
             mock.patch("backend.http_client.open_stream", side_effect=fake_open_stream):
            # 1. /info returns normalized contentType + streamUrlWithExt (.flac)
            with urllib.request.urlopen(f"{self.base}/api/stream/dz%3A908604612/info", timeout=5) as r:
                info = json.loads(r.read().decode("utf-8"))
                self.assertEqual(info["contentType"], "audio/flac")
                self.assertEqual(info["streamUrl"], "/api/stream/dz:908604612")
                self.assertEqual(info["streamUrlWithExt"], "/api/stream/dz:908604612.flac")
                self.assertTrue(info["adFree"])

            # 2. iOS AVPlayer initial 2-byte probe: Range: bytes=0-1 on .flac extension route
            req_probe = urllib.request.Request(
                f"{self.base}/api/stream/dz%3A908604612.flac",
                headers={"Range": "bytes=0-1"},
            )
            with urllib.request.urlopen(req_probe, timeout=5) as r2:
                body2 = r2.read()
                self.assertEqual(r2.status, 206)
                self.assertEqual(body2, b"fL")
                self.assertEqual(r2.headers.get("Content-Length"), "2")
                self.assertEqual(r2.headers.get("Content-Range"), f"bytes 0-1/{len(full_flac)}")
                self.assertEqual(r2.headers.get("Content-Type"), "audio/flac")
                self.assertEqual(r2.headers.get("Accept-Ranges"), "bytes")
                self.assertEqual(r2.headers.get("Content-Disposition"), 'inline; filename="stream.flac"')

            # 3. Follow-up bytes=0-65535 shares the SINGLE upstream fetch (call_count == 1)
            req_main = urllib.request.Request(
                f"{self.base}/api/stream/dz%3A908604612.flac",
                headers={"Range": "bytes=0-65535"},
            )
            with urllib.request.urlopen(req_main, timeout=5) as r3:
                body3 = r3.read()
                self.assertEqual(r3.status, 206)
                self.assertEqual(len(body3), 65536)
                self.assertEqual(body3, head_chunk)

            self.assertEqual(len(open_calls), 1, f"Expected 1 upstream Monochrome fetch, got {len(open_calls)}")

        # 4. Also verify .wav / .m4a / .mp3 / .mp4 extension stripping on local bytes
        for ext, ctype, expect_disp in (
            (".wav", "audio/wav", 'inline; filename="stream.wav"'),
            (".m4a", "audio/x-m4p", 'inline; filename="stream.m4a"'),
            (".mp3", "audio/mpeg", 'inline; filename="stream.mp3"'),
            (".mp4", "video/mp4", 'inline; filename="stream.mp4"'),
        ):
            tid = f"dz:exttest{ext[1:]}"
            self.srv.stream._store_cache(
                ResolvedStream(
                    track_id=tid,
                    mode="full",
                    source="soundcloud",
                    content_type=ctype,
                    local_bytes=b"0123456789ABCDEF" * 64,
                )
            )
            req = urllib.request.Request(f"{self.base}/api/stream/{tid}{ext}", headers={"Range": "bytes=0-15"})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 206)
                self.assertEqual(r.read(), b"0123456789ABCDEF")
                self.assertEqual(r.headers.get("Content-Disposition"), expect_disp)
                self.assertEqual(r.headers.get("Content-Type"), normalize_stream_mime(ctype))


class TestIOSPWAUpgrade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.srv = create_server(
            host="127.0.0.1",
            port=0,
            db_path=os.path.join(self._tmp.name, "pwa.db"),
            offline=True,
            quiet=True,
        )
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        http_client.set_offline_mode(None)
        self._tmp.cleanup()

    def test_pwa_manifest_icons_sw_and_safe_area_css(self):
        # 1. /manifest.json HTTP 200
        with urllib.request.urlopen(f"{self.base}/manifest.json", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("json", r.headers.get("Content-Type", ""))
            manifest = json.loads(r.read().decode("utf-8"))
            self.assertEqual(manifest["short_name"], "Sonify")
            self.assertEqual(manifest["display"], "standalone")
            self.assertEqual(manifest["start_url"], "/")

        # 2. /sw.js HTTP 200 & explicitly bypasses /api/
        with urllib.request.urlopen(f"{self.base}/sw.js", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("javascript", r.headers.get("Content-Type", ""))
            sw_src = r.read().decode("utf-8")
            self.assertIn("url.pathname.startsWith('/api/')", sw_src)

        # 3. PWA icons HTTP 200 & exact RGB PNG dimensions
        for path, dims in (
            ("/icons/apple-touch-icon.png", (180, 180)),
            ("/icons/icon-192.png", (192, 192)),
            ("/icons/icon-512.png", (512, 512)),
        ):
            with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as r:
                self.assertEqual(r.status, 200, path)
                self.assertIn("image/png", r.headers.get("Content-Type", ""))
                raw = r.read()
                self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
                w, h, bit_depth, color_type = struct.unpack(">IIBB", raw[16:26])
                self.assertEqual((w, h), dims, path)
                self.assertEqual((bit_depth, color_type), (8, 2), f"{path} should be 8-bit RGB")

        # 4. index.html iOS meta tags & viewport-fit=cover
        html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        for needle in (
            "viewport-fit=cover",
            'rel="manifest" href="/manifest.json"',
            'rel="apple-touch-icon"',
            'name="apple-mobile-web-app-capable" content="yes"',
            'name="apple-mobile-web-app-status-bar-style" content="black-translucent"',
            'name="apple-mobile-web-app-title" content="Sonify"',
        ):
            self.assertIn(needle, html)

        # 5. layout.css dynamic viewport height & iPhone safe-area insets
        layout_css = (FRONTEND_DIR / "css" / "layout.css").read_text(encoding="utf-8")
        self.assertIn("100dvh", layout_css)
        self.assertIn("env(safe-area-inset-top", layout_css)
        self.assertIn("env(safe-area-inset-bottom", layout_css)

        # 6. main.js registers /sw.js
        main_js = (FRONTEND_DIR / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("navigator.serviceWorker.register('/sw.js')", main_js)


class TestNativeIOSAppStoreReadiness(unittest.TestCase):
    def test_info_plist_privacy_manifest_and_app_icon_rgb_no_alpha(self):
        # 1. Info.plist
        info = plistlib.loads((IOS_DIR / "Sonify" / "Info.plist").read_bytes())
        self.assertIn("audio", info.get("UIBackgroundModes", []))
        self.assertIs(info.get("ITSAppUsesNonExemptEncryption"), False)
        self.assertEqual(info.get("UIUserInterfaceStyle"), "Dark")
        self.assertIn("UILaunchScreen", info)
        ats = info.get("NSAppTransportSecurity") or {}
        self.assertIs(ats.get("NSAllowsLocalNetworking"), True)
        self.assertIs(ats.get("NSAllowsArbitraryLoads"), False)

        # 2. PrivacyInfo.xcprivacy
        priv = plistlib.loads((IOS_DIR / "Sonify" / "Resources" / "PrivacyInfo.xcprivacy").read_bytes())
        self.assertIs(priv.get("NSPrivacyTracking"), False)
        self.assertEqual(priv.get("NSPrivacyCollectedDataTypes"), [])
        accessed = priv.get("NSPrivacyAccessedAPITypes") or []
        ud_entries = [
            e for e in accessed if e.get("NSPrivacyAccessedAPIType") == "NSPrivacyAccessedAPICategoryUserDefaults"
        ]
        self.assertEqual(len(ud_entries), 1)
        self.assertIn("CA92.1", ud_entries[0].get("NSPrivacyAccessedAPITypeReasons", []))

        # 3. 1024x1024 8-bit RGB AppIcon.png (ZERO alpha channel, Apple App Store rule)
        icon_path = IOS_DIR / "Sonify" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon.png"
        self.assertEqual(_read_png_ihdr(icon_path), (1024, 1024, 8, 2))

        # 4. Asset catalog colorsets referenced by Info.plist & project.yml exist and are valid JSON
        for rel in (
            "Assets.xcassets/Contents.json",
            "Assets.xcassets/AppIcon.appiconset/Contents.json",
            "Assets.xcassets/AccentColor.colorset/Contents.json",
            "Assets.xcassets/LaunchBackground.colorset/Contents.json",
        ):
            data = json.loads((IOS_DIR / "Sonify" / "Resources" / rel).read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)

    def test_xcode_project_project_yml_scheme_and_github_actions_ci(self):
        # 1. ios/project.yml (must NOT have `info:` block that overwrites Info.plist on xcodegen!)
        proj_yml = (IOS_DIR / "project.yml").read_text(encoding="utf-8")
        self.assertIn('SWIFT_VERSION: "5.9"', proj_yml)
        self.assertIn('IPHONEOS_DEPLOYMENT_TARGET: "16.0"', proj_yml)
        self.assertIn("INFOPLIST_FILE: Sonify/Info.plist", proj_yml)
        self.assertIn("GENERATE_INFOPLIST_FILE: NO", proj_yml)
        self.assertNotIn("\n    info:\n", proj_yml, "project.yml must not overwrite existing Info.plist via info:")
        self.assertNotIn("packages:", proj_yml, "Zero third-party SwiftPM dependencies expected")

        # 2. project.pbxproj references all Swift files on disk & mandatory resources
        pbx = (IOS_DIR / "Sonify.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
        disk_swifts = {p.name for p in IOS_DIR.rglob("*.swift")}
        pbx_swifts = set(re.findall(r"\b([A-Za-z0-9_]+\.swift)\b", pbx))
        self.assertGreaterEqual(len(disk_swifts), 25)
        self.assertEqual(disk_swifts - pbx_swifts, set(), f"Swift files missing from pbxproj: {disk_swifts - pbx_swifts}")
        for res in ("PrivacyInfo.xcprivacy", "Assets.xcassets", "Info.plist"):
            self.assertIn(res, pbx)

        # 3. Shared scheme XML is valid and references both Sonify and SonifyTests
        scheme_xml = ET.fromstring(
            (IOS_DIR / "Sonify.xcodeproj" / "xcshareddata" / "xcschemes" / "Sonify.xcscheme").read_text(encoding="utf-8")
        )
        self.assertEqual(scheme_xml.tag, "Scheme")
        scheme_text = ET.tostring(scheme_xml, encoding="unicode")
        self.assertIn("Sonify.app", scheme_text)
        self.assertIn("SonifyTests.xctest", scheme_text)

        # 4. GitHub Actions macOS Xcode workflow
        ci_yml = (ROOT_DIR / ".github" / "workflows" / "ios-build.yml").read_text(encoding="utf-8")
        for needle in (
            "runs-on: macos-latest",
            "xcodegen generate --spec ios/project.yml",
            "xcodebuild test",
            "Sonify-unsigned.ipa",
            "workflow_dispatch",
            "upload_to_testflight",
            "xcrun altool --upload-app",
            "if: always()",
        ):
            self.assertIn(needle, ci_yml)
        # Ensure plist validation runs AFTER xcodegen generate
        self.assertLess(ci_yml.index("xcodegen generate"), ci_yml.index("Validate App Store Plists"))

        # 5. .dockerignore excludes ios/ and .github/
        dockerignore = (ROOT_DIR / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("ios/", dockerignore)
        self.assertIn(".github/", dockerignore)

    def test_swift_sources_apple_only_imports_explicit_combine_and_backend_contract_parity(self):
        allowed_frameworks = {
            "Foundation",
            "SwiftUI",
            "Combine",
            "AVFoundation",
            "AVKit",
            "MediaPlayer",
            "UIKit",
            "XCTest",
            "Sonify",
        }
        swift_files = sorted(IOS_DIR.rglob("*.swift"))
        self.assertGreaterEqual(len(swift_files), 25)

        for sf in swift_files:
            src = sf.read_text(encoding="utf-8")
            imports = set(re.findall(r"^\s*(?:@testable\s+)?import\s+(\w+)", src, re.M))
            unknown = imports - allowed_frameworks
            self.assertEqual(unknown, set(), f"Non-Apple framework import in {sf.name}: {unknown}")

            # Explicit Combine import whenever ObservableObject / @Published is used without SwiftUI
            if re.search(r"\b(ObservableObject|@Published)\b", src) and "SwiftUI" not in imports:
                self.assertIn("Combine", imports, f"{sf.name} uses ObservableObject/@Published without import Combine")

            # Explicit UIKit import whenever UIKit types are referenced
            if re.search(r"\b(UITabBar|UIImage|UIColor|UIView)\b", src):
                self.assertIn("UIKit", imports, f"{sf.name} uses UIKit types without import UIKit")

            # Zero ad/tracker references in Swift code
            lower = src.lower()
            for banned in ("doubleclick", "admob", "googlesyndication", "app-tracking-transparency"):
                self.assertNotIn(banned, lower, f"Banned pattern '{banned}' in {sf.name}")

        # Verify Swift Codable models match live offline backend JSON contract keys 1:1
        models_text = "\n".join(
            p.read_text(encoding="utf-8") for p in sorted((IOS_DIR / "Sonify" / "Models").glob("*.swift"))
        )
        for key in (
            "streamUrlWithExt",
            "repeatMode = \"repeat\"",
            "let time: Double",
            "let topResult: TopResult?",
            "let likedIds: [String]",
            "let alreadyInPlaylist: Bool",
            "let renderRegion: String?",
            "let ytDlpConfigured: Bool?",
        ):
            self.assertIn(key, models_text, f"Missing expected Swift Codable contract '{key}'")


if __name__ == "__main__":
    unittest.main()
