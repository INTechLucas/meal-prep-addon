"""Accès à Home Assistant via l'API du Supervisor (jeton fourni automatiquement à l'add-on)."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any

LOG = logging.getLogger("liseuse.ha")
API = os.environ.get("HA_API", "http://supervisor/core/api")

METEO = {
    "clear-night": "Nuit claire", "cloudy": "Nuageux", "exceptional": "Exceptionnel", "fog": "Brouillard",
    "hail": "Grêle", "lightning": "Orages", "lightning-rainy": "Orages et pluie", "partlycloudy": "Éclaircies",
    "pouring": "Averses", "rainy": "Pluie", "snowy": "Neige", "snowy-rainy": "Neige et pluie",
    "sunny": "Ensoleillé", "windy": "Venteux", "windy-variant": "Venteux et nuageux",
}
MODES = {"off": "Arrêt", "heat": "Chauffage", "cool": "Clim", "auto": "Auto", "heat_cool": "Auto",
         "dry": "Déshumidification", "fan_only": "Ventilation"}
ACTIONS = {"heating": "chauffe", "cooling": "refroidit", "idle": "au repos", "off": "à l'arrêt"}

# Liste des appareils portant l'étiquette, en une seule requête
TEMPLATE_APPAREILS = (
    "[{% for e in label_entities(lbl) %}"
    '{"id": {{ e|tojson }}, "nom": {{ (state_attr(e, "friendly_name") or e)|tojson }}, '
    '"piece": {{ (area_name(e) or "")|tojson }}, "etat": {{ states(e)|tojson }}}'
    "{{ ',' if not loop.last }}{% endfor %}]"
)


class HAError(RuntimeError):
    pass


def _req(method: str, path: str, data: Any = None, timeout: float = 10) -> Any:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise HAError("API Home Assistant indisponible (SUPERVISOR_TOKEN absent)")
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f"{API}{path}", data=body, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (URL interne fixe)
            raw = r.read()
    except Exception as exc:  # noqa: BLE001
        raise HAError(f"Home Assistant : {exc}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode()


def etat(entity_id: str) -> dict[str, Any] | None:
    if not entity_id:
        return None
    try:
        return _req("GET", f"/states/{entity_id}")
    except HAError as exc:
        LOG.warning("État de %s indisponible : %s", entity_id, exc)
        return None


def appareils(etiquette: str) -> list[dict[str, Any]]:
    tpl = "{% set lbl = " + json.dumps(etiquette) + " %}" + TEMPLATE_APPAREILS
    res = _req("POST", "/template", {"template": tpl})
    liste = json.loads(res) if isinstance(res, str) else res
    return liste or []


def service(domaine: str, nom: str, data: dict[str, Any]) -> None:
    _req("POST", f"/services/{domaine}/{nom}", data)


def attendre_changement(entity_id: str, avant: str | None, cle: str = "state", delai: float = 3.0) -> None:
    """Laisse le temps à l'appareil (Zigbee…) de confirmer son nouvel état avant de répondre à la liseuse."""
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        e = etat(entity_id) or {}
        valeur = e.get("state") if cle == "state" else (e.get("attributes") or {}).get(cle)
        if valeur != avant:
            return
        time.sleep(0.25)
