"""Construction des écrans de la liseuse : données déjà mises en forme (textes courts, en français),
pour que le plugin KOReader n'ait plus qu'à les afficher."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import ha

LOG = logging.getLogger("liseuse.vues")

JOURS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre")
ORDRE_MOMENTS = ("petit_dej", "dejeuner", "collation", "diner", "batch")
STATUTS_REPAS = {"fait": "Fait", "saute": "Sauté", "ecarte": "Écarté", "remplace": "Remplacé"}
STATUTS_SEANCE = {"realisee": "Réalisée", "manquee": "Manquée", "aujourdhui": "Aujourd'hui", "prevue": "Prévue"}
SPORTS = {"course": "Course", "natation": "Natation", "renfo": "Renfo", "velo": "Vélo", "swimrun": "Swimrun",
          "trail": "Trail", "marche": "Marche", "mobilite": "Mobilité", "repos": "Repos"}
METEO_ICONES = {
    "clear-night": "weather_night", "cloudy": "weather_cloudy", "exceptional": "alert_circle_outline",
    "fog": "weather_fog", "hail": "weather_hail", "lightning": "weather_lightning",
    "lightning-rainy": "weather_lightning_rainy", "partlycloudy": "weather_partly_cloudy",
    "pouring": "weather_pouring", "rainy": "weather_rainy", "snowy": "weather_snowy",
    "snowy-rainy": "weather_snowy_rainy", "sunny": "weather_sunny", "windy": "weather_windy",
    "windy-variant": "weather_windy",
}
MODES_ICONES = {"off": "power", "heat": "fire", "cool": "snowflake", "auto": "thermometer",
                "heat_cool": "thermometer", "fan_only": "fan", "dry": "weather_fog"}
MOMENTS_ICONES = {"petit_dej": "coffee", "dejeuner": "food", "collation": "food_apple", "diner": "silverware_fork_knife",
                  "batch": "pot_steam"}
SPORTS_ICONES = {"course": "run", "trail": "run", "natation": "swim", "velo": "bike", "renfo": "weight_lifter",
                 "mobilite": "yoga", "marche": "walk", "swimrun": "swim"}
CSS = """
body { font-family: serif; line-height: 1.35; }
h1 { font-size: 1.35em; margin: 0 0 0.3em 0; }
h2 { font-size: 1.1em; margin: 0.9em 0 0.3em 0; border-bottom: 1px solid black; }
p { margin: 0.3em 0; }
ul, ol { margin: 0.2em 0 0.2em 1.2em; padding: 0; }
li { margin: 0.15em 0; }
.meta { font-style: italic; }
.parts { font-size: 0.85em; }
table { border-collapse: collapse; }
td, th { border: 1px solid black; padding: 0.15em 0.3em; }
"""


TZ = ZoneInfo(os.environ.get("TZ") or "Europe/Paris")


def maintenant() -> datetime:
    return datetime.now(TZ)


def aujourdhui() -> date:
    return maintenant().date()


class SourceError(RuntimeError):
    pass


# ---------------------------------------------------------------- outils

def nombre(x: float, dec: int = 1) -> str:
    s = f"{x:.{dec}f}".rstrip("0").rstrip(".") if dec else f"{x:.0f}"
    return s.replace(".", ",")


def degres(x: Any, dec: int = 1) -> str:
    return f"{nombre(float(x), dec)}°" if isinstance(x, (int, float)) else "—"


def jour_long(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def jour_court(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d.day}"


def lire_date(valeur: str | None) -> date:
    try:
        return date.fromisoformat(valeur) if valeur else aujourdhui()
    except ValueError:
        return aujourdhui()


def semaine_iso(d: date) -> str:
    a, s, _ = d.isocalendar()
    return f"{a}-W{s:02d}"


def esc(t: Any) -> str:
    return html.escape(str(t if t is not None else ""))


def page_html(titre: str, corps: str) -> dict[str, Any]:
    return {"titre": titre, "html": corps, "css": CSS}


class Cache:
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, cle: str, ttl: float, fn: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._d.get(cle)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = fn()
        with self._lock:
            self._d[cle] = (time.monotonic(), val)
        return val

    def vider(self) -> None:
        with self._lock:
            self._d.clear()


def http_json(url: str, entetes: dict[str, str] | None = None, timeout: float = 25) -> Any:
    req = urllib.request.Request(url, headers=entetes or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (add-ons internes configurés)
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"{urllib.parse.urlsplit(url).netloc} : {exc}") from exc


def http_post(url: str, corps: dict[str, Any], entetes: dict[str, str] | None = None, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, data=json.dumps(corps).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(entetes or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (add-ons internes configurés)
            brut = r.read()
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("erreur")
        except Exception:  # noqa: BLE001
            msg = None
        raise SourceError(msg or f"erreur {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SourceError(f"{urllib.parse.urlsplit(url).netloc} : {exc}") from exc
    return json.loads(brut) if brut else {}


DOULEURS_LIB = {"mollet": "Mollet", "achille": "Achille", "pied": "Pied", "genou": "Genou", "hanche": "Hanche",
                "ischios": "Ischios", "dos": "Dos", "epaule": "Épaule", "autre": "Autre"}
JOURS_COURTS = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")


def arrondi_part(q: float, unite: str) -> float:
    """Arrondi pratique d'une part individuelle (comme le panneau Cuisine)."""
    if unite in ("g", "ml"):
        return round(q / 5) * 5 if q >= 50 else round(q)
    return round(q * 10) / 10


# ---------------------------------------------------------------- vues

class Vues:
    def __init__(self, options: dict[str, Any]) -> None:
        self.o = options
        self.cache = Cache()
        self.cuisine = (options.get("cuisine_url") or "").rstrip("/")
        self.coach = (options.get("coach_url") or "").rstrip("/")

    # ---------- Maison

    def _appareils(self) -> list[dict[str, Any]]:
        return ha.appareils(self.o.get("etiquette") or "kobo")

    @staticmethod
    def _nom_capteur(e: dict[str, Any]) -> str:
        nom = (e.get("attributes") or {}).get("friendly_name") or e.get("entity_id", "")
        nom = re.sub(r"(?i)\btemp[ée]rature\b", " ", nom)
        nom = " ".join(nom.split())
        return nom[:1].upper() + nom[1:] if nom else "Température"

    @staticmethod
    def _nom_appareil(nom: str) -> str:
        return re.sub(r"\bLumiere\b", "Lumière", nom)

    @staticmethod
    def _appareil_court(nom: str, piece: str) -> tuple[str, str]:
        """Nom court (sans « Lumière », « Prise » ni la pièce) et icône d'un appareil."""
        bas = nom.lower()
        if "prise" in bas or "ampli" in bas:
            icone = "power_socket_eu"
        elif "ventil" in bas:
            icone = "fan"
        elif "tv" in bas or "télé" in bas:
            icone = "television"
        elif "lampadaire" in bas or " sol" in bas:
            icone = "floor_lamp"
        else:
            icone = "lightbulb"
        court = re.sub(r"(?i)\b(lumi[eè]re|lampe|prise|switch|light)\b", " ", nom)
        if piece:
            court = re.sub(r"(?i)\b" + re.escape(piece) + r"\b", " ", court)
        court = " ".join(court.split())
        court = court[:1].upper() + court[1:] if court else ("Prise" if icone == "power_socket_eu" else "Lumière")
        return court, icone

    def maison(self) -> dict[str, Any]:
        mt = maintenant()
        out: dict[str, Any] = {"date": jour_long(mt.date()), "heure": mt.strftime("%H:%M")}

        m = ha.etat(self.o.get("meteo") or "")
        if m:
            t = (m.get("attributes") or {}).get("temperature")
            out["meteo"] = {"lib": ha.METEO.get(m.get("state"), m.get("state")),
                            "temp": f"{nombre(float(t))} °C" if isinstance(t, (int, float)) else "",
                            "temp_court": degres(t, 0) if isinstance(t, (int, float)) else "",
                            "icone": METEO_ICONES.get(m.get("state"), "weather_cloudy")}

        temps = []
        for eid in self.o.get("temperatures") or []:
            e = ha.etat(eid)
            if not e:
                continue
            try:
                f = float(e["state"])
                v, vc = f"{nombre(f)} °C", degres(f)
            except (TypeError, ValueError):
                v = vc = "—"
            temps.append({"nom": self._nom_capteur(e), "valeur": v, "valeur_court": vc})
        out["temperatures"] = temps

        th = ha.etat(self.o.get("thermostat") or "")
        if th:
            a = th.get("attributes") or {}
            cur, cons = a.get("current_temperature"), a.get("temperature")
            out["thermostat"] = {
                "id": th["entity_id"],
                "nom": a.get("friendly_name") or "Thermostat",
                "actuelle": f"{nombre(float(cur))} °C" if isinstance(cur, (int, float)) else "—",
                "consigne": cons,
                "consigne_txt": f"{nombre(float(cons))} °C" if isinstance(cons, (int, float)) else "—",
                "actuelle_court": degres(cur),
                "consigne_court": degres(cons),
                "piece": re.sub(r"(?i)^thermostat\s*", "", a.get("friendly_name") or "").capitalize(),
                "mode": th.get("state"),
                "mode_lib": ha.MODES.get(th.get("state"), th.get("state")),
                "action_lib": ha.ACTIONS.get(a.get("hvac_action") or "", ""),
                "modes": [{"id": x, "lib": ha.MODES.get(x, x), "icone": MODES_ICONES.get(x, "thermometer")}
                          for x in a.get("hvac_modes") or []],
                "actif": a.get("hvac_action") in ("heating", "cooling"),
                "min": a.get("min_temp", 7), "max": a.get("max_temp", 35),
                "pas": a.get("target_temp_step") or 0.5,
            }

        pieces: dict[str, list[dict[str, Any]]] = {}
        for ap in self._appareils():
            etat = ap.get("etat")
            nom = self._nom_appareil(ap.get("nom") or ap["id"])
            court, icone = self._appareil_court(nom, ap.get("piece") or "")
            pieces.setdefault(ap.get("piece") or "Autres", []).append({
                "id": ap["id"], "nom": nom, "court": court, "icone": icone, "piece": ap.get("piece") or "",
                "allume": etat == "on", "dispo": etat not in ("unavailable", "unknown"),
                "etat_lib": {"on": "Allumé", "off": "Éteint"}.get(etat, "Indisponible"),
            })
        out["pieces"] = [{"nom": p, "appareils": sorted(v, key=lambda x: x["nom"])}
                         for p, v in sorted(pieces.items(), key=lambda kv: (kv[0] == "Autres", kv[0]))]
        return out

    def basculer(self, entity_id: str) -> dict[str, Any]:
        autorises = {a["id"]: a for a in self._appareils()}
        if entity_id not in autorises:
            raise SourceError("appareil non autorisé (étiquette manquante)")
        avant = autorises[entity_id].get("etat")
        ha.service("homeassistant", "toggle", {"entity_id": entity_id})
        ha.attendre_changement(entity_id, avant)
        return self.maison()

    def thermostat(self, temperature: float | None = None, delta: float | None = None,
                   mode: str | None = None) -> dict[str, Any]:
        eid = self.o.get("thermostat")
        th = ha.etat(eid or "")
        if not th:
            raise SourceError("thermostat introuvable")
        a = th.get("attributes") or {}
        if mode:
            if mode not in (a.get("hvac_modes") or []):
                raise SourceError("mode inconnu")
            ha.service("climate", "set_hvac_mode", {"entity_id": eid, "hvac_mode": mode})
            ha.attendre_changement(eid, th.get("state"), delai=2)
        if temperature is not None or delta is not None:
            base = a.get("temperature") if isinstance(a.get("temperature"), (int, float)) else 19.0
            cible = float(temperature) if temperature is not None else base + float(delta or 0)
            cible = max(float(a.get("min_temp", 7)), min(float(a.get("max_temp", 35)), round(cible * 2) / 2))
            ha.service("climate", "set_temperature", {"entity_id": eid, "temperature": cible})
            ha.attendre_changement(eid, a.get("temperature"), cle="temperature", delai=2)
        return self.maison()

    # ---------- Repas

    def _cuisine(self, chemin: str, ttl: float = 30) -> Any:
        if not self.cuisine:
            raise SourceError("add-on Cuisine non configuré")
        return self.cache.get(f"cuisine:{chemin}", ttl, lambda: http_json(self.cuisine + chemin))

    @staticmethod
    def _portions(p: dict[str, float] | None, noms: dict[str, str]) -> str:
        return " · ".join(f"{noms.get(k, k.capitalize())} ×{nombre(v, 2)}" for k, v in (p or {}).items() if v)

    def repas(self, d: date) -> dict[str, Any]:
        j = self._cuisine(f"/api/jour?d={d.isoformat()}")
        noms = {p["id"]: p["nom"] for p in j.get("profils") or []}
        groupes: dict[str, list[dict[str, Any]]] = {}
        libs: dict[str, str] = {}
        for r in j.get("repas") or []:
            if r.get("statut") == "ecarte":
                continue
            libs[r["moment"]] = r.get("moment_lib") or r["moment"]
            manque = (r.get("dispo") or {}).get("manque") or []
            groupes.setdefault(r["moment"], []).append({
                "id": r["id"], "d": r["date"], "titre": r.get("titre") or r.get("libelle") or "Repas",
                "option": r.get("role") == "option",
                "statut_lib": STATUTS_REPAS.get(r.get("statut"), ""),
                "temps": f"{r['temps_min']} min" if r.get("temps_min") else "",
                "portions": self._portions(r.get("portions"), noms),
                "note": r.get("note") or "",
                "manque": ("Manque : " + ", ".join(manque)) if manque and r.get("statut") == "prevu" else "",
                "recette": r.get("recette_id") or "",
            })
        moments = [{"lib": libs[m], "icone": MOMENTS_ICONES.get(m, "silverware_fork_knife"), "repas": groupes[m]}
                   for m in sorted(groupes, key=lambda m: ORDRE_MOMENTS.index(m) if m in ORDRE_MOMENTS else 99)]
        return {"date": d.isoformat(), "titre": jour_long(d), "aujourdhui": d == aujourdhui(),
                "precedent": (d - timedelta(days=1)).isoformat(), "suivant": (d + timedelta(days=1)).isoformat(),
                "moments": moments}

    def repas_semaine(self, d: date) -> dict[str, Any]:
        s = self._cuisine(f"/api/semaine?d={d.isoformat()}")
        lundi = date.fromisoformat(s["debut"])
        jours = []
        for i in range(7):
            jd = lundi + timedelta(days=i)
            rows = [r for r in s.get("repas") or [] if r.get("date") == jd.isoformat() and r.get("statut") != "ecarte"]
            rows.sort(key=lambda r: (ORDRE_MOMENTS.index(r["moment"]) if r["moment"] in ORDRE_MOMENTS else 99,
                                     r.get("role") == "option"))
            jours.append({"date": jd.isoformat(), "titre": jour_court(jd), "aujourdhui": jd == aujourdhui(),
                          "repas": [{"id": r["id"], "d": r["date"],
                                     "moment": r.get("moment_lib") or r["moment"],
                                     "icone": MOMENTS_ICONES.get(r["moment"], "silverware_fork_knife"),
                                     "titre": r.get("titre") or r.get("libelle") or "Repas",
                                     "option": r.get("role") == "option",
                                     "statut_lib": STATUTS_REPAS.get(r.get("statut"), ""),
                                     "recette": r.get("recette_id") or ""} for r in rows]})
        fin = lundi + timedelta(days=6)
        return {"titre": f"Semaine du {lundi.day} au {fin.day} {MOIS[fin.month - 1]}",
                "precedente": (lundi - timedelta(days=7)).isoformat(),
                "suivante": (lundi + timedelta(days=7)).isoformat(), "jours": jours}

    @staticmethod
    def _quantite(q: float, unite: str) -> str:
        if unite in ("g", "ml"):
            if q >= 1000:
                return f"{nombre(q / 1000, 2)} {'kg' if unite == 'g' else 'l'}"
            pas = 5 if q >= 20 else 1
            return f"{nombre(round(q / pas) * pas, 0)} {unite}"
        if unite == "pc":
            return nombre(round(q * 2) / 2, 1)
        if unite == "portion":
            return f"{nombre(round(q * 2) / 2, 1)} portion" + ("s" if q > 1 else "")
        return f"{nombre(q, 1)} {unite}"

    @staticmethod
    def _actions_repas(repas: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not repas:
            return []
        st = repas.get("statut")
        if st == "prevu":
            lib = "Batch fait" if repas.get("moment") == "batch" else ("Je prends ça" if repas.get("role") == "option" else "C'est fait")
            return [{"id": "valider", "lib": lib, "icone": "check_circle", "principal": True},
                    {"id": "sauter", "lib": "Pas fait", "icone": "close"}]
        if st in ("fait", "saute", "remplace"):
            return [{"id": "annuler_repas", "lib": "Annuler la validation", "icone": "undo"}]
        return []

    def recette(self, plan_id: int | None, d: date | None, rid: str | None) -> dict[str, Any]:
        repas = None
        noms: dict[str, str] = {}
        if plan_id is not None:
            j = self._cuisine(f"/api/jour?d={(d or aujourdhui()).isoformat()}")
            repas = next((r for r in j.get("repas") or [] if r.get("id") == plan_id), None)
            if repas is None:
                raise SourceError("repas introuvable")
            rid = repas.get("recette_id")
            noms = {p["id"]: p["nom"] for p in j.get("profils") or []}
        nom = lambda k: noms.get(k, k.capitalize())  # noqa: E731
        portions = (repas or {}).get("portions") or {}
        statut = STATUTS_REPAS.get((repas or {}).get("statut"), "")
        extra = {"actions": self._actions_repas(repas),
                 "cible": {"repas": plan_id, "d": (repas or {}).get("date") or ""}}
        corps = []
        if not rid:
            titre = (repas or {}).get("titre") or "Repas"
            corps.append(f"<h1>{esc(titre)}</h1>")
            if portions:
                corps.append(f"<p class='meta'>{esc(self._portions(portions, noms))}{(' · ' + esc(statut)) if statut else ''}</p>")
            if repas and repas.get("note"):
                corps.append(f"<h2>Note</h2><p>{esc(repas['note'])}</p>")
            if repas and repas.get("plat_id"):
                corps.append("<p>Plat préparé lors du batch cooking : à réchauffer.</p>")
            return {**page_html(titre, "\n".join(corps)), **extra}

        r = self._cuisine(f"/api/recette/{urllib.parse.quote(rid)}", ttl=300)
        # répartition par personne seulement si le repas est partagé (pas pour un batch en portions totales)
        pers = [(k, float(v)) for k, v in portions.items() if k != "total" and v] if "total" not in portions else []
        partage = len(pers) > 1
        total = sum(v for _, v in pers) if partage else float(portions.get("total") or (pers[0][1] if pers else 1) or 1)
        meta = []
        if r.get("temps_min"):
            meta.append(f"{r['temps_min']} min")
        if partage:
            meta.append(f"{nombre(total, 2)} portions (" + " · ".join(f"{nom(k)} ×{nombre(v, 2)}" for k, v in pers) + ")")
        else:
            meta.append(f"pour {nombre(total, 2)} portion" + ("s" if total > 1 else ""))
        if statut:
            meta.append(statut)
        n = r.get("nutrition_portion") or {}
        corps.append(f"<h1>{esc(r.get('titre'))}</h1>")
        corps.append(f"<p class='meta'>{esc(' · '.join(meta))}</p>")
        if n.get("kcal"):
            corps.append(f"<p class='meta'>Par portion : {n['kcal']} kcal · P {n.get('prot', 0)} g · G {n.get('gluc', 0)} g · L {n.get('lip', 0)} g</p>")
        if repas and repas.get("note"):
            corps.append(f"<p><b>Note du jour :</b> {esc(repas['note'])}</p>")
        corps.append("<h2>Ingrédients</h2><ul>")
        for i in r.get("ingredients_details") or []:
            u, q = i.get("unite") or "g", float(i.get("quantite") or 0)
            note = f" <i>({esc(i['note'])})</i>" if i.get("note") else ""
            if partage:
                parts = [(k, arrondi_part(q * v, u)) for k, v in pers]
                tot = sum(x for _, x in parts)
                detail = " · ".join(f"{nom(k)} {nombre(x, 1) if u == 'pc' else self._quantite(x, u)}" for k, x in parts)
                corps.append(f"<li>{esc(i.get('nom'))}{note} : <b>{esc(self._quantite(tot, u))}</b>"
                             f"<br/><span class='parts'>{esc(detail)}</span></li>")
            else:
                corps.append(f"<li>{esc(i.get('nom'))}{note} : <b>{esc(self._quantite(q * total, u))}</b></li>")
        corps.append("</ul>")
        if r.get("html"):
            corps.append(f"<h2>Étapes</h2>{r['html']}")
        if r.get("notes_html") and (r.get("notes") or "").strip():
            corps.append(f"<h2>Notes</h2>{r['notes_html']}")
        return {**page_html(r.get("titre") or "Recette", "\n".join(corps)), **extra}

    # ---------- Actions Cuisine

    @staticmethod
    def _pas(q: float, unite: str) -> float:
        if unite in ("g", "ml"):
            return 10 if q >= 100 else 5
        return 0.5

    def validation(self, plan_id: int) -> dict[str, Any]:
        v = http_json(f"{self.cuisine}/api/repas/{plan_id}/validation")
        lignes = []
        for l in v.get("lignes") or []:
            u, q = l.get("unite") or "g", float(l.get("quantite") or 0)
            lignes.append({
                "produit": l.get("produit"), "nom": l.get("nom"), "unite": u, "sens": l.get("sens"),
                "quantite": round(q, 1), "pas": self._pas(q, u),
                "info": "produit, ajouté au stock" if l.get("sens") == "produit"
                        else f"en stock : {self._quantite(float(l.get('stock') or 0), u)}",
            })
        rep = v.get("repas") or {}
        return {"titre": rep.get("titre") or "Repas", "statut": rep.get("statut"), "lignes": lignes}

    def valider(self, plan_id: int, statut: str, lignes: list[dict[str, Any]] | None, note: str | None) -> dict[str, Any]:
        corps: dict[str, Any] = {"statut": statut}
        if lignes is not None:
            corps["lignes"] = [{"produit": l.get("produit"), "sens": l.get("sens"), "quantite": float(l.get("quantite") or 0)}
                               for l in lignes if isinstance(l, dict)]
        if note:
            corps["note"] = str(note)[:300]
        http_post(f"{self.cuisine}/api/repas/{plan_id}/valider", corps)
        self.cache.vider()
        return {"ok": True, "message": "Stock mis à jour" if statut == "fait" else "Noté"}

    def annuler_repas(self, plan_id: int) -> dict[str, Any]:
        http_post(f"{self.cuisine}/api/repas/{plan_id}/annuler", {})
        self.cache.vider()
        return {"ok": True, "message": "Validation annulée, stock rétabli"}

    # ---------- Séances

    def _athletes(self) -> list[dict[str, str]]:
        if not self.coach:
            raise SourceError("add-on Coach non configuré")
        return self.cache.get("coach:athletes", 3600, lambda: http_json(self.coach + "/api/athletes"))["athletes"]

    def _semaine_coach(self, athlete: str, d: date) -> dict[str, Any]:
        w = semaine_iso(d)
        return self.cache.get(f"coach:{athlete}:{w}", 120,
                              lambda: http_json(f"{self.coach}/api/semaine?w={w}", {"X-Athlete": athlete}, timeout=60))

    @staticmethod
    def _seance(s: dict[str, Any], partenaires: dict[str, list[str]]) -> dict[str, Any]:
        fait = ""
        a = s.get("activite")
        if a:
            bouts = [a.get("duree") or ""]
            if a.get("distance_km"):
                bouts.append(f"{nombre(float(a['distance_km']), 1)} km")
            if a.get("allure_min_km"):
                bouts.append(f"{a['allure_min_km']} /km")
            if a.get("fc_moy"):
                bouts.append(f"FC {a['fc_moy']}")
            fait = " · ".join(b for b in bouts if b)
        res = s.get("ressenti") or {}
        avec = s.get("commune")
        if isinstance(avec, list):  # le panneau Coach donne directement les partenaires
            partenaires = {s["id"]: avec}
        fiche = s.get("fiche") if isinstance(s.get("fiche"), dict) else {}
        return {
            "id": s["id"], "d": s.get("date"), "titre": s.get("titre") or fiche.get("titre") or s["id"],
            "sport": SPORTS.get(s.get("sport") or "", (s.get("sport") or "").capitalize()),
            "icone": SPORTS_ICONES.get(s.get("sport") or "", "run"),
            "intensite": s.get("intensite") or "",
            "statut": s.get("statut"), "statut_lib": STATUTS_SEANCE.get(s.get("statut"), ""),
            "resume": s.get("resume") or fiche.get("resume") or "",
            "fc": f"FC ≤ {s['fc_cible']}" if s.get("fc_cible") else "",
            "commune": ("avec " + ", ".join(partenaires.get(s["id"], []))) if s.get("commune") and partenaires.get(s["id"]) else
                       ("en binôme" if s.get("commune") else ""),
            "fait": fait, "rpe": f"RPE {res['rpe']}" if res.get("rpe") else "",
        }

    def _seances_jour(self, d: date) -> tuple[list[dict[str, Any]], list[str]]:
        athletes, erreurs = self._athletes(), []
        semaines: dict[str, dict[str, Any]] = {}
        for at in athletes:
            try:
                semaines[at["id"]] = self._semaine_coach(at["id"], d)
            except SourceError as exc:
                erreurs.append(f"{at['nom']} : {exc}")
        # qui fait chaque séance commune
        qui: dict[str, list[str]] = {}
        for at in athletes:
            for s in ((semaines.get(at["id"]) or {}).get("planning") or {}).get("seances") or []:
                if s.get("commune"):
                    qui.setdefault(s["id"], []).append(at["nom"])
        out = []
        for at in athletes:
            sem = semaines.get(at["id"])
            if sem is None:
                continue
            partenaires = {k: [n for n in v if n != at["nom"]] for k, v in qui.items()}
            seances = [self._seance(s, partenaires) for s in (sem.get("planning") or {}).get("seances") or []
                       if s.get("date") == d.isoformat()]
            out.append({"id": at["id"], "nom": at["nom"], "seances": seances})
        return out, erreurs

    def seances(self, d: date) -> dict[str, Any]:
        athletes, erreurs = self._seances_jour(d)
        return {"date": d.isoformat(), "titre": jour_long(d), "aujourdhui": d == aujourdhui(),
                "precedent": (d - timedelta(days=1)).isoformat(), "suivant": (d + timedelta(days=1)).isoformat(),
                "athletes": athletes, "erreurs": erreurs}

    def seances_semaine(self, d: date) -> dict[str, Any]:
        lundi = d - timedelta(days=d.weekday())
        athletes, erreurs = self._athletes(), []
        par_jour: dict[str, list[dict[str, Any]]] = {}
        for at in athletes:
            try:
                sem = self._semaine_coach(at["id"], lundi)
            except SourceError as exc:
                erreurs.append(f"{at['nom']} : {exc}")
                continue
            for s in (sem.get("planning") or {}).get("seances") or []:
                par_jour.setdefault(s.get("date") or "", []).append({
                    "athlete": at["id"], "nom": at["nom"], "id": s["id"], "d": s.get("date"),
                    "titre": s.get("titre") or (s.get("fiche") or {}).get("titre") or s["id"],
                    "icone": SPORTS_ICONES.get(s.get("sport") or "", "run"),
                    "statut_lib": STATUTS_SEANCE.get(s.get("statut"), ""), "statut": s.get("statut")})
        jours = []
        for i in range(7):
            jd = lundi + timedelta(days=i)
            jours.append({"date": jd.isoformat(), "titre": jour_court(jd), "aujourdhui": jd == aujourdhui(),
                          "seances": par_jour.get(jd.isoformat(), [])})
        fin = lundi + timedelta(days=6)
        return {"titre": f"Semaine du {lundi.day} au {fin.day} {MOIS[fin.month - 1]}",
                "precedente": (lundi - timedelta(days=7)).isoformat(),
                "suivante": (lundi + timedelta(days=7)).isoformat(), "jours": jours, "erreurs": erreurs}

    def seance(self, athlete: str, sid: str, d: date) -> dict[str, Any]:
        noms = {a["id"]: a["nom"] for a in self._athletes()}
        if athlete not in noms:
            raise SourceError("athlète inconnu")
        sem = self._semaine_coach(athlete, d)
        s = next((x for x in (sem.get("planning") or {}).get("seances") or [] if x.get("id") == sid), None)
        if s is None:
            raise SourceError("séance introuvable")
        v = self._seance(s, {})
        corps = [f"<h1>{esc(v['titre'])}</h1>"]
        meta = [noms[athlete], v["sport"]]
        for k in ("intensite", "fc", "statut_lib"):
            if v[k]:
                meta.append(v[k])
        corps.append(f"<p class='meta'>{esc(' · '.join(meta))}</p>")
        if v["resume"]:
            corps.append(f"<p>{esc(v['resume'])}</p>")
        if v["fait"]:
            corps.append(f"<h2>Réalisé</h2><p>{esc(v['fait'])}{(' · ' + esc(v['rpe'])) if v['rpe'] else ''}</p>")
        fiche = s.get("fiche") or {}
        if fiche.get("html"):
            # les schémas (SVG en data:) ne passent pas bien sur l'e-ink : on les retire
            corps.append(f"<h2>Déroulé</h2>{re.sub(r'<img[^>]*>', '', fiche['html'])}")
        res = s.get("ressenti") or {}
        if res.get("rpe") or res.get("douleurs") or res.get("note"):
            bouts = [f"Effort {res['rpe']}/10" if res.get("rpe") else "",
                     ("Douleurs : " + ", ".join(DOULEURS_LIB.get(x, x) for x in res.get("douleurs") or [])) if res.get("douleurs") else ""]
            corps.append(f"<h2>Ressenti</h2><p>{esc(' · '.join(b for b in bouts if b))}</p>")
            if res.get("note"):
                corps.append(f"<p>{esc(res['note']).replace(chr(10), '<br/>')}</p>")

        realisee = s.get("statut") == "realisee"
        actions = []
        if not realisee:
            actions.append({"id": "deplacer", "lib": "Déplacer", "icone": "calendar_week"})
            if s.get("date") != aujourdhui().isoformat():
                actions.append({"id": "maintenant", "lib": "Aujourd'hui", "icone": "run"})
            actions.append({"id": "fait", "lib": "Marquer faite", "icone": "check_circle", "principal": True})
        else:
            actions.append({"id": "ressenti", "lib": "Mon ressenti", "icone": "pencil", "principal": True})
            if s.get("fait_manuel"):
                actions.append({"id": "pas_fait", "lib": "Pas faite", "icone": "undo"})
        lundi = d - timedelta(days=d.weekday())
        semaine = []
        for i in range(7):
            jd = lundi + timedelta(days=i)
            semaine.append({"date": jd.isoformat(), "lib": f"{JOURS_COURTS[i]} {jd.day}",
                            "actuel": jd.isoformat() == s.get("date"), "aujourdhui": jd == aujourdhui()})
        cible = {"a": athlete, "id": sid, "d": s.get("date") or d.isoformat(), "titre": v["titre"],
                 "activity_id": str((s.get("activite") or {}).get("activity_id") or ""),
                 "semaine": semaine,
                 "ressenti": {"rpe": res.get("rpe") or 0, "douleurs": res.get("douleurs") or [], "note": res.get("note") or ""},
                 "douleurs": [{"id": k, "lib": l} for k, l in DOULEURS_LIB.items()]}
        return {**page_html(v["titre"], "\n".join(corps)), "actions": actions, "cible": cible}

    # ---------- Actions Coach

    def _coach_post(self, athlete: str, chemin: str, corps: dict[str, Any]) -> Any:
        if athlete not in {a["id"] for a in self._athletes()}:
            raise SourceError("athlète inconnu")
        res = http_post(self.coach + chemin, corps, {"X-Athlete": athlete}, timeout=60)
        if not corps.get("simuler"):
            self.cache.vider()
        return res

    def deplacer(self, athlete: str, sid: str, nouvelle: str, simuler: bool) -> dict[str, Any]:
        res = self._coach_post(athlete, f"/api/seance/{urllib.parse.quote(sid)}/deplacer",
                               {"date": nouvelle, "simuler": simuler})
        alertes = [a.get("texte", "") for a in res.get("alertes") or [] if isinstance(a, dict)]
        msg = "Séance déplacée" + (f" (aussi pour {', '.join(res['binome'])})" if res.get("binome") else "")
        return {"ok": True, "simulation": bool(res.get("simulation")), "alertes": alertes, "message": msg}

    def maintenant_seance(self, athlete: str, sid: str) -> dict[str, Any]:
        res = self._coach_post(athlete, f"/api/seance/{urllib.parse.quote(sid)}/maintenant", {})
        return {"ok": True, "message": "Séance prévue aujourd'hui et envoyée à la montre"
                + (f" (aussi pour {', '.join(res['binome'])})" if res.get("binome") else "")}

    def fait_seance(self, athlete: str, sid: str, fait: bool) -> dict[str, Any]:
        self._coach_post(athlete, f"/api/seance/{urllib.parse.quote(sid)}/fait", {"fait": fait})
        return {"ok": True, "message": "Séance marquée faite" if fait else "Séance remise à faire"}

    def ressenti(self, athlete: str, sid: str, activity_id: str, rpe: int | None, douleurs: list[str],
                 note: str) -> dict[str, Any]:
        corps = {"seance_id": sid, "activity_id": activity_id or None, "rpe": rpe, "douleurs": douleurs, "note": note}
        self._coach_post(athlete, "/api/ressenti", corps)
        return {"ok": True, "message": "Ressenti enregistré"}
