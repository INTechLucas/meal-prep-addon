"""Point d'entrée de l'add-on : lit la configuration, lance le serveur MCP (HTTPS) et le panneau (Ingress)."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import secrets
from pathlib import Path

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from .panel import build_panel
from .server import build_server
from .store import Store

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("OPTIONS_FILE", DATA_DIR / "options.json"))
SSL_DIR = Path(os.environ.get("SSL_DIR", "/ssl"))
DB_FILE = Path(os.environ.get("DB_FILE", DATA_DIR / "cuisine.db"))
PANEL_PORT = int(os.environ.get("PANEL_PORT", "8099"))
PORT = int(os.environ.get("PORT", "8767"))
# Réseau interne des add-ons HA : seul un proxy situé ici peut indiquer l'IP réelle du client
TRUSTED_PROXIES = ipaddress.ip_network("172.30.32.0/23")

LOG = logging.getLogger("cuisine")


def load_access_token(options: dict) -> str:
    token = (options.get("access_token") or "").strip()
    if token:
        if len(token) < 32 or not token.replace("-", "").replace("_", "").isalnum():
            raise SystemExit("access_token doit faire au moins 32 caractères (lettres, chiffres, - ou _).")
        return token
    stored = DATA_DIR / "access_token"
    if stored.exists():
        return stored.read_text().strip()
    token = secrets.token_urlsafe(32)
    fd = os.open(stored, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    LOG.warning("Jeton d'accès généré automatiquement (affiché une seule fois) : %s", token)
    LOG.warning("Copiez-le dans l'option 'access_token' de l'add-on pour le conserver de façon visible.")
    return token


def client_ip(scope) -> str:
    ip = (scope.get("client") or ("?",))[0]
    try:
        if ipaddress.ip_address(ip) in TRUSTED_PROXIES:
            for name, value in scope.get("headers", []):
                if name == b"x-forwarded-for":
                    return value.decode("latin-1").split(",")[0].strip() + " (via proxy)"
    except ValueError:
        pass
    return ip


class Guard:
    """Ne laisse passer que les requêtes dont le premier segment d'URL est le jeton secret."""

    def __init__(self, app, token: str) -> None:
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        client = client_ip(scope)
        segment = scope.get("path", "").lstrip("/").split("/", 1)[0]
        if scope["type"] != "http" or not hmac.compare_digest(segment.encode(), self.token):
            LOG.warning("Requête refusée depuis %s (%s %s…)", client, scope.get("method", scope["type"]),
                        scope.get("path", "")[:8])
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Not found"})
            return None
        LOG.info("%s %s /<jeton>/%s", client, scope["method"], scope["path"].split("/", 2)[-1])
        return await self.app(scope, receive, send)


def main() -> None:
    options = json.loads(OPTIONS_FILE.read_text()) if OPTIONS_FILE.exists() else {}
    level = str(options.get("log_level", "info")).upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = load_access_token(options)
    store = Store(DB_FILE)
    if options.get("jour_courses") is not None:
        store.set_config("jour_courses", str(int(options["jour_courses"])))
    mcp = build_server(store, options)
    app = mcp.streamable_http_app(
        streamable_http_path=f"/{token}/cuisine/mcp",
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    ssl_kwargs = {}
    if options.get("ssl", True):
        cert, key = SSL_DIR / options.get("certfile", "fullchain.pem"), SSL_DIR / options.get("keyfile", "privkey.pem")
        if not cert.exists() or not key.exists():
            raise SystemExit(f"Certificat introuvable ({cert}, {key}) : vérifiez l'add-on Let's Encrypt / Duck DNS.")
        ssl_kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}

    LOG.info("Serveur MCP prêt. URL du connecteur : https://<votre-domaine>/%s…/cuisine/mcp "
             "(jeton complet dans l'option access_token)", token[:4])
    LOG.info("Panneau Cuisine prêt (Ingress, port %s). Base : %s", PANEL_PORT, DB_FILE)

    servers = [
        uvicorn.Server(uvicorn.Config(Guard(app, token), host="0.0.0.0", port=PORT, access_log=False,
                                      log_level=level.lower(), proxy_headers=False, **ssl_kwargs)),
        uvicorn.Server(uvicorn.Config(build_panel(store, options), host="0.0.0.0", port=PANEL_PORT,
                                      access_log=False, log_level=level.lower(), proxy_headers=False)),
    ]

    async def serve_all() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve_all())


if __name__ == "__main__":
    main()
