"""iOS Direct Install & Distribution service (.mobileconfig Web Clip, OTA manifest, AltStore v2)."""

from __future__ import annotations

import plistlib
import re
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

from backend import config

_SAFE_HOST_RE = re.compile(r"^[a-zA-Z0-9._:\-\[\]]+$")


def resolve_public_origin(headers: Any) -> str:
    """Resolve pinned or allowlisted public http(s) origin (prevents Host/X-Forwarded-Host spoofing)."""
    pinned = config.get_public_origin()
    if pinned:
        return pinned

    allowed_hosts = config.get_allowed_hosts()
    raw_proto = ""
    raw_host = ""
    if headers is not None:
        raw_proto = str(headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        raw_host = str(headers.get("X-Forwarded-Host") or headers.get("Host") or "").split(",", 1)[0].strip()

    if allowed_hosts:
        host_only = raw_host.split(":", 1)[0].strip().lower() if raw_host and not raw_host.startswith("[") else raw_host.lower()
        if not raw_host or not _SAFE_HOST_RE.match(raw_host) or (
            raw_host.lower() not in allowed_hosts and host_only not in allowed_hosts
        ):
            primary_host = sorted(allowed_hosts)[0]
            return f"https://{primary_host}".rstrip("/")

    proto = raw_proto if raw_proto in ("http", "https") else ""
    if not raw_host or not _SAFE_HOST_RE.match(raw_host):
        return f"http://127.0.0.1:{config.DEFAULT_PORT}"

    if not proto:
        lower_host = raw_host.lower()
        is_local = (
            lower_host.startswith("127.0.0.1")
            or lower_host.startswith("localhost")
            or lower_host.startswith("[::1]")
            or lower_host.startswith("0.0.0.0")
        )
        proto = "http" if is_local else "https"

    return f"{proto}://{raw_host}".rstrip("/")


def get_install_info(origin: str) -> Dict[str, Any]:
    """Return structured iOS install & sharing metadata for the web frontend."""
    clean_origin = (origin or f"http://127.0.0.1:{config.DEFAULT_PORT}").rstrip("/")
    testflight_url = config.get_testflight_url()
    ipa_url = config.get_ios_ipa_url()
    bundle_id = config.get_ios_bundle_id()
    github_repo = config.get_github_repo()

    ota_manifest_url = f"{clean_origin}/api/ios/manifest.plist" if ipa_url else None
    ota_install_url = (
        f"itms-services://?action=download-manifest&url={urllib.parse.quote(ota_manifest_url, safe='')}"
        if ota_manifest_url
        else None
    )
    ipa_download_url = ipa_url or f"https://github.com/{github_repo}/actions/workflows/ios-build.yml"

    return {
        "appUrl": f"{clean_origin}/",
        "mobileconfigUrl": "/api/ios/install.mobileconfig",
        "testflightUrl": testflight_url,
        "otaManifestUrl": ota_manifest_url,
        "otaInstallUrl": ota_install_url,
        "ipaDownloadUrl": ipa_download_url,
        "altstoreSourceUrl": f"{clean_origin}/api/ios/altstore-source.json",
        "hasDirectNativeInstall": bool(testflight_url or ota_install_url),
        "version": config.APP_VERSION,
        "bundleId": bundle_id,
    }


def build_mobileconfig(origin: str) -> bytes:
    """Generate a valid Apple iOS Web Clip Configuration Profile (.mobileconfig)."""
    clean_origin = (origin or f"http://127.0.0.1:{config.DEFAULT_PORT}").rstrip("/")
    app_url = f"{clean_origin}/"
    bundle_id = config.get_ios_bundle_id()

    profile_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{app_url}#sonify-profile")).upper()
    clip_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{app_url}#sonify-webclip")).upper()

    webclip_payload: Dict[str, Any] = {
        "FullScreen": True,
        "IgnoreManifestScope": False,
        "IsRemovable": True,
        "Label": config.APP_NAME,
        "PayloadDescription": "Adds Sonify Ad-Free Music to your Home Screen.",
        "PayloadDisplayName": config.APP_NAME,
        "PayloadIdentifier": f"{bundle_id}.webclip",
        "PayloadType": "com.apple.webClip.managed",
        "PayloadUUID": clip_uuid,
        "PayloadVersion": 1,
        "Precomposed": True,
        "URL": app_url,
    }

    icon_path = config.FRONTEND_DIR / "icons" / "apple-touch-icon.png"
    if icon_path.is_file():
        webclip_payload["Icon"] = icon_path.read_bytes()

    profile: Dict[str, Any] = {
        "PayloadContent": [webclip_payload],
        "PayloadDescription": (
            "Installs Sonify — 100% Ad-Free Music Streaming directly onto your iPhone Home Screen "
            "as a full-screen app (free, no PC required, never expires)."
        ),
        "PayloadDisplayName": config.APP_NAME,
        "PayloadIdentifier": f"{bundle_id}.webclip.profile",
        "PayloadOrganization": config.APP_NAME,
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": profile_uuid,
        "PayloadVersion": 1,
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)


def build_ota_manifest(origin: str) -> Optional[bytes]:
    """Generate an Apple itms-services:// OTA manifest plist when SONIFY_IOS_IPA_URL is configured."""
    ipa_url = config.get_ios_ipa_url()
    if not ipa_url:
        return None

    clean_origin = (origin or f"http://127.0.0.1:{config.DEFAULT_PORT}").rstrip("/")
    bundle_id = config.get_ios_bundle_id()
    manifest = {
        "items": [
            {
                "assets": [
                    {
                        "kind": "software-package",
                        "url": ipa_url,
                    },
                    {
                        "kind": "display-image",
                        "needs-shine": False,
                        "url": f"{clean_origin}/icons/icon-192.png",
                    },
                    {
                        "kind": "full-size-image",
                        "needs-shine": False,
                        "url": f"{clean_origin}/icons/icon-512.png",
                    },
                ],
                "metadata": {
                    "bundle-identifier": bundle_id,
                    "bundle-version": config.APP_VERSION,
                    "kind": "software",
                    "title": config.APP_NAME,
                },
            }
        ]
    }
    return plistlib.dumps(manifest, fmt=plistlib.FMT_XML)


def build_altstore_source(origin: str) -> Dict[str, Any]:
    """Generate an AltStore / SideStore v2 compatible JSON app source."""
    clean_origin = (origin or f"http://127.0.0.1:{config.DEFAULT_PORT}").rstrip("/")
    ipa_url = config.get_ios_ipa_url()
    bundle_id = config.get_ios_bundle_id()
    github_repo = config.get_github_repo()

    versions: List[Dict[str, Any]] = []
    if ipa_url:
        versions.append(
            {
                "version": config.APP_VERSION,
                "date": "2026-11-25",
                "localizedDescription": (
                    "100% Ad-Free music streaming with Lossless FLAC, synced lyrics, "
                    "playlists, background audio, Lock Screen controls, and AirPlay."
                ),
                "downloadURL": ipa_url,
                "size": 8_500_000,
                "minOSVersion": "16.0",
            }
        )

    return {
        "name": f"{config.APP_NAME} iOS Source",
        "subtitle": "100% Ad-Free Music Streaming for iOS",
        "description": "Official Sonify AltStore & SideStore source for native SwiftUI iOS builds.",
        "identifier": f"{bundle_id}.altstore",
        "sourceURL": f"{clean_origin}/api/ios/altstore-source.json",
        "iconURL": f"{clean_origin}/icons/icon-512.png",
        "website": f"{clean_origin}/",
        "tintColor": "#1DB954",
        "apps": [
            {
                "name": config.APP_NAME,
                "bundleIdentifier": bundle_id,
                "developerName": config.APP_NAME,
                "subtitle": "100% Ad-Free Music & Synced Lyrics",
                "localizedDescription": (
                    "Stream global & Vietnam charts, Lossless FLAC & full tracks, synced .lrc lyrics, "
                    "custom playlists, and Lock Screen / AirPlay playback with zero ads."
                ),
                "iconURL": f"{clean_origin}/icons/icon-512.png",
                "tintColor": "#1DB954",
                "category": "entertainment",
                "marketplaceURL": f"https://github.com/{github_repo}",
                "versions": versions,
                "appPermissions": {
                    "entitlements": [],
                    "privacy": {},
                },
            }
        ],
        "news": [],
    }
