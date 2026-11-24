"""Envoi de la liste de courses vers une liste « À faire » de Home Assistant (appli mobile, widget…).

Seuls les éléments créés par l'add-on (description commençant par MARQUE) sont remplacés :
ce que vous ajoutez vous-même à la liste dans Home Assistant n'est jamais touché.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

LOG = logging.getLogger("cuisine.ha")
MARQUE = "Cuisine ·"
API = os.environ.get("HA_API", "http://supervisor/core/api")


def _call(service: str, data: dict[str, Any], reponse: bool = False) -> Any:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("API Home Assistant indisponible (SUPERVISOR_TOKEN absent)")
    url = f"{API}/services/todo/{service}" + ("?return_response" if reponse else "")
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (URL interne fixe)
        body = r.read()
    return json.loads(body) if body else None


def envoyer_liste(entity_id: str, liste: dict[str, Any]) -> dict[str, Any]:
    if not entity_id or not entity_id.startswith("todo."):
        raise RuntimeError("Option todo_entity non renseignée (ex. todo.courses_lidl)")
    existants = _call("get_items", {"entity_id": entity_id}, reponse=True) or {}
    items = (existants.get("service_response") or {}).get(entity_id, {}).get("items", [])
    anciens = [i["uid"] for i in items if (i.get("description") or "").startswith(MARQUE)]
    if anciens:
        _call("remove_item", {"entity_id": entity_id, "item": anciens})
    n = 0
    for l in liste["lignes"]:
        if l.get("coche"):
            continue
        desc = f"{MARQUE} {l['achat']}" + (f" · {l['prix']:.2f} €".replace(".", ",") if l.get("prix") else "")
        _call("add_item", {"entity_id": entity_id, "item": f"{l['nom']} ({l['achat']})", "description": desc})
        n += 1
    for l in liste["libres"]:
        if not l["coche"]:
            _call("add_item", {"entity_id": entity_id, "item": l["texte"], "description": MARQUE})
            n += 1
    LOG.info("Liste envoyée vers %s : %d élément(s)", entity_id, n)
    return {"entity_id": entity_id, "envoyes": n, "remplaces": len(anciens)}
