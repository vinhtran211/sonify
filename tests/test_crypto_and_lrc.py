"""Unit tests for DES-ECB decryptor, LRC timestamp parser, normalizers, and offline synthesis."""

from __future__ import annotations

import io
import struct
import unittest
import wave

from backend import crypto_des, lrc_parser, offline_catalog


class TestCryptoDes(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip_and_pure_parity(self):
        samples = [
            "https://aac.saavncdn.com/123/abcd_96.mp4",
            "http://aac.saavncdn.com/999/test_track_96.mp4?token=xyz",
            "https://a.co/1234567",  # exact 16-byte multiple + extra
            "12345678",              # exact 8-byte block boundary
        ]
        for url in samples:
            enc_crypto = crypto_des.encrypt_media_url(url, use_pure=False)
            enc_pure = crypto_des.encrypt_media_url(url, use_pure=True)
            self.assertEqual(enc_crypto, enc_pure, f"Parity mismatch for {url}")

            expected = url.replace("http://", "https://", 1)
            self.assertEqual(crypto_des.decrypt_media_url(enc_crypto, use_pure=False), expected)
            self.assertEqual(crypto_des.decrypt_media_url(enc_crypto, use_pure=True), expected)

    def test_empty_and_invalid_padding(self):
        self.assertEqual(crypto_des.decrypt_media_url(""), "")
        with self.assertRaises(ValueError):
            crypto_des.pkcs7_unpad(b"short", 8)
        with self.assertRaises(ValueError):
            crypto_des.pkcs7_unpad(b"1234567\x00", 8)
        with self.assertRaises(ValueError):
            crypto_des.pkcs7_unpad(b"123456\x01\x02", 8)
        with self.assertRaises(ValueError):
            crypto_des.des_ecb_decrypt_pure(b"12345")

    def test_upgrade_bitrate_candidates(self):
        cands = crypto_des.upgrade_bitrate_candidates(
            "https://aac.saavncdn.com/001/song_96.mp4?sig=abc", prefer_320=True
        )
        self.assertEqual(
            cands,
            [
                "https://aac.saavncdn.com/001/song_320.mp4?sig=abc",
                "https://aac.saavncdn.com/001/song_160.mp4?sig=abc",
                "https://aac.saavncdn.com/001/song_96.mp4?sig=abc",
            ],
        )

        cands_no320 = crypto_des.upgrade_bitrate_candidates(
            "https://aac.saavncdn.com/001/song_96.mp4", prefer_320=False
        )
        self.assertEqual(
            cands_no320,
            [
                "https://aac.saavncdn.com/001/song_160.mp4",
                "https://aac.saavncdn.com/001/song_96.mp4",
            ],
        )

        plain = "https://cdn.example.com/audio/plain.mp4"
        self.assertEqual(crypto_des.upgrade_bitrate_candidates(plain), [plain])
        self.assertEqual(crypto_des.upgrade_bitrate_candidates(""), [])


class TestLrcParser(unittest.TestCase):
    def test_parse_lrc_standard_multi_timestamp_and_sorting(self):
        raw = """
        [ar:The Weeknd]
        [ti:Blinding Lights]
        [al:After Hours]
        [length:03:20]
        [00:00.00]
        [00:27.12]I've been tryna call
        [00:29.850]I've been <00:30.10>on my own <00:31.00>for long enough
        [00:15.2][01:12.40]Chorus line repeated
        [00:40:50]Colon centisecond format
        [00:45]No fraction line
        [00:50.00]
        [00:52.00]
        [01:00.00]After interlude
        [01:30.00]
        """
        lines = lrc_parser.parse_lrc(raw)
        # Should strip leading/consecutive/trailing blanks and sort ascending
        times = [ln["time"] for ln in lines]
        self.assertEqual(times, sorted(times))
        self.assertEqual(lines[0], {"time": 15.2, "text": "Chorus line repeated"})
        self.assertEqual(lines[1], {"time": 27.12, "text": "I've been tryna call"})
        self.assertEqual(lines[2], {"time": 29.85, "text": "I've been on my own for long enough"})
        self.assertEqual(lines[3], {"time": 40.5, "text": "Colon centisecond format"})
        self.assertEqual(lines[4], {"time": 45.0, "text": "No fraction line"})
        self.assertEqual(lines[5], {"time": 50.0, "text": "", "interlude": True})
        self.assertEqual(lines[6], {"time": 60.0, "text": "After interlude"})
        self.assertEqual(lines[7], {"time": 72.4, "text": "Chorus line repeated"})
        self.assertEqual(len(lines), 8)

    def test_parse_lrc_offset_positive_and_negative_clamp(self):
        raw_pos = "[offset:+500]\n[00:01.00]Line A\n[00:05.25]Line B"
        lines_pos = lrc_parser.parse_lrc(raw_pos)
        self.assertEqual(lines_pos[0]["time"], 1.5)
        self.assertEqual(lines_pos[1]["time"], 5.75)

        raw_neg = "[offset:-2000]\n[00:01.00]Clamped\n[00:05.50]Shifted"
        lines_neg = lrc_parser.parse_lrc(raw_neg)
        self.assertEqual(lines_neg[0]["time"], 0.0)
        self.assertEqual(lines_neg[1]["time"], 3.5)

    def test_lrc_to_plain(self):
        raw = "[00:01.00]First\n[00:03.00]\n[00:05.00]Second"
        self.assertEqual(lrc_parser.lrc_to_plain(raw), "First\n\nSecond")
        self.assertEqual(lrc_parser.lrc_to_plain(""), "")

    def test_clean_title_and_primary_artist(self):
        self.assertEqual(
            lrc_parser.clean_track_title("Blinding Lights (Official Music Video)"),
            "Blinding Lights",
        )
        self.assertEqual(
            lrc_parser.clean_track_title("Bohemian Rhapsody - Remastered 2011"),
            "Bohemian Rhapsody",
        )
        self.assertEqual(
            lrc_parser.clean_track_title("Save Your Tears (feat. Ariana Grande) [Lyrics]"),
            "Save Your Tears",
        )
        self.assertEqual(lrc_parser.extract_primary_artist("The Weeknd, Ariana Grande"), "The Weeknd")
        self.assertEqual(lrc_parser.extract_primary_artist("Sơn Tùng M-TP ft. Snoop Dogg"), "Sơn Tùng M-TP")
        self.assertEqual(lrc_parser.extract_primary_artist("Daft Punk & Pharrell Williams"), "Daft Punk")

    def test_normalize_key_vietnamese_and_unicode_cjk(self):
        self.assertEqual(lrc_parser.normalize_key("Đêm Sài Gòn Không Ngủ!"), "dem sai gon khong ngu")
        self.assertEqual(lrc_parser.normalize_key("Sơn Tùng M-TP"), "son tung m tp")
        # Non-Latin scripts (Korean / Japanese / Cyrillic) must be preserved, not erased to empty string
        self.assertNotEqual(lrc_parser.normalize_key("아이유 밤편지"), "")
        self.assertNotEqual(lrc_parser.normalize_key("YOASOBI 夜に駆ける"), "")
        self.assertNotEqual(
            lrc_parser.normalize_key("밤편지"),
            lrc_parser.normalize_key("좋은 날"),
        )
        self.assertGreater(lrc_parser.token_similarity("아이유 밤편지", "아이유 밤편지"), 0.99)
        self.assertGreater(lrc_parser.token_similarity("Đêm Sài Gòn", "dem sai gon"), 0.99)


class TestOfflineCatalogAndSynth(unittest.TestCase):
    def test_artwork_svg_known_and_escaped_custom_slug(self):
        svg_known = offline_catalog.render_artwork_svg("neon-horizon").decode("utf-8")
        self.assertIn("<svg", svg_known)
        self.assertIn("NEON HORIZON", svg_known)

        svg_xss = offline_catalog.render_artwork_svg("<script>alert(1)</script>").decode("utf-8")
        self.assertNotIn("<script>", svg_xss)
        self.assertIn("&lt;SCRIPT&gt;", svg_xss)

    def test_synthesize_wav_valid_stereo_deterministic(self):
        wav1 = offline_catalog.synthesize_track_wav("local:neon-horizon", duration=5)
        wav2 = offline_catalog.synthesize_track_wav("local:neon-horizon", duration=5)
        wav_other = offline_catalog.synthesize_track_wav("local:dem-saigon", duration=5)

        self.assertEqual(wav1, wav2)
        self.assertNotEqual(wav1, wav_other)
        self.assertTrue(wav1.startswith(b"RIFF"))

        with wave.open(io.BytesIO(wav1), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 2)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 44100)
            self.assertEqual(wf.getnframes(), 5 * 44100)
            # Sample around 1 second in (past fade-in) must be non-silent
            wf.setpos(44100)
            frames = wf.readframes(1000)
            samples = struct.unpack(f"<{len(frames) // 2}h", frames)
            self.assertGreater(max(abs(s) for s in samples), 500)


if __name__ == "__main__":
    unittest.main()
