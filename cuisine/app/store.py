"""Cœur de l'add-on Cuisine : catalogue Lidl, stock, recettes, plan de repas, batch, liste de courses.

Tout ce qui doit être exact (quantités, stock, liste) est calculé ici, de façon déterministe.
Claude, via le serveur MCP, ne fait que publier des recettes et des plans de repas.

Unités : chaque produit a une unité de base (g, ml, pc ou portion). Les quantités de stock,
de recette et de liste sont toujours exprimées dans cette unité.
Valeurs nutritionnelles : pour 100 g / 100 ml, ou pour 1 pièce / 1 portion.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UNITES = ("g", "ml", "pc", "portion")
MOMENTS = ("petit_dej", "dejeuner", "collation", "diner", "batch")
MOMENTS_LIB = {"petit_dej": "Petit-déjeuner", "dejeuner": "Déjeuner", "collation": "Collation",
               "diner": "Dîner", "batch": "Batch cooking"}
STATUTS = ("prevu", "fait", "saute", "ecarte", "remplace")
RAYONS = ("Fruits et légumes", "Boulangerie", "Boucherie / volaille", "Charcuterie", "Poissonnerie", "Traiteur",
          "Crèmerie", "Épicerie salée", "Épicerie sucrée", "Surgelés", "Boissons", "Préparé maison", "Autre")
NUTRI = ("kcal", "prot", "gluc", "lip", "fibres")
FICHES = {"semaine": "Semaine", "batch": "Batch cooking", "note": "Note"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CuisineError(ValueError):
    """Erreur de saisie, renvoyée telle quelle à Claude ou au panneau."""


def slugify(text: str) -> str:
    text = (text or "").lower()
    for a, b in (("àâä", "a"), ("éèêë", "e"), ("îï", "i"), ("ôö", "o"), ("ùûü", "u"), ("ç", "c"), ("œ", "oe")):
        for ch in a:
            text = text.replace(ch, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60] or "x"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def check_date(value: str, champ: str = "date") -> str:
    if not _DATE.match(value or ""):
        raise CuisineError(f"{champ} attendue au format AAAA-MM-JJ, reçu {value!r}")
    date.fromisoformat(value)
    return value


JOURS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")


def jour_court(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{JOURS[d.weekday()]} {d.day}"


def fmt_qte(q: float | None, unite: str) -> str:
    if q is None:
        return ""
    if unite == "g" and q >= 1000:
        return f"{q / 1000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " kg"
    if unite == "ml" and q >= 1000:
        return f"{q / 1000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " L"
    txt = f"{q:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return {"g": f"{txt} g", "ml": f"{txt} ml", "pc": f"{txt} pc", "portion": f"{txt} portion" + ("s" if q > 1 else "")}[unite]


SCHEMA = """
CREATE TABLE IF NOT EXISTS produits (
  id TEXT PRIMARY KEY, nom TEXT NOT NULL, libelle_ticket TEXT, unite TEXT NOT NULL,
  conditionnement REAL, au_poids INTEGER DEFAULT 0, prix REAL, prix_kg REAL,
  rayon TEXT, conservation TEXT, kcal REAL, prot REAL, gluc REAL, lip REAL, fibres REAL,
  poids_piece REAL, basique INTEGER DEFAULT 0, stock_min REAL DEFAULT 0, recette_id TEXT,
  actif INTEGER DEFAULT 1, notes TEXT, maj TEXT);
CREATE TABLE IF NOT EXISTS stock (
  produit_id TEXT PRIMARY KEY REFERENCES produits(id), quantite REAL NOT NULL DEFAULT 0, dlc TEXT, maj TEXT);
CREATE TABLE IF NOT EXISTS recettes (
  id TEXT PRIMARY KEY, titre TEXT NOT NULL, moments TEXT, temps_min INTEGER, ingredients TEXT NOT NULL,
  etapes TEXT, notes TEXT, tags TEXT, maj TEXT);
CREATE TABLE IF NOT EXISTS plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, moment TEXT NOT NULL,
  recette_id TEXT, plat_id TEXT, libelle TEXT, portions TEXT NOT NULL, produit_sortie TEXT,
  role TEXT NOT NULL DEFAULT 'principal', statut TEXT NOT NULL DEFAULT 'prevu', note TEXT, fait_le TEXT, cree_le TEXT);
CREATE INDEX IF NOT EXISTS plan_date ON plan(date);
CREATE TABLE IF NOT EXISTS mouvements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, produit_id TEXT, delta REAL, motif TEXT, plan_id INTEGER);
CREATE TABLE IF NOT EXISTS liste_etat (
  produit_id TEXT PRIMARY KEY, coche INTEGER DEFAULT 0, paquets REAL, retire INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS liste_libre (
  id INTEGER PRIMARY KEY AUTOINCREMENT, texte TEXT NOT NULL, coche INTEGER DEFAULT 0, ts TEXT);
CREATE TABLE IF NOT EXISTS evenements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, details TEXT, traite INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS fiches (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, titre TEXT NOT NULL, contenu TEXT, semaine TEXT, date TEXT, maj TEXT);
CREATE TABLE IF NOT EXISTS profils (
  id TEXT PRIMARY KEY, nom TEXT NOT NULL, facteur REAL NOT NULL DEFAULT 1, cibles TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS config (cle TEXT PRIMARY KEY, valeur TEXT);
"""

PROFILS_DEFAUT = [
    ("lucas", "Lucas", 1.2, {"kcal": 2700}, "75 kg, sport le midi, portions de légumes réduites"),
    ("anais", "Anaïs", 0.9, {}, "60 kg"),
]

# Base produits construite à partir du ticket Lidl du 20/11/2026 (libellés exacts du ticket)
PRODUITS_DEFAUT = [
    # id, nom, libellé ticket, unité, conditionnement, au poids, prix, prix/kg, rayon, conservation
    ("escalopes-dinde", "Escalopes de dinde", "Escalopes de dinde", "g", 750, 0, 7.69, None, "Boucherie / volaille", "Frigo"),
    ("filets-canard", "Filets de canard", "Filet de canard", "g", None, 1, None, 16.65, "Boucherie / volaille", "Frigo"),
    ("saumon-fume", "Saumon fumé de Norvège", "Saumon fumé Norvège", "g", 200, 0, 4.45, None, "Poissonnerie", "Frigo"),
    ("poitrine-fumee", "Poitrine fumée en tranches", "Poitrine fumée", "g", 200, 0, 2.39, None, "Charcuterie", "Frigo"),
    ("steak-veggie", "Steak végétarien", "Steak veggie", "g", 180, 0, 2.38, None, "Traiteur", "Frigo"),
    ("bowl-mexicain", "Bowl mexicain (plat prêt)", "Bowl Mexico", "g", 450, 0, 2.99, None, "Traiteur", "Frigo"),
    ("skyr-nature", "Skyr nature", "Skyr nature", "g", 450, 0, 1.89, None, "Crèmerie", "Frigo"),
    ("emmental", "Emmental français", "Emmental français", "g", 500, 0, 3.99, None, "Crèmerie", "Frigo"),
    ("fromage-bleu", "Fromage bleu", "Fromage bleu 55% MG", "g", 250, 0, 2.29, None, "Crèmerie", "Frigo"),
    ("cheddar-rape", "Cheddar râpé", "Cheddar râpé", "g", 200, 0, 2.39, None, "Crèmerie", "Frigo"),
    ("mozzarella", "Mozzarella", "Mozzarella sans OGM", "g", 125, 0, 0.75, None, "Crèmerie", "Frigo"),
    ("lait-demi-ecreme", "Lait bio demi-écrémé", "Lait bio 1/2 écrémé", "ml", 1000, 0, 1.08, None, "Crèmerie", "Placard"),
    ("creme-soja", "Crème de soja cuisine bio", "Soja cuisine bio", "ml", 200, 0, 0.95, None, "Épicerie salée", "Placard"),
    ("linguine", "Linguine al bronzo", "Linguine al bronzo", "g", 500, 0, 1.69, None, "Épicerie salée", "Placard"),
    ("sauce-tomate", "Sauce tomate bio", "Sauces tomate Bio as", "g", 350, 0, 1.67, None, "Épicerie salée", "Placard"),
    ("chips-paysannes", "Chips paysannes", "Chips paysannes", "g", 200, 0, 1.32, None, "Épicerie salée", "Placard"),
    ("chocolat-noir", "Chocolat noir dégustation", "Chocolat Noir dégust", "g", 100, 0, 2.15, None, "Épicerie sucrée", "Placard"),
    ("pain-de-mie", "Pain de mie", "Pain de mie", "g", 500, 0, 1.62, None, "Boulangerie", "Placard"),
    ("pains-burger", "Pains burger briochés (×4)", "4 burgers briochés", "pc", 4, 0, 1.29, None, "Boulangerie", "Placard"),
    ("avocat", "Avocat", "Avocat", "pc", 1, 0, 0.99, None, "Fruits et légumes", "Panier"),
    ("bananes", "Bananes bio", "Banane Bio Fairtrade", "g", None, 1, None, 2.39, "Fruits et légumes", "Panier"),
    ("raisin-rose", "Raisin rosé", "Raisin rosé", "g", None, 1, None, 3.49, "Fruits et légumes", "Frigo"),
]


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=OFF")
        self._lock = threading.RLock()
        with self._lock:
            self.db.executescript(SCHEMA)
            self._seed()

    # ------------------------------------------------------------------ bas niveau
    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def _one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute(sql, args).fetchone()

    def _tx(self):
        store = self

        class _T:
            def __enter__(self):
                store._lock.acquire()
                store.db.execute("BEGIN IMMEDIATE")

            def __exit__(self, exc_type, *_):
                try:
                    store.db.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()
        return _T()

    def config(self, cle: str, defaut: str | None = None) -> str | None:
        r = self._one("SELECT valeur FROM config WHERE cle=?", (cle,))
        return r["valeur"] if r else defaut

    def set_config(self, cle: str, valeur: str) -> None:
        self.db.execute("INSERT INTO config(cle, valeur) VALUES(?,?) ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
                        (cle, valeur))

    def _seed(self) -> None:
        if self.config("seed") == "1":
            return
        with self._tx():
            for pid, nom, fac, cibles, notes in PROFILS_DEFAUT:
                self.db.execute("INSERT OR IGNORE INTO profils VALUES(?,?,?,?,?)", (pid, nom, fac, json.dumps(cibles), notes))
            for p in PRODUITS_DEFAUT:
                self.db.execute(
                    "INSERT OR IGNORE INTO produits(id, nom, libelle_ticket, unite, conditionnement, au_poids, prix, prix_kg,"
                    " rayon, conservation, maj) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (*p, now_iso()))
            self.set_config("jour_courses", "4")  # vendredi
            self.set_config("seed", "1")

    def evenement(self, type_: str, details: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO evenements(ts, type, details) VALUES(?,?,?)",
                        (now_iso(), type_, json.dumps(details, ensure_ascii=False)))

    # ------------------------------------------------------------------ profils
    def profils(self) -> list[dict[str, Any]]:
        return [{**dict(r), "cibles": json.loads(r["cibles"] or "{}")} for r in self._q("SELECT * FROM profils ORDER BY id")]

    def set_profil(self, pid: str, nom: str | None = None, facteur: float | None = None,
                   cibles: dict[str, float] | None = None, notes: str | None = None) -> dict[str, Any]:
        if not _ID.match(pid or ""):
            raise CuisineError("id de profil invalide (minuscules, chiffres, tirets)")
        if facteur is not None and not 0.3 <= facteur <= 3:
            raise CuisineError("facteur de portion attendu entre 0,3 et 3")
        old = self._one("SELECT * FROM profils WHERE id=?", (pid,))
        if not old and not nom:
            raise CuisineError("nom obligatoire pour un nouveau profil")
        with self._tx():
            self.db.execute(
                "INSERT INTO profils(id, nom, facteur, cibles, notes) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "nom=excluded.nom, facteur=excluded.facteur, cibles=excluded.cibles, notes=excluded.notes",
                (pid, nom or old["nom"], facteur if facteur is not None else (old["facteur"] if old else 1),
                 json.dumps(cibles if cibles is not None else json.loads(old["cibles"] or "{}") if old else {}),
                 notes if notes is not None else (old["notes"] if old else None)))
        return next(p for p in self.profils() if p["id"] == pid)

    def _facteurs(self) -> dict[str, float]:
        return {p["id"]: p["facteur"] for p in self.profils()}

    # ------------------------------------------------------------------ produits
    def produit(self, pid: str) -> dict[str, Any] | None:
        r = self._one("SELECT p.*, s.quantite AS stock, s.dlc FROM produits p LEFT JOIN stock s ON s.produit_id=p.id WHERE p.id=?",
                      (pid,))
        return dict(r) if r else None

    def produits(self, actifs: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT p.*, COALESCE(s.quantite,0) AS stock, s.dlc FROM produits p LEFT JOIN stock s ON s.produit_id=p.id"
        if actifs:
            sql += " WHERE p.actif=1"
        rows = [dict(r) for r in self._q(sql + " ORDER BY p.nom")]
        return rows

    def _need_produit(self, pid: str) -> dict[str, Any]:
        p = self.produit(pid)
        if not p:
            raise CuisineError(f"Produit inconnu : {pid!r} (voir list_produits ou upsert_produit)")
        return p

    def upsert_produit(self, *, id: str | None = None, nom: str | None = None, unite: str | None = None,
                       **champs: Any) -> dict[str, Any]:
        pid = id or slugify(nom or "")
        if not _ID.match(pid):
            raise CuisineError("id produit invalide (minuscules, chiffres, tirets)")
        old = self.produit(pid)
        if not old and (not nom or not unite):
            raise CuisineError("nom et unite obligatoires pour un nouveau produit")
        if unite and unite not in UNITES:
            raise CuisineError(f"unite doit être l'une de : {', '.join(UNITES)}")
        if old and unite and unite != old["unite"] and (old["stock"] or 0) > 0:
            raise CuisineError("impossible de changer l'unité d'un produit en stock : ajuster le stock à 0 d'abord")
        rayon = champs.get("rayon")
        if rayon and rayon not in RAYONS:
            raise CuisineError(f"rayon doit être l'un de : {', '.join(RAYONS)}")
        cols = ("libelle_ticket", "conditionnement", "au_poids", "prix", "prix_kg", "rayon", "conservation", *NUTRI,
                "poids_piece", "basique", "stock_min", "recette_id", "actif", "notes")
        vals = {k: champs[k] for k in cols if k in champs and champs[k] is not None}
        for k in ("au_poids", "basique", "actif"):
            if k in vals:
                vals[k] = 1 if vals[k] else 0
        with self._tx():
            if not old:
                self.db.execute("INSERT INTO produits(id, nom, unite, maj) VALUES(?,?,?,?)", (pid, nom, unite, now_iso()))
            sets = {"nom": nom, "unite": unite, **vals, "maj": now_iso()}
            sets = {k: v for k, v in sets.items() if v is not None}
            self.db.execute(f"UPDATE produits SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), pid))
        return {"id": pid, "statut": "mis à jour" if old else "créé"}

    # ------------------------------------------------------------------ stock
    def _stock_delta(self, pid: str, delta: float, motif: str, plan_id: int | None = None,
                     dlc: str | None = None) -> float:
        r = self._one("SELECT quantite FROM stock WHERE produit_id=?", (pid,))
        avant = r["quantite"] if r else 0.0
        apres = max(0.0, round(avant + delta, 3))
        self.db.execute(
            "INSERT INTO stock(produit_id, quantite, dlc, maj) VALUES(?,?,?,?) ON CONFLICT(produit_id) DO UPDATE SET "
            "quantite=excluded.quantite, dlc=COALESCE(excluded.dlc, stock.dlc), maj=excluded.maj",
            (pid, apres, dlc, now_iso()))
        if apres == 0:
            self.db.execute("UPDATE stock SET dlc=NULL WHERE produit_id=?", (pid,))
        self.db.execute("INSERT INTO mouvements(ts, produit_id, delta, motif, plan_id) VALUES(?,?,?,?,?)",
                        (now_iso(), pid, round(apres - avant, 3), motif, plan_id))
        return apres

    def stock(self) -> list[dict[str, Any]]:
        out = []
        for p in self.produits():
            if p["stock"] <= 0 and not p["basique"]:
                continue
            out.append({"id": p["id"], "nom": p["nom"], "quantite": p["stock"], "unite": p["unite"],
                        "affichage": fmt_qte(p["stock"], p["unite"]), "dlc": p["dlc"], "rayon": p["rayon"] or "Autre",
                        "conservation": p["conservation"], "basique": bool(p["basique"]), "stock_min": p["stock_min"],
                        "sous_min": bool(p["basique"]) and p["stock"] < (p["stock_min"] or 0),
                        "conditionnement": p["conditionnement"]})
        return out

    def ajuster_stock(self, pid: str, *, quantite: float | None = None, delta: float | None = None,
                      dlc: str | None = None, motif: str = "ajustement", source: str = "panneau") -> dict[str, Any]:
        p = self._need_produit(pid)
        if (quantite is None) == (delta is None) and dlc is None:
            raise CuisineError("donner soit quantite (valeur absolue), soit delta")
        if dlc:
            check_date(dlc, "dlc")
        with self._tx():
            if quantite is not None:
                if quantite < 0:
                    raise CuisineError("quantité négative")
                d = quantite - (p["stock"] or 0)
            else:
                d = delta or 0.0
            apres = self._stock_delta(pid, d, motif, dlc=dlc)
            if dlc and d == 0:
                self.db.execute("UPDATE stock SET dlc=? WHERE produit_id=?", (dlc, pid))
            if source == "panneau":
                self.evenement("stock_ajuste", {"produit": pid, "nom": p["nom"], "delta": round(d, 3), "stock": apres,
                                                "unite": p["unite"], "motif": motif})
        return {"produit": pid, "stock": apres, "affichage": fmt_qte(apres, p["unite"])}

    def entree_stock(self, lignes: list[dict[str, Any]], motif: str = "courses", source: str = "claude") -> dict[str, Any]:
        """Ajoute plusieurs lignes au stock (ticket de caisse, courses rangées)."""
        ok, erreurs = [], []
        with self._tx():
            for l in lignes:
                pid = l.get("produit")
                p = self.produit(pid or "")
                if not p:
                    erreurs.append({"ligne": l, "erreur": "produit inconnu"})
                    continue
                q = l.get("quantite")
                if q is None and l.get("paquets") is not None:
                    if not p["conditionnement"]:
                        erreurs.append({"ligne": l, "erreur": "produit sans conditionnement : donner quantite"})
                        continue
                    q = float(l["paquets"]) * p["conditionnement"]
                if q is None or q <= 0:
                    erreurs.append({"ligne": l, "erreur": "quantité manquante"})
                    continue
                dlc = l.get("dlc")
                if dlc:
                    check_date(dlc, "dlc")
                apres = self._stock_delta(pid, float(q), motif, dlc=dlc)
                ok.append({"produit": pid, "ajoute": fmt_qte(float(q), p["unite"]), "stock": fmt_qte(apres, p["unite"])})
            if ok and source == "panneau":
                self.evenement("courses_rangees", {"lignes": ok})
        return {"ajoutes": ok, "erreurs": erreurs}

    # ------------------------------------------------------------------ recettes
    def _recette_row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["moments"] = json.loads(d["moments"] or "[]")
        d["ingredients"] = json.loads(d["ingredients"] or "[]")
        d["tags"] = json.loads(d["tags"] or "[]")
        return d

    def recette(self, rid: str, avec_details: bool = True) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM recettes WHERE id=?", (rid,))
        if not r:
            return None
        rec = self._recette_row(r)
        if avec_details:
            ing = []
            for i in rec["ingredients"]:
                p = self.produit(i["produit"]) or {}
                ing.append({**i, "nom": p.get("nom", i["produit"]), "unite": p.get("unite", "g"),
                            "affichage": fmt_qte(i["quantite"], p.get("unite", "g"))})
            rec["ingredients_details"] = ing
            rec["nutrition_portion"] = self.nutrition(rec["ingredients"], 1)
        return rec

    def recettes(self) -> list[dict[str, Any]]:
        return [self._recette_row(r) for r in self._q("SELECT * FROM recettes ORDER BY titre")]

    def upsert_recette(self, *, id: str | None, titre: str, ingredients: list[dict[str, Any]], moments: list[str],
                       temps_min: int | None = None, etapes: str = "", notes: str | None = None,
                       tags: list[str] | None = None) -> dict[str, Any]:
        rid = id or slugify(titre)
        if not _ID.match(rid):
            raise CuisineError("id recette invalide")
        if not titre.strip():
            raise CuisineError("titre obligatoire")
        bad = [m for m in moments if m not in MOMENTS]
        if bad:
            raise CuisineError(f"moments invalides {bad} ; attendus : {', '.join(MOMENTS)}")
        propres = []
        for i in ingredients:
            pid, q = i.get("produit"), i.get("quantite")
            if not self.produit(pid or ""):
                raise CuisineError(f"ingrédient {pid!r} absent du catalogue : le créer d'abord avec upsert_produit")
            if not isinstance(q, (int, float)) or q <= 0:
                raise CuisineError(f"quantité invalide pour {pid}")
            propres.append({"produit": pid, "quantite": float(q), **({"note": i["note"]} if i.get("note") else {})})
        old = self._one("SELECT id FROM recettes WHERE id=?", (rid,))
        with self._tx():
            self.db.execute(
                "INSERT INTO recettes(id, titre, moments, temps_min, ingredients, etapes, notes, tags, maj) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET titre=excluded.titre, moments=excluded.moments, "
                "temps_min=excluded.temps_min, ingredients=excluded.ingredients, etapes=excluded.etapes, "
                "notes=excluded.notes, tags=excluded.tags, maj=excluded.maj",
                (rid, titre.strip(), json.dumps(moments), temps_min, json.dumps(propres, ensure_ascii=False), etapes,
                 notes, json.dumps(tags or [], ensure_ascii=False), now_iso()))
        return {"id": rid, "statut": "mise à jour" if old else "créée", "nutrition_portion": self.nutrition(propres, 1)}

    def nutrition(self, ingredients: list[dict[str, Any]], multiplicateur: float) -> dict[str, Any]:
        tot = {k: 0.0 for k in NUTRI}
        manquants = []
        for i in ingredients:
            p = self.produit(i["produit"])
            if not p or p["kcal"] is None:
                manquants.append(i["produit"])
                continue
            base = i["quantite"] * multiplicateur
            f = base / 100 if p["unite"] in ("g", "ml") else base
            for k in NUTRI:
                tot[k] += (p[k] or 0) * f
        out = {k: round(v) for k, v in tot.items()}
        if manquants:
            out["incomplet"] = manquants
        return out

    # ------------------------------------------------------------------ plan de repas
    def _plan_row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["portions"] = json.loads(d["portions"] or "{}")
        return d

    def _titre(self, row: dict[str, Any]) -> str:
        if row.get("libelle"):
            return row["libelle"]
        if row.get("recette_id"):
            r = self._one("SELECT titre FROM recettes WHERE id=?", (row["recette_id"],))
            return r["titre"] if r else row["recette_id"]
        if row.get("plat_id"):
            p = self.produit(row["plat_id"])
            return p["nom"] if p else row["plat_id"]
        return "Repas"

    def consommation(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """Ce qu'un repas prévu consomme (quantités dans l'unité du produit)."""
        portions = row["portions"]
        total = float(portions.get("total") or sum(v for k, v in portions.items() if k != "total") or 0)
        if row.get("plat_id"):
            return [{"produit": row["plat_id"], "quantite": round(total, 3)}]
        if row.get("recette_id"):
            r = self._one("SELECT ingredients FROM recettes WHERE id=?", (row["recette_id"],))
            if not r:
                return []
            return [{"produit": i["produit"], "quantite": round(i["quantite"] * total, 1)}
                    for i in json.loads(r["ingredients"])]
        return []

    def production(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        if row.get("moment") == "batch" and row.get("produit_sortie"):
            return [{"produit": row["produit_sortie"], "quantite": float(row["portions"].get("total") or 0)}]
        return []

    def _portions(self, personnes: Any) -> dict[str, float]:
        facteurs = self._facteurs()
        if personnes is None or personnes == "tous":
            personnes = list(facteurs)
        if isinstance(personnes, str):
            personnes = [personnes]
        if isinstance(personnes, list):
            inconnus = [p for p in personnes if p not in facteurs]
            if inconnus:
                raise CuisineError(f"profils inconnus : {inconnus}")
            return {p: facteurs[p] for p in personnes}
        if isinstance(personnes, dict):
            inconnus = [p for p in personnes if p not in facteurs]
            if inconnus:
                raise CuisineError(f"profils inconnus : {inconnus}")
            return {p: float(v) for p, v in personnes.items() if float(v) > 0}
        raise CuisineError("personnes : liste de profils, dict {profil: portions} ou 'tous'")

    def publier_repas(self, repas: list[dict[str, Any]], remplacer: bool = True) -> dict[str, Any]:
        """Publie des repas prévus. Avec remplacer=True, les repas encore « prévus » sur les mêmes
        (date, moment) sont supprimés avant insertion (les repas déjà faits sont conservés)."""
        propres = []
        for r in repas:
            dt, moment = check_date(r.get("date", "")), r.get("moment")
            if moment not in MOMENTS:
                raise CuisineError(f"moment invalide {moment!r} ; attendus : {', '.join(MOMENTS)}")
            rid, plat, lib = r.get("recette"), r.get("plat"), (r.get("libelle") or "").strip() or None
            if not (rid or plat or lib):
                raise CuisineError(f"{dt} {moment} : donner recette, plat ou libelle")
            if rid and not self._one("SELECT id FROM recettes WHERE id=?", (rid,)):
                raise CuisineError(f"recette inconnue : {rid}")
            if plat:
                self._need_produit(plat)
            role = r.get("role") or "principal"
            if role not in ("principal", "option"):
                raise CuisineError("role : principal ou option")
            if moment == "batch":
                total = r.get("portions_total")
                if not rid or not isinstance(total, (int, float)) or total <= 0:
                    raise CuisineError("batch : recette et portions_total obligatoires")
                portions: dict[str, float] = {"total": float(total)}
            else:
                portions = self._portions(r.get("personnes"))
                if plat:  # un plat préparé se mange par portion entière
                    portions = {k: 1.0 for k in portions} if not isinstance(r.get("personnes"), dict) else portions
            propres.append((dt, moment, rid, plat, lib, portions, r.get("produit_sortie"), role, r.get("note")))
        ids = []
        with self._tx():
            if remplacer:
                for dt, moment in {(p[0], p[1]) for p in propres}:
                    self.db.execute("DELETE FROM plan WHERE date=? AND moment=? AND statut='prevu'", (dt, moment))
            for dt, moment, rid, plat, lib, portions, sortie, role, note in propres:
                cur = self.db.execute(
                    "INSERT INTO plan(date, moment, recette_id, plat_id, libelle, portions, produit_sortie, role, note, cree_le)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (dt, moment, rid, plat, lib, json.dumps(portions), sortie, role, note, now_iso()))
                ids.append(cur.lastrowid)
        return {"repas_crees": len(ids), "ids": ids}

    def publier_batch(self, date_: str, plats: list[dict[str, Any]], titre: str | None = None,
                      contenu: str | None = None) -> dict[str, Any]:
        """Prépare le batch : crée le produit « plat préparé » de chaque plat (unité portion, nutrition
        par portion) et le repas « batch » qui consomme les ingrédients et produit les portions."""
        check_date(date_)
        if not plats:
            raise CuisineError("au moins un plat")
        repas = []
        for pl in plats:
            rec = self.recette(pl.get("recette") or "")
            if not rec:
                raise CuisineError(f"recette inconnue : {pl.get('recette')}")
            plat_id = pl.get("plat_id") or f"plat-{rec['id']}"[:80]
            n = rec["nutrition_portion"]
            self.upsert_produit(id=plat_id, nom=pl.get("plat_nom") or rec["titre"], unite="portion",
                                rayon="Préparé maison", conservation="Frigo", recette_id=rec["id"],
                                **{k: n.get(k) for k in NUTRI}, notes=f"Batch du {date_}")
            repas.append({"date": date_, "moment": "batch", "recette": rec["id"], "portions_total": pl.get("portions"),
                          "produit_sortie": plat_id, "note": pl.get("note")})
        res = self.publier_repas(repas, remplacer=True)
        if contenu:
            self.publier_fiche("batch", titre or f"Déroulé du batch ({jour_court(date_)})", contenu, date_=date_)
        return {**res, "plats": [r["produit_sortie"] for r in repas]}

    def plan(self, debut: str, fin: str) -> list[dict[str, Any]]:
        rows = [self._plan_row(r) for r in self._q(
            "SELECT * FROM plan WHERE date BETWEEN ? AND ? ORDER BY date, "
            "CASE moment WHEN 'petit_dej' THEN 0 WHEN 'dejeuner' THEN 1 WHEN 'collation' THEN 2 WHEN 'diner' THEN 3 ELSE 4 END,"
            " role DESC, id", (debut, fin))]
        recs: dict[str, Any] = {}
        for r in rows:
            r["titre"] = self._titre(r)
            r["moment_lib"] = MOMENTS_LIB[r["moment"]]
            rid = r["recette_id"] or (self.produit(r["plat_id"]) or {}).get("recette_id") if r["plat_id"] else r["recette_id"]
            if rid and rid not in recs:
                recs[rid] = self._one("SELECT temps_min FROM recettes WHERE id=?", (rid,))
            r["temps_min"] = recs[rid]["temps_min"] if rid and recs.get(rid) else None
            r["recette_ref"] = rid
            r["dispo"] = self._disponibilite(self.consommation(r)) if r["statut"] == "prevu" else None
        return rows

    def _disponibilite(self, conso: list[dict[str, Any]]) -> dict[str, Any]:
        manque = []
        for c in conso:
            p = self.produit(c["produit"]) or {}
            if (p.get("stock") or 0) + 1e-6 < c["quantite"]:
                manque.append(p.get("nom", c["produit"]))
        return {"ok": not manque, "manque": manque}

    def preparer_validation(self, plan_id: int) -> dict[str, Any]:
        r = self._one("SELECT * FROM plan WHERE id=?", (plan_id,))
        if not r:
            raise CuisineError("repas introuvable")
        row = self._plan_row(r)
        lignes = []
        for sens, items in ((-1, self.consommation(row)), (1, self.production(row))):
            for c in items:
                p = self.produit(c["produit"]) or {"nom": c["produit"], "unite": "g", "stock": 0}
                lignes.append({"produit": c["produit"], "nom": p["nom"], "unite": p["unite"], "quantite": c["quantite"],
                               "sens": "produit" if sens > 0 else "utilise", "stock": p.get("stock") or 0})
        return {"repas": {**row, "titre": self._titre(row)}, "lignes": lignes}

    def valider(self, plan_id: int, statut: str = "fait", lignes: list[dict[str, Any]] | None = None,
                note: str | None = None, source: str = "panneau") -> dict[str, Any]:
        """Marque un repas fait (ou sauté) et met le stock à jour. `lignes` permet d'ajuster les
        quantités réellement utilisées ou produites (sinon : celles de la recette)."""
        if statut not in ("fait", "saute", "remplace", "prevu"):
            raise CuisineError("statut : fait, saute, remplace ou prevu")
        prep = self.preparer_validation(plan_id)
        row = prep["repas"]
        if row["statut"] == "fait" and statut == "fait":
            raise CuisineError("repas déjà validé")
        with self._tx():
            appliques = []
            if statut == "fait":
                prevues = {(l["produit"], l["sens"]): l for l in prep["lignes"]}
                for l in (lignes if lignes is not None else prep["lignes"]):
                    pid, q = l.get("produit"), float(l.get("quantite") or 0)
                    sens = l.get("sens") or prevues.get((pid, "utilise"), {}).get("sens", "utilise")
                    if q <= 0 or not self.produit(pid or ""):
                        continue
                    self._stock_delta(pid, q if sens == "produit" else -q, f"repas {row['date']} {row['moment']}", plan_id)
                    appliques.append({"produit": pid, "quantite": q, "sens": sens})
                if row["role"] == "option" or row["moment"] == "diner":
                    # les autres choix du même créneau sont écartés
                    self.db.execute("UPDATE plan SET statut='ecarte' WHERE date=? AND moment=? AND id<>? AND statut='prevu'",
                                    (row["date"], row["moment"], plan_id))
            self.db.execute("UPDATE plan SET statut=?, note=COALESCE(?, note), fait_le=? WHERE id=?",
                            (statut, note, now_iso() if statut != "prevu" else None, plan_id))
            if source == "panneau":
                self.evenement(f"repas_{statut}", {"plan_id": plan_id, "date": row["date"], "moment": row["moment"],
                                                   "titre": row["titre"], "note": note, "stock": appliques})
        return {"plan_id": plan_id, "statut": statut, "stock": appliques}

    def annuler_validation(self, plan_id: int) -> dict[str, Any]:
        """Remet un repas à « prévu » et annule ses mouvements de stock."""
        with self._tx():
            mvts = self._q("SELECT produit_id, delta FROM mouvements WHERE plan_id=?", (plan_id,))
            for m in mvts:
                self._stock_delta(m["produit_id"], -m["delta"], "annulation", None)
            self.db.execute("DELETE FROM mouvements WHERE plan_id=?", (plan_id,))
            r = self._one("SELECT date, moment FROM plan WHERE id=?", (plan_id,))
            if not r:
                raise CuisineError("repas introuvable")
            self.db.execute("UPDATE plan SET statut='prevu', fait_le=NULL WHERE id=?", (plan_id,))
            self.db.execute("UPDATE plan SET statut='prevu' WHERE date=? AND moment=? AND statut='ecarte'",
                            (r["date"], r["moment"]))
            self.evenement("repas_annule", {"plan_id": plan_id})
        return {"plan_id": plan_id, "statut": "prevu"}

    def _mouvements_repas(self, plan_id: int) -> list[dict[str, Any]]:
        """Mouvements de stock d'un repas fait, regroupés par produit (quantité positive + sens)."""
        tot: dict[str, float] = {}
        for m in self._q("SELECT produit_id, delta FROM mouvements WHERE plan_id=?", (plan_id,)):
            tot[m["produit_id"]] = tot.get(m["produit_id"], 0) + m["delta"]
        out = []
        for pid, d in tot.items():
            if abs(d) < 1e-9:
                continue
            p = self.produit(pid) or {"nom": pid, "unite": "g", "stock": 0}
            out.append({"produit": pid, "nom": p["nom"], "unite": p["unite"], "quantite": round(abs(d), 3),
                        "sens": "produit" if d > 0 else "utilise", "stock": p.get("stock") or 0})
        return out

    def preparer_modification(self, plan_id: int) -> dict[str, Any]:
        """Ce qui a été enregistré pour un repas fait : ingrédients sortis (ou produits) et personnes."""
        r = self._one("SELECT * FROM plan WHERE id=?", (plan_id,))
        if not r:
            raise CuisineError("repas introuvable")
        row = self._plan_row(r)
        if row["statut"] != "fait":
            raise CuisineError("seul un repas fait peut être modifié")
        return {"repas": {**row, "titre": self._titre(row)}, "lignes": self._mouvements_repas(plan_id),
                "profils": [{"id": p["id"], "nom": p["nom"]} for p in self.profils()]}

    def modifier_fait(self, plan_id: int, lignes: list[dict[str, Any]] | None = None, personnes: Any = None,
                      note: str | None = None, source: str = "panneau") -> dict[str, Any]:
        """Corrige un repas déjà fait : remplace ses mouvements de stock par `lignes` (quantités réellement
        utilisées ou produites) et/ou change qui l'a mangé (`personnes` : liste d'ids de profils)."""
        r = self._one("SELECT * FROM plan WHERE id=?", (plan_id,))
        if not r:
            raise CuisineError("repas introuvable")
        row = self._plan_row(r)
        if row["statut"] != "fait":
            raise CuisineError("seul un repas fait peut être modifié")
        if lignes is not None and not isinstance(lignes, list):
            raise CuisineError("lignes invalides")
        portions = None
        if personnes is not None and row["moment"] != "batch":
            if not isinstance(personnes, list) or not personnes:
                raise CuisineError("personnes : au moins un profil")
            nouv = self._portions(personnes)
            anc = row["portions"]
            # on garde les portions existantes des personnes déjà là (portions sur mesure), facteur pour les autres
            portions = {k: (1.0 if row["plat_id"] else v) if k not in anc else anc[k] for k, v in nouv.items()}
        avant = self._mouvements_repas(plan_id)
        with self._tx():
            appliques = None
            if lignes is not None:
                for m in self._q("SELECT produit_id, delta FROM mouvements WHERE plan_id=?", (plan_id,)):
                    self._stock_delta(m["produit_id"], -m["delta"], "correction", None)
                self.db.execute("DELETE FROM mouvements WHERE plan_id=?", (plan_id,))
                appliques = []
                for l in lignes:
                    pid, q = l.get("produit"), float(l.get("quantite") or 0)
                    sens = "produit" if l.get("sens") == "produit" else "utilise"
                    if q <= 0 or not self.produit(pid or ""):
                        continue
                    self._stock_delta(pid, q if sens == "produit" else -q, f"repas {row['date']} {row['moment']} (corrigé)", plan_id)
                    appliques.append({"produit": pid, "quantite": q, "sens": sens})
            if portions is not None:
                self.db.execute("UPDATE plan SET portions=? WHERE id=?", (json.dumps(portions), plan_id))
            if note:
                self.db.execute("UPDATE plan SET note=? WHERE id=?", (note, plan_id))
            if source == "panneau":
                self.evenement("repas_modifie", {
                    "plan_id": plan_id, "date": row["date"], "moment": row["moment"], "titre": self._titre(row),
                    "note": note, "avant": [{k: m[k] for k in ("produit", "quantite", "sens")} for m in avant],
                    "stock": appliques, "personnes_avant": list(row["portions"]),
                    "personnes": list(portions) if portions is not None else None})
        return {"plan_id": plan_id, "stock": appliques, "portions": portions or row["portions"]}

    def supprimer_repas(self, ids: list[int]) -> int:
        with self._tx():
            n = 0
            for i in ids:
                n += self.db.execute("DELETE FROM plan WHERE id=? AND statut IN ('prevu','ecarte','saute')", (i,)).rowcount
        return n

    def ajouter_hors_plan(self, date_: str, moment: str, libelle: str, lignes: list[dict[str, Any]] | None = None,
                          personnes: Any = None) -> dict:
        """Repas mangé hors plan (restes, resto…) : enregistré comme fait, avec consommation éventuelle.
        personnes : profils qui l'ont mangé (liste d'ids), tout le foyer par défaut."""
        if isinstance(personnes, list) and not personnes:
            raise CuisineError("choisir au moins une personne")
        res = self.publier_repas([{"date": date_, "moment": moment, "libelle": libelle, "personnes": personnes or "tous",
                                   "role": "option"}], remplacer=False)
        pid = res["ids"][0]
        return self.valider(pid, "fait", lignes or [], note="hors plan")

    # ------------------------------------------------------------------ jour
    def journee(self, jour: str) -> dict[str, Any]:
        rows = self.plan(jour, jour)
        profils = self.profils()
        tot = {p["id"]: {k: 0.0 for k in NUTRI} for p in profils}
        for r in rows:
            if r["statut"] in ("ecarte", "saute") or (r["role"] == "option" and r["statut"] != "fait") or r["moment"] == "batch":
                continue
            # nutrition par personne
            if r["plat_id"]:
                p = self.produit(r["plat_id"]) or {}
                for pers, n in r["portions"].items():
                    if pers in tot:
                        for k in NUTRI:
                            tot[pers][k] += (p.get(k) or 0) * n
            elif r["recette_id"]:
                rec = self._one("SELECT ingredients FROM recettes WHERE id=?", (r["recette_id"],))
                if rec:
                    ing = json.loads(rec["ingredients"])
                    for pers, n in r["portions"].items():
                        if pers in tot:
                            nut = self.nutrition(ing, n)
                            for k in NUTRI:
                                tot[pers][k] += nut[k]
        bilan = []
        for p in profils:
            bilan.append({"id": p["id"], "nom": p["nom"], "cibles": p["cibles"],
                          **{k: round(v) for k, v in tot[p["id"]].items()}})
        return {"date": jour, "repas": rows, "bilan": bilan}

    def faisables(self, moment: str = "diner", personnes: Any = None, limite: int = 8) -> list[dict[str, Any]]:
        """Recettes du moment donné classées selon ce qui est en stock (pour les dîners improvisés)."""
        portions = self._portions(personnes)
        total = sum(portions.values())
        out = []
        for rec in self.recettes():
            if moment not in rec["moments"]:
                continue
            manque = []
            for i in rec["ingredients"]:
                p = self.produit(i["produit"]) or {}
                if (p.get("stock") or 0) + 1e-6 < i["quantite"] * total:
                    manque.append(p.get("nom", i["produit"]))
            out.append({"id": rec["id"], "titre": rec["titre"], "temps_min": rec["temps_min"], "manque": manque})
        out.sort(key=lambda r: (len(r["manque"]), r["temps_min"] or 99))
        return [r for r in out if len(r["manque"]) <= 1][:limite]

    # ------------------------------------------------------------------ courses
    def prochaines_courses(self, aujourd_hui: date | None = None) -> date:
        t = aujourd_hui or date.today()
        jour = int(self.config("jour_courses", "4") or 4)
        return t + timedelta(days=(jour - t.weekday()) % 7)

    def liste_courses(self, aujourd_hui: date | None = None) -> dict[str, Any]:
        """Liste = besoins des repas prévus sur [courses, courses+7 j[ − stock projeté au jour des courses,
        plus le réassort des basiques, arrondie aux conditionnements Lidl."""
        t = aujourd_hui or date.today()
        d0 = self.prochaines_courses(t)
        d1 = d0 + timedelta(days=7)
        rows = [self._plan_row(r) for r in self._q(
            "SELECT * FROM plan WHERE date>=? AND date<? AND statut='prevu' AND role='principal'",
            (t.isoformat(), d1.isoformat()))]
        avant: dict[str, float] = {}
        besoin: dict[str, float] = {}
        pour: dict[str, list[str]] = {}
        for r in rows:
            cible = avant if r["date"] < d0.isoformat() else besoin
            for c in self.consommation(r):
                cible[c["produit"]] = cible.get(c["produit"], 0) + c["quantite"]
                if cible is besoin:
                    lab = f"{jour_court(r['date'])} · {self._titre(r)}"
                    pour.setdefault(c["produit"], [])
                    if lab not in pour[c["produit"]]:
                        pour[c["produit"]].append(lab)
            for c in self.production(r):
                cible[c["produit"]] = cible.get(c["produit"], 0) - c["quantite"]
        etats = {r["produit_id"]: dict(r) for r in self._q("SELECT * FROM liste_etat")}
        lignes, alertes = [], []
        jour_long = jour_court(d0.isoformat())
        for p in self.produits(actifs=False):
            pid = p["id"]
            projete = max(0.0, (p["stock"] or 0) - avant.get(pid, 0))
            net = besoin.get(pid, 0)
            mini = (p["stock_min"] or 0) if p["basique"] else 0
            reel = max(0.0, net - projete)       # ce qui manque vraiment pour les repas prévus
            fin = projete - net                  # stock restant à la fin de l'horizon
            if p["unite"] == "portion":
                if reel > 0.01:
                    alertes.append(f"{p['nom']} : il manquera {fmt_qte(reel, 'portion')} (vérifier le batch)")
                continue
            if reel > 0.01:
                manque = reel + mini             # on rachète et on reconstitue le minimum des basiques
            elif mini and fin < mini / 2:
                manque = mini - fin              # réassort : basique presque épuisé en fin de semaine
            else:
                manque = 0.0                     # le stock suffit (la marge de sécurité tolère un petit écart)
            if manque <= 0.01 or not p["actif"]:
                continue
            e = etats.get(pid, {})
            if e.get("retire"):
                continue
            ligne = {"produit": pid, "nom": p["nom"], "rayon": p["rayon"] or "Autre", "unite": p["unite"],
                     "besoin": round(manque, 1), "besoin_affichage": fmt_qte(manque, p["unite"]),
                     "prevu": round(net, 1), "stock_projete": round(projete, 1),
                     "detail": (f"prévu {fmt_qte(net, p['unite'])} · {jour_long} : il restera {fmt_qte(projete, p['unite'])}"
                                if net > 0 else f"{jour_long} : il restera {fmt_qte(projete, p['unite'])}")
                               + (f" · minimum {fmt_qte(mini, p['unite'])}" if mini else ""),
                     "pour": pour.get(pid, [])[:4] or (["réassort (basique)"] if mini else []),
                     "coche": bool(e.get("coche"))}
            if p["conditionnement"] and not p["au_poids"]:
                n = math.ceil(manque / p["conditionnement"] - 0.05)
                n = max(n, 1)
                if e.get("paquets") is not None:
                    n = e["paquets"]
                ligne.update(paquets=n, conditionnement=p["conditionnement"],
                             achat=f"{n:g} × {fmt_qte(p['conditionnement'], p['unite'])}",
                             reste_apres=fmt_qte(max(0, n * p["conditionnement"] - manque), p["unite"]),
                             prix=round(n * p["prix"], 2) if p["prix"] else None)
            else:
                q = manque if p["unite"] != "g" else math.ceil(manque / 50) * 50
                if e.get("paquets") is not None:
                    q = e["paquets"]
                ligne.update(paquets=None, quantite=q, achat=("≈ " if p["au_poids"] else "") + fmt_qte(q, p["unite"]),
                             prix=round(q / 1000 * p["prix_kg"], 2) if p["prix_kg"] and p["unite"] in ("g", "ml") else
                             (round(q * p["prix"], 2) if p["prix"] and p["unite"] == "pc" else None))
            lignes.append(ligne)
        ordre = {r: i for i, r in enumerate(RAYONS)}
        lignes.sort(key=lambda l: (ordre.get(l["rayon"], 99), l["nom"]))
        libres = [dict(r) for r in self._q("SELECT id, texte, coche FROM liste_libre ORDER BY id")]
        for l in libres:
            l["coche"] = bool(l["coche"])
        return {
            "date_courses": d0.isoformat(), "horizon_fin": (d1 - timedelta(days=1)).isoformat(),
            "lignes": lignes, "libres": libres, "alertes": alertes,
            "total_estime": round(sum(l["prix"] or 0 for l in lignes), 2),
            "sans_prix": [l["nom"] for l in lignes if l.get("prix") is None],
            "repas_prevus": len([r for r in rows if r["date"] >= d0.isoformat()]),
        }

    def liste_cocher(self, produit: str | None = None, libre_id: int | None = None, coche: bool = True) -> None:
        with self._tx():
            if produit:
                self.db.execute("INSERT INTO liste_etat(produit_id, coche) VALUES(?,?) ON CONFLICT(produit_id) "
                                "DO UPDATE SET coche=excluded.coche", (produit, 1 if coche else 0))
            if libre_id is not None:
                self.db.execute("UPDATE liste_libre SET coche=? WHERE id=?", (1 if coche else 0, libre_id))

    def liste_modifier(self, produit: str, paquets: float | None = None, retire: bool | None = None) -> None:
        self._need_produit(produit)
        with self._tx():
            self.db.execute("INSERT OR IGNORE INTO liste_etat(produit_id) VALUES(?)", (produit,))
            if retire is not None:
                self.db.execute("UPDATE liste_etat SET retire=? WHERE produit_id=?", (1 if retire else 0, produit))
            if paquets is not None:
                self.db.execute("UPDATE liste_etat SET paquets=? WHERE produit_id=?", (paquets if paquets >= 0 else None, produit))

    def liste_ajouter(self, texte: str) -> int:
        texte = (texte or "").strip()[:200]
        if not texte:
            raise CuisineError("texte vide")
        with self._tx():
            return self.db.execute("INSERT INTO liste_libre(texte, ts) VALUES(?,?)", (texte, now_iso())).lastrowid

    def liste_supprimer_libre(self, libre_id: int) -> None:
        with self._tx():
            self.db.execute("DELETE FROM liste_libre WHERE id=?", (libre_id,))

    def preparer_rangement(self) -> list[dict[str, Any]]:
        """Lignes cochées à ranger dans le stock, avec la quantité proposée (modifiable dans le panneau)."""
        out = []
        for l in self.liste_courses()["lignes"]:
            if not l["coche"]:
                continue
            q = l["paquets"] * l["conditionnement"] if l.get("paquets") else l.get("quantite")
            out.append({"produit": l["produit"], "nom": l["nom"], "unite": l["unite"], "quantite": q})
        return out

    def ranger_courses(self, lignes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        lignes = lignes if lignes is not None else self.preparer_rangement()
        res = self.entree_stock(lignes, motif="courses", source="panneau")
        with self._tx():
            self.db.execute("DELETE FROM liste_etat")
            self.db.execute("DELETE FROM liste_libre WHERE coche=1")
        return res

    # ------------------------------------------------------------------ fiches & événements
    def publier_fiche(self, type_: str, titre: str, contenu: str, date_: str | None = None,
                      fid: str | None = None) -> dict[str, Any]:
        if type_ not in FICHES:
            raise CuisineError(f"type de fiche : {', '.join(FICHES)}")
        if date_:
            check_date(date_)
        sem = iso_week(date.fromisoformat(date_)) if date_ else iso_week(date.today())
        fid = fid or (f"{type_}-{sem.lower()}" if type_ in ("semaine", "batch") else f"{date_ or date.today()}-{slugify(titre)}")
        if not _ID.match(fid):
            raise CuisineError("id de fiche invalide")
        if len(contenu or "") > 40_000:
            raise CuisineError("contenu trop long")
        with self._tx():
            self.db.execute("INSERT INTO fiches(id, type, titre, contenu, semaine, date, maj) VALUES(?,?,?,?,?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET titre=excluded.titre, contenu=excluded.contenu, "
                            "semaine=excluded.semaine, date=excluded.date, maj=excluded.maj",
                            (fid, type_, titre.strip(), contenu, sem, date_, now_iso()))
        return {"id": fid, "semaine": sem}

    def fiches(self, type_: str | None = None, semaine: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM fiches WHERE 1=1", []
        if type_:
            sql += " AND type=?"
            args.append(type_)
        if semaine:
            sql += " AND semaine=?"
            args.append(semaine)
        return [dict(r) for r in self._q(sql + " ORDER BY COALESCE(date, maj) DESC", tuple(args))]

    def evenements(self, tous: bool = False) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM evenements" + ("" if tous else " WHERE traite=0") + " ORDER BY id DESC LIMIT 200")
        return [{**dict(r), "details": json.loads(r["details"] or "{}")} for r in rows]

    def marquer_traites(self, ids: list[int]) -> int:
        with self._tx():
            return sum(self.db.execute("UPDATE evenements SET traite=1 WHERE id=?", (i,)).rowcount for i in ids)
