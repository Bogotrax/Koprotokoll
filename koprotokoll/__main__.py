"""Kommandozeile.

    python -m koprotokoll [serve] [--host 127.0.0.1] [--port 8000]   Server starten
    python -m koprotokoll create-key NAME                            API-Schlüssel erzeugen
    python -m koprotokoll bot-login                                  Google-Konto für den Bot anmelden
    python -m koprotokoll mcp                                        MCP-Server (stdio) starten
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="koprotokoll", description="Lokaler Meeting-Koprotokoll (Parakeet)")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="Server starten (Standard)")
    for p in (parser, serve):
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8000)
    key = sub.add_parser("create-key", help="API-Schlüssel erzeugen")
    key.add_argument("name")
    sub.add_parser("bot-login", help="Browser öffnen, um das Bot-Google-Konto anzumelden")
    sub.add_parser("mcp", help="MCP-Server über stdio starten (nutzt KOPROTOKOLL_URL / KOPROTOKOLL_API_KEY)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "create-key":
        from .auth import create_key
        from .config import Settings
        from .db import Database

        s = Settings()
        s.ensure_dirs()
        k = create_key(Database(s.db_path), args.name)
        print(f"API-Schlüssel „{k['name']}“ (wird nur einmal angezeigt):\n{k['key']}")
    elif args.cmd == "bot-login":
        bot_login()
    elif args.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
    else:
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)


def bot_login() -> None:
    """Sichtbaren Browser mit dem Bot-Profil öffnen; Anmeldung bleibt für spätere Bots erhalten."""
    from playwright.sync_api import sync_playwright

    from .config import Settings

    s = Settings()
    s.bot_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(s.bot_profile_dir), headless=False, executable_path=s.bot_chrome_path or None,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://accounts.google.com/")
        print("Im geöffneten Browser mit dem Google-Konto des Bots anmelden, dann das Fenster schließen.")
        page.wait_for_event("close", timeout=0)
        ctx.close()
    print(f"Profil gespeichert unter {s.bot_profile_dir}")


if __name__ == "__main__":
    main()
