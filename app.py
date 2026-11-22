#!/usr/bin/env python3
"""Sonify — Spotify-like 100% Ad-Free Music Streaming Web App entry point."""

from __future__ import annotations

import argparse

from backend import config
from backend.server import run_server


def parse_args() -> argparse.Namespace:
    default_host = config.resolve_default_host()
    default_port = config.resolve_default_port()
    parser = argparse.ArgumentParser(
        description="Run the Sonify ad-free music streaming web server."
    )
    parser.add_argument(
        "--host",
        default=default_host,
        help=f"Bind address (default: {default_host})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help=f"Listen port (default: {default_port})",
    )
    parser.add_argument(
        "--db",
        default=str(config.DEFAULT_DB_PATH),
        help=f"SQLite database path (default: {config.DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force offline demo catalog and synthesized WAV playback",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress HTTP request access logs",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_server(
        host=args.host,
        port=args.port,
        db_path=args.db,
        offline=True if args.offline else None,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    main()
