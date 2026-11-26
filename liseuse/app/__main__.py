"""Add-on « Liseuse » : API JSON (réseau local) pour le plugin KOReader de la liseuse Kobo."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .ha import HAError
from .vues import SourceError, Vues, lire_date

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("OPTIONS_FILE", DATA_DIR / "options.json"))
PLUGIN_DIR = Path(os.environ.get("PLUGIN_DIR", "/app/koplugin/maison.koplugin"))
PORT = int(os.environ.get("PORT", "8768"))

LOG = logging.getLogger("liseuse")


def load_access_token(options: dict) -> str:
    token = (options.get("access_token") or "").strip()
    if token:
        if len(token) < 24:
            raise SystemExit("access_token doit faire au moins 24 caractères.")
        return token
    stored = DATA_DIR / "access_token"
    if stored.exists():
        return stored.read_text().strip()
    token = secrets.token_urlsafe(24)
    fd = os.open(stored, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    LOG.warning("Jeton d'accès généré automatiquement : %s", token)
    return token


def plugin_manifest() -> dict[str, Any]:
    fichiers, h = [], hashlib.sha1()  # noqa: S324 (empreinte de version, pas de sécurité)
    for p in sorted(PLUGIN_DIR.rglob("*")):
        if p.is_file():
            rel = p.relative_to(PLUGIN_DIR).as_posix()
            contenu = p.read_bytes()
            h.update(rel.encode() + b"\0" + contenu)
            fichiers.append({"nom": rel, "taille": len(contenu)})
    return {"version": h.hexdigest()[:12], "fichiers": fichiers}


def build_app(vues: Vues, token: str) -> Starlette:
    manifest = plugin_manifest()

    def autorise(request: Request) -> bool:
        fourni = request.headers.get("x-jeton") or request.query_params.get("jeton") or ""
        return hmac.compare_digest(fourni.encode(), token.encode())

    def api(fn):
        async def handler(request: Request):
            if not autorise(request):
                LOG.warning("Accès refusé depuis %s (%s)", request.client.host if request.client else "?",
                            request.url.path)
                return JSONResponse({"erreur": "jeton invalide"}, status_code=401)
            corps: dict[str, Any] = {}
            if request.method == "POST":
                try:
                    data = await request.json()
                    corps = data if isinstance(data, dict) else {}
                except ValueError:
                    corps = {}
            try:
                return JSONResponse(await run_in_threadpool(fn, request, corps))
            except (SourceError, HAError) as exc:
                LOG.warning("%s : %s", request.url.path, exc)
                return JSONResponse({"erreur": str(exc)}, status_code=502)
            except (TypeError, ValueError, KeyError) as exc:
                LOG.exception("Requête invalide %s", request.url.path)
                return JSONResponse({"erreur": f"requête invalide : {exc}"}, status_code=400)
        return handler

    q = lambda r, k: r.query_params.get(k)  # noqa: E731

    def maison(r, _c):
        return vues.maison()

    def basculer(r, c):
        return vues.basculer(str(c.get("entity_id") or q(r, "e") or ""))

    def thermostat(r, c):
        t = c.get("temperature")
        dl = c.get("delta")
        return vues.thermostat(temperature=float(t) if t is not None else None,
                               delta=float(dl) if dl is not None else None, mode=c.get("mode"))

    def repas(r, _c):
        return vues.repas(lire_date(q(r, "d")))

    def repas_semaine(r, _c):
        return vues.repas_semaine(lire_date(q(r, "d")))

    def recette(r, _c):
        plan = q(r, "repas")
        return vues.recette(int(plan) if plan else None, lire_date(q(r, "d")), q(r, "id"))

    def seances(r, _c):
        return vues.seances(lire_date(q(r, "d")))

    def seances_semaine(r, _c):
        return vues.seances_semaine(lire_date(q(r, "d")))

    def seance(r, _c):
        return vues.seance(q(r, "a") or "", q(r, "id") or "", lire_date(q(r, "d")))

    def entier(c, k):
        v = c.get(k)
        if v in (None, ""):
            raise ValueError(f"{k} manquant")
        return int(v)

    def validation(r, _c):
        return vues.validation(int(q(r, "repas") or 0))

    def valider(r, c):
        statut = str(c.get("statut") or "fait")
        if statut not in ("fait", "saute"):
            raise ValueError("statut : fait ou saute")
        lignes = c.get("lignes")
        return vues.valider(entier(c, "repas"), statut, lignes if isinstance(lignes, list) else None,
                            str(c.get("note") or "") or None)

    def annuler(r, c):
        return vues.annuler_repas(entier(c, "repas"))

    def deplacer(r, c):
        return vues.deplacer(str(c.get("a") or ""), str(c.get("id") or ""), str(c.get("date") or ""),
                             bool(c.get("simuler")))

    def maintenant_s(r, c):
        return vues.maintenant_seance(str(c.get("a") or ""), str(c.get("id") or ""))

    def fait_s(r, c):
        return vues.fait_seance(str(c.get("a") or ""), str(c.get("id") or ""), bool(c.get("fait", True)))

    def ressenti(r, c):
        rpe = c.get("rpe")
        rpe = int(rpe) if rpe not in (None, "", 0) else None
        douleurs = [str(x) for x in c.get("douleurs") or []] if isinstance(c.get("douleurs"), list) else []
        return vues.ressenti(str(c.get("a") or ""), str(c.get("id") or ""), str(c.get("activity_id") or ""),
                             rpe, douleurs[:10], str(c.get("note") or "")[:600])

    def rafraichir(r, _c):
        vues.cache.vider()
        return {"ok": True}

    def plugin(r, _c):
        return manifest

    async def plugin_fichier(request: Request):
        if not autorise(request):
            return Response(status_code=401)
        nom = request.path_params["nom"]
        if nom not in {f["nom"] for f in manifest["fichiers"]}:
            return Response(status_code=404)
        return Response((PLUGIN_DIR / nom).read_bytes(), media_type="application/octet-stream")

    async def ping(request: Request):
        return JSONResponse({"ok": True, "service": "liseuse"})

    return Starlette(routes=[
        Route("/ping", ping),
        Route("/api/maison", api(maison)),
        Route("/api/basculer", api(basculer), methods=["POST"]),
        Route("/api/thermostat", api(thermostat), methods=["POST"]),
        Route("/api/repas", api(repas)),
        Route("/api/repas/semaine", api(repas_semaine)),
        Route("/api/recette", api(recette)),
        Route("/api/seances", api(seances)),
        Route("/api/seances/semaine", api(seances_semaine)),
        Route("/api/seance", api(seance)),
        Route("/api/repas/validation", api(validation)),
        Route("/api/repas/valider", api(valider), methods=["POST"]),
        Route("/api/repas/annuler", api(annuler), methods=["POST"]),
        Route("/api/seance/deplacer", api(deplacer), methods=["POST"]),
        Route("/api/seance/maintenant", api(maintenant_s), methods=["POST"]),
        Route("/api/seance/fait", api(fait_s), methods=["POST"]),
        Route("/api/seance/ressenti", api(ressenti), methods=["POST"]),
        Route("/api/rafraichir", api(rafraichir), methods=["POST"]),
        Route("/plugin", api(plugin)),
        Route("/plugin/{nom:path}", plugin_fichier),
    ])


def main() -> None:
    options = json.loads(OPTIONS_FILE.read_text()) if OPTIONS_FILE.exists() else {}
    level = str(options.get("log_level", "info")).upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = load_access_token(options)
    app = build_app(Vues(options), token)
    LOG.info("API de la liseuse prête sur le port %s (plugin %s)", PORT, plugin_manifest()["version"])
    uvicorn.run(app, host="0.0.0.0", port=PORT, access_log=False, log_level=level.lower(), proxy_headers=False)


if __name__ == "__main__":
    main()
