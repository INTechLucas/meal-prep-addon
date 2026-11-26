"""Panneau « Cuisine » servi via l'Ingress de Home Assistant."""

from __future__ import annotations

import ipaddress
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import markdown as md
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from . import ha
from .store import MOMENTS_LIB, RAYONS, UNITES, CuisineError, Store, iso_week

LOG = logging.getLogger("cuisine.panel")
STATIC = Path(__file__).parent / "static"
CONSERVATIONS = ("Frigo", "Placard", "Congélateur", "Panier")
ALLOWED = [ipaddress.ip_network(n) for n in os.environ.get("PANEL_ALLOWED", "172.30.32.2/32").split(",")]
# Réseau interne des add-ons : d'autres add-ons (ex. « Liseuse ») peuvent lire ces routes (GET)
# et utiliser les actions du panneau listées dans ECRITURE_INTERNE (POST)
INTERNE = ipaddress.ip_network(os.environ.get("PANEL_INTERNE", "172.30.32.0/23"))
ECRITURE_INTERNE = ("/api/repas/", "/api/courses/", "/api/stock/")
LECTURE_INTERNE = ("/api/jour", "/api/semaine", "/api/recette/", "/api/repas/", "/api/courses", "/api/stock")


def render_md(text: str | None) -> str:
    safe = (text or "").replace("&", "&amp;").replace("<", "&lt;")
    return md.markdown(safe, extensions=["tables", "sane_lists", "nl2br"])


def _fiche(f: dict[str, Any] | None) -> dict[str, Any] | None:
    if not f:
        return None
    return {**{k: f[k] for k in ("id", "type", "titre", "semaine", "date", "maj")}, "html": render_md(f["contenu"])}


def _date(value: str | None, defaut: date) -> date:
    try:
        return date.fromisoformat(value) if value else defaut
    except ValueError as exc:
        raise CuisineError("date invalide") from exc


def build_panel(store: Store, options: dict[str, Any]) -> Starlette:

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError:
            data = None
        return data if isinstance(data, dict) else {}

    def route(fn):
        """Exécute le traitement hors de la boucle et transforme les erreurs de saisie en 400."""
        async def handler(request: Request):
            b = await body(request) if request.method == "POST" else {}
            try:
                return JSONResponse(await run_in_threadpool(fn, request, b))
            except CuisineError as exc:
                return JSONResponse({"erreur": str(exc)}, status_code=400)
            except (TypeError, ValueError) as exc:
                return JSONResponse({"erreur": f"saisie invalide : {exc}"}, status_code=400)
        return handler

    async def index(request: Request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    def api_jour(request: Request, _b) -> dict[str, Any]:
        d = _date(request.query_params.get("d"), date.today())
        j = store.journee(d.isoformat())
        sem = store.fiches("semaine", iso_week(d))
        return {**j, "faisables": store.faisables("diner"), "fiche": _fiche(sem[0] if sem else None),
                "precedent": (d - timedelta(days=1)).isoformat(), "suivant": (d + timedelta(days=1)).isoformat(),
                "aujourd_hui": date.today().isoformat(), "profils": store.profils(), "moments": MOMENTS_LIB}

    def api_semaine(request: Request, _b) -> dict[str, Any]:
        d = _date(request.query_params.get("d"), date.today())
        lundi = d - timedelta(days=d.weekday())
        fin = lundi + timedelta(days=6)
        return {"debut": lundi.isoformat(), "fin": fin.isoformat(), "repas": store.plan(lundi.isoformat(), fin.isoformat()),
                "precedente": (lundi - timedelta(days=7)).isoformat(), "suivante": (lundi + timedelta(days=7)).isoformat(),
                "aujourd_hui": date.today().isoformat(), "profils": store.profils()}

    def api_recette(request: Request, _b) -> dict[str, Any]:
        r = store.recette(request.path_params["rid"])
        if not r:
            raise CuisineError("recette introuvable")
        return {**r, "html": render_md(r.get("etapes")), "notes_html": render_md(r.get("notes"))}

    def api_batch(request: Request, _b) -> dict[str, Any]:
        t = date.today()
        # batch de la semaine à venir (dimanche le plus proche), sinon le dernier
        dim = t + timedelta(days=(6 - t.weekday()) % 7)
        rows = store.plan((dim - timedelta(days=6)).isoformat(), (dim + timedelta(days=7)).isoformat())
        batch = [r for r in rows if r["moment"] == "batch"]
        jour = batch[0]["date"] if batch else dim.isoformat()
        fiches = store.fiches("batch", iso_week(date.fromisoformat(jour)))
        plats_ids = {r["produit_sortie"] for r in batch}
        dejeuners = [r for r in rows if r["moment"] == "dejeuner" and r["plat_id"] in plats_ids and r["date"] > jour]
        plats = [p for p in store.stock() if p["id"] in plats_ids or p["unite"] == "portion"]
        return {"date": jour, "batch": batch, "fiche": _fiche(fiches[0] if fiches else None), "plats": plats,
                "dejeuners": dejeuners}

    def api_stock(request: Request, _b) -> dict[str, Any]:
        return {"stock": store.stock(), "catalogue": [{k: p[k] for k in ("id", "nom", "unite", "conditionnement",
                                                                          "rayon", "stock")} for p in store.produits()],
                "rayons": [r for r in RAYONS if r != "Préparé maison"], "unites": list(UNITES),
                "conservations": list(CONSERVATIONS)}

    def api_stock_maj(request: Request, b) -> dict[str, Any]:
        q, dl = b.get("quantite"), b.get("delta")
        return store.ajuster_stock(request.path_params["pid"], quantite=float(q) if q not in (None, "") else None,
                                   delta=float(dl) if dl not in (None, "") else None, dlc=b.get("dlc") or None,
                                   motif=str(b.get("motif") or "ajustement panneau")[:80])

    def _num(b: dict[str, Any], k: str) -> float | None:
        v = b.get(k)
        if v in (None, ""):
            return None
        v = float(str(v).replace(",", "."))
        if v < 0:
            raise CuisineError(f"{k} ne peut pas être négatif")
        return v

    def api_produit(request: Request, b) -> dict[str, Any]:
        """Crée (sans id) ou modifie (avec id) une fiche produit depuis le panneau."""
        pid = request.path_params.get("pid")
        nom = str(b.get("nom") or "").strip()[:80] or None
        if not pid and not nom:
            raise CuisineError("nom obligatoire")
        if not pid and not b.get("rayon"):
            raise CuisineError("choisis un rayon")
        cons = b.get("conservation") or None
        if cons and cons not in CONSERVATIONS:
            raise CuisineError("conservation invalide")
        champs = {k: _num(b, k) for k in ("conditionnement", "prix", "prix_kg", "kcal", "prot", "gluc", "lip",
                                           "fibres", "poids_piece", "stock_min")}
        res = store.upsert_produit(
            id=pid, nom=nom, unite=b.get("unite") or None, rayon=b.get("rayon") or None, conservation=cons,
            au_poids=bool(b.get("au_poids")) if "au_poids" in b else None,
            basique=bool(b.get("basique")) if "basique" in b else None,
            libelle_ticket=(str(b.get("libelle_ticket")).strip()[:80] or None) if b.get("libelle_ticket") else None,
            notes=(str(b.get("notes")).strip()[:300] or None) if b.get("notes") else None,
            **{k: v for k, v in champs.items() if v is not None})
        store.evenement("produit_modifie" if pid else "produit_cree", {"produit": res["id"], "nom": nom})
        return res

    def api_produit_get(request: Request, _b) -> dict[str, Any]:
        p = store.produit(request.path_params["pid"])
        if not p:
            raise CuisineError("produit introuvable")
        return p

    def api_validation(request: Request, _b) -> dict[str, Any]:
        return store.preparer_validation(int(request.path_params["pid"]))

    def api_valider(request: Request, b) -> dict[str, Any]:
        lignes = b.get("lignes")
        if lignes is not None and not isinstance(lignes, list):
            raise CuisineError("lignes invalides")
        return store.valider(int(request.path_params["pid"]), str(b.get("statut") or "fait"), lignes,
                             (str(b["note"])[:300] if b.get("note") else None))

    def api_annuler(request: Request, _b) -> dict[str, Any]:
        return store.annuler_validation(int(request.path_params["pid"]))

    def api_hors_plan(request: Request, b) -> dict[str, Any]:
        return store.ajouter_hors_plan(_date(b.get("date"), date.today()).isoformat(), str(b.get("moment") or ""),
                                       str(b.get("libelle") or "").strip()[:120] or "Hors plan", b.get("lignes") or [])

    def api_courses(request: Request, _b) -> dict[str, Any]:
        return {**store.liste_courses(), "ha_configure": bool(options.get("todo_entity"))}

    def api_cocher(request: Request, b) -> dict[str, Any]:
        store.liste_cocher(produit=b.get("produit"), libre_id=b.get("libre_id"), coche=bool(b.get("coche", True)))
        return {"ok": True}

    def api_modifier(request: Request, b) -> dict[str, Any]:
        p = b.get("paquets")
        store.liste_modifier(str(b.get("produit") or ""), paquets=float(p) if p not in (None, "") else None,
                             retire=b.get("retire"))
        return {"ok": True}

    def api_libre(request: Request, b) -> dict[str, Any]:
        return {"id": store.liste_ajouter(str(b.get("texte") or ""))}

    def api_libre_suppr(request: Request, _b) -> dict[str, Any]:
        store.liste_supprimer_libre(int(request.path_params["lid"]))
        return {"ok": True}

    def api_rangement(request: Request, _b) -> dict[str, Any]:
        return {"lignes": store.preparer_rangement()}

    def api_ranger(request: Request, b) -> dict[str, Any]:
        lignes = b.get("lignes")
        return store.ranger_courses(lignes if isinstance(lignes, list) else None)

    def api_envoyer(request: Request, _b) -> dict[str, Any]:
        try:
            return ha.envoyer_liste(options.get("todo_entity", ""), store.liste_courses())
        except CuisineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CuisineError(f"Envoi vers Home Assistant impossible : {exc}") from exc

    app = Starlette(routes=[
        Route("/", index),
        Route("/api/jour", route(api_jour)),
        Route("/api/semaine", route(api_semaine)),
        Route("/api/recette/{rid}", route(api_recette)),
        Route("/api/batch", route(api_batch)),
        Route("/api/stock", route(api_stock)),
        Route("/api/stock/{pid}", route(api_stock_maj), methods=["POST"]),
        Route("/api/produit", route(api_produit), methods=["POST"]),
        Route("/api/produit/{pid}", route(api_produit_get)),
        Route("/api/produit/{pid}", route(api_produit), methods=["POST"]),
        Route("/api/repas/{pid:int}/validation", route(api_validation)),
        Route("/api/repas/{pid:int}/valider", route(api_valider), methods=["POST"]),
        Route("/api/repas/{pid:int}/annuler", route(api_annuler), methods=["POST"]),
        Route("/api/hors_plan", route(api_hors_plan), methods=["POST"]),
        Route("/api/courses", route(api_courses)),
        Route("/api/courses/cocher", route(api_cocher), methods=["POST"]),
        Route("/api/courses/modifier", route(api_modifier), methods=["POST"]),
        Route("/api/courses/libre", route(api_libre), methods=["POST"]),
        Route("/api/courses/libre/{lid:int}/supprimer", route(api_libre_suppr), methods=["POST"]),
        Route("/api/courses/rangement", route(api_rangement)),
        Route("/api/courses/ranger", route(api_ranger), methods=["POST"]),
        Route("/api/courses/envoyer_ha", route(api_envoyer), methods=["POST"]),
    ])
    return IngressOnly(app)


class IngressOnly:
    """Refuse toute connexion qui ne vient pas du proxy Ingress de Home Assistant."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            ip = (scope.get("client") or ("0.0.0.0",))[0]
            try:
                ok = any(ipaddress.ip_address(ip) in n for n in ALLOWED)
            except ValueError:
                ok = False
            chemin, methode = scope.get("path", ""), scope.get("method")
            if not ok and ((methode == "GET" and chemin.startswith(LECTURE_INTERNE))
                           or (methode == "POST" and chemin.startswith(ECRITURE_INTERNE))):
                try:
                    ok = ipaddress.ip_address(ip) in INTERNE
                except ValueError:
                    ok = False
            if not ok:
                LOG.warning("Panneau : accès refusé depuis %s", ip)
                return await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)
