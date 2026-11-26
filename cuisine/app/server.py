"""Outils MCP exposés à Claude (diététicien et gestionnaire des courses du foyer)."""

from __future__ import annotations

import functools
from datetime import date, timedelta
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import ha
from .store import FICHES, MOMENTS, NUTRI, RAYONS, UNITES, CuisineError, Store, fmt_qte, iso_week

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = (
    "Cuisine du foyer (Lucas et Anaïs) : catalogue des produits Lidl, stock, recettes, plan de repas, "
    "batch cooking du dimanche et liste de courses, affichés dans le panneau « Cuisine » de Home Assistant. "
    "Commencer par get_contexte. L'add-on calcule tout ce qui est quantitatif (stock, liste de courses arrondie "
    "aux conditionnements Lidl) : ne jamais recalculer la liste soi-même, utiliser get_liste_courses. "
    "Toute quantité est dans l'unité de base du produit (g, ml, pc ou portion) ; valeurs nutritionnelles "
    "pour 100 g/100 ml ou pour 1 pièce/portion. Une recette décrit UNE portion standard ; chaque profil a un "
    "facteur de portion appliqué automatiquement. Ingrédient absent du catalogue : upsert_produit d'abord. "
    "Le batch du dimanche : publier_batch (crée les plats préparés, unité portion), puis les déjeuners avec "
    "publier_plan en moment=dejeuner et plat=<id du plat>, en alternant au moins deux plats. Dîners : un repas "
    "role=principal (compté dans la liste de courses) et 1 à 2 role=option faisables avec le stock. "
    "Le panneau remonte repas faits, sautés, ajustements de stock et courses rangées dans list_evenements, "
    "à traiter puis marquer_evenements_traites."
)


def build_server(store: Store, options: dict[str, Any]) -> MCPServer:
    mcp = MCPServer(name="cuisine", instructions=INSTRUCTIONS, version="0.1.0")

    def tool(annotations: ToolAnnotations) -> Callable:
        """Décorateur : transforme les erreurs de saisie en ToolError lisibles par Claude."""
        def deco(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*a: Any, **k: Any) -> Any:
                try:
                    return fn(*a, **k)
                except CuisineError as exc:
                    raise ToolError(str(exc)) from exc
            return mcp.tool(annotations=annotations)(wrapper)
        return deco

    # ------------------------------------------------------------------ vue d'ensemble
    @tool(RO)
    def get_contexte() -> dict[str, Any]:
        """Vue d'ensemble à lire en premier : profils (facteur de portion, cibles), date du jour et des
        prochaines courses, stock actuel, plan de cette semaine et de la suivante (résumé), recettes
        disponibles et nombre d'événements du panneau à traiter."""
        t = date.today()
        lundi = t - timedelta(days=t.weekday())
        plan = store.plan(lundi.isoformat(), (lundi + timedelta(days=13)).isoformat())
        return {
            "aujourd_hui": t.isoformat(), "jour": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi",
                                                   "dimanche"][t.weekday()],
            "prochaines_courses": store.prochaines_courses(t).isoformat(),
            "profils": store.profils(),
            "stock": [{k: s[k] for k in ("id", "nom", "affichage", "dlc", "sous_min")} for s in store.stock()],
            "plan": [{"id": r["id"], "date": r["date"], "moment": r["moment"], "titre": r["titre"], "role": r["role"],
                      "statut": r["statut"], "portions": r["portions"]} for r in plan],
            "recettes": [{"id": r["id"], "titre": r["titre"], "moments": r["moments"], "temps_min": r["temps_min"]}
                         for r in store.recettes()],
            "evenements_a_traiter": len(store.evenements()),
            "moments": list(MOMENTS), "rayons": list(RAYONS), "unites": list(UNITES),
        }

    @tool(WRITE)
    def set_profil(id: str, nom: str | None = None, facteur: float | None = None,
                   cibles: dict[str, float] | None = None, notes: str | None = None) -> dict[str, Any]:
        """Crée ou modifie un profil (lucas, anais). facteur : multiplicateur des portions standard des
        recettes (ex. 1.2). cibles : apports journaliers visés, clés kcal, prot, gluc, lip, fibres (g)."""
        return store.set_profil(id, nom, facteur, cibles, notes)

    # ------------------------------------------------------------------ catalogue & stock
    @tool(RO)
    def list_produits(recherche: str | None = None) -> dict[str, Any]:
        """Catalogue des produits (Lidl et plats préparés maison) avec unité, conditionnement, prix,
        valeurs nutritionnelles, libellé du ticket de caisse et stock. recherche : filtre sur le nom."""
        items = store.produits()
        if recherche:
            r = recherche.lower()
            items = [p for p in items if r in p["nom"].lower() or r in (p["libelle_ticket"] or "").lower() or r in p["id"]]
        return {"nombre": len(items), "produits": items}

    @tool(WRITE)
    def upsert_produit(id: str | None = None, nom: str | None = None, unite: str | None = None,
                       conditionnement: float | None = None, au_poids: bool | None = None, prix: float | None = None,
                       prix_kg: float | None = None, rayon: str | None = None, conservation: str | None = None,
                       libelle_ticket: str | None = None, kcal: float | None = None, prot: float | None = None,
                       gluc: float | None = None, lip: float | None = None, fibres: float | None = None,
                       poids_piece: float | None = None, basique: bool | None = None, stock_min: float | None = None,
                       actif: bool | None = None, notes: str | None = None) -> dict[str, Any]:
        """Crée ou complète un produit du catalogue (seuls les champs fournis sont modifiés).

        unite : g, ml, pc ou portion. conditionnement : quantité par paquet Lidl dans cette unité
        (ex. 500 pour un paquet de 500 g) ; au_poids=true pour les produits pesés (prix_kg alors).
        prix : prix d'un paquet. Nutrition pour 100 g/100 ml, ou par pièce si unite=pc.
        basique + stock_min : produit à toujours avoir (réassort automatique dans la liste).
        libelle_ticket : libellé exact sur le ticket Lidl, pour l'association automatique."""
        return store.upsert_produit(
            id=id, nom=nom, unite=unite, conditionnement=conditionnement, au_poids=au_poids, prix=prix,
            prix_kg=prix_kg, rayon=rayon, conservation=conservation, libelle_ticket=libelle_ticket, kcal=kcal,
            prot=prot, gluc=gluc, lip=lip, fibres=fibres, poids_piece=poids_piece, basique=basique,
            stock_min=stock_min, actif=actif, notes=notes)

    @tool(RO)
    def get_stock() -> dict[str, Any]:
        """Stock actuel par produit (quantité, DLC, basiques sous le minimum)."""
        return {"stock": store.stock()}

    @tool(WRITE)
    def ajuster_stock(produit: str, quantite: float | None = None, delta: float | None = None,
                      dlc: str | None = None, motif: str = "ajustement") -> dict[str, Any]:
        """Corrige le stock d'un produit : quantite = nouvelle valeur absolue, ou delta = variation
        (négative pour retirer). dlc au format AAAA-MM-JJ."""
        return store.ajuster_stock(produit, quantite=quantite, delta=delta, dlc=dlc, motif=motif, source="claude")

    @tool(WRITE)
    def entree_stock(lignes: list[dict[str, Any]], motif: str = "courses") -> dict[str, Any]:
        """Ajoute des achats au stock (ticket de caisse). Chaque ligne : {produit, quantite} dans l'unité
        du produit, ou {produit, paquets} ; dlc optionnelle. Les lignes inconnues sont renvoyées en erreur
        sans bloquer les autres."""
        return store.entree_stock(lignes, motif=motif, source="claude")

    # ------------------------------------------------------------------ recettes
    @tool(RO)
    def list_recettes(moment: str | None = None) -> dict[str, Any]:
        """Recettes enregistrées (id, titre, moments, temps, ingrédients par portion)."""
        recs = store.recettes()
        if moment:
            recs = [r for r in recs if moment in r["moments"]]
        return {"nombre": len(recs), "recettes": recs}

    @tool(RO)
    def get_recette(id: str) -> dict[str, Any]:
        """Recette complète avec étapes et nutrition calculée pour une portion standard."""
        r = store.recette(id)
        if not r:
            raise ToolError(f"recette inconnue : {id}")
        return r

    @tool(WRITE)
    def upsert_recette(titre: str, ingredients: list[dict[str, Any]], moments: list[str], etapes: str,
                       temps_min: int | None = None, id: str | None = None, notes: str | None = None,
                       tags: list[str] | None = None) -> dict[str, Any]:
        """Crée ou remplace une recette. ingredients : [{produit, quantite, note?}] pour UNE portion
        standard, quantités dans l'unité du produit. moments parmi petit_dej, dejeuner, collation, diner,
        batch. etapes : Markdown court (liste numérotée), lisible sur téléphone pendant la cuisine.
        Renvoie la nutrition par portion (signale les produits sans valeurs nutritionnelles)."""
        return store.upsert_recette(id=id, titre=titre, ingredients=ingredients, moments=moments,
                                    temps_min=temps_min, etapes=etapes, notes=notes, tags=tags)

    @tool(RO)
    def recettes_faisables(moment: str = "diner", personnes: list[str] | None = None) -> dict[str, Any]:
        """Recettes réalisables avec le stock actuel (ou à un ingrédient près), les plus rapides d'abord."""
        return {"recettes": store.faisables(moment, personnes)}

    # ------------------------------------------------------------------ plan
    @tool(RO)
    def get_plan(debut: str | None = None, fin: str | None = None) -> dict[str, Any]:
        """Repas prévus et réalisés entre deux dates (défaut : semaine en cours), avec statut,
        portions par personne et disponibilité des ingrédients pour les repas encore prévus."""
        t = date.today()
        d0 = debut or (t - timedelta(days=t.weekday())).isoformat()
        d1 = fin or (date.fromisoformat(d0) + timedelta(days=6)).isoformat()
        return {"debut": d0, "fin": d1, "repas": store.plan(d0, d1)}

    @tool(RO)
    def get_journee(date_jour: str | None = None) -> dict[str, Any]:
        """Repas d'une journée et apports estimés par personne comparés à leurs cibles."""
        return store.journee(date_jour or date.today().isoformat())

    @tool(WRITE)
    def publier_plan(repas: list[dict[str, Any]], remplacer: bool = True) -> dict[str, Any]:
        """Publie des repas dans le plan. Chaque repas : {date, moment, recette | plat | libelle, personnes?,
        role?, note?}. personnes : liste de profils (défaut : tout le foyer, portions selon leur facteur)
        ou {profil: nb_portions}. plat : id d'un plat préparé du batch (1 portion par personne).
        libelle seul : repas sans recette (cantine, resto, repas plaisir) sans effet sur le stock.
        role : principal (compté pour les courses) ou option (alternative de dîner).
        remplacer=true supprime d'abord les repas encore prévus sur les mêmes date+moment."""
        return store.publier_repas(repas, remplacer)

    @tool(WRITE)
    def publier_batch(date_batch: str, plats: list[dict[str, Any]], titre: str | None = None,
                      deroule: str | None = None) -> dict[str, Any]:
        """Prépare le batch cooking : plats = [{recette, portions, plat_id?, plat_nom?, note?}].
        Crée chaque plat préparé (unité portion, nutrition par portion) et le repas « batch » qui
        consommera les ingrédients et produira les portions quand il sera validé dans le panneau.
        deroule : Markdown de l'ordonnancement (ce qu'on lance en parallèle, base commune, assaisonnements),
        affiché dans l'onglet Batch. Publier ensuite les déjeuners avec publier_plan (plat=<plat_id>)."""
        return store.publier_batch(date_batch, plats, titre, deroule)

    @tool(DELETE)
    def supprimer_repas(ids: list[int]) -> dict[str, Any]:
        """Supprime des repas non encore faits (ids de get_plan)."""
        return {"supprimes": store.supprimer_repas(ids)}

    @tool(WRITE)
    def valider_repas(id: int, statut: str = "fait", lignes: list[dict[str, Any]] | None = None,
                      note: str | None = None) -> dict[str, Any]:
        """Valide un repas à la place des utilisateurs (fait, saute, remplace, prevu) et met à jour le stock.
        lignes : quantités réellement utilisées [{produit, quantite, sens: utilise|produit}]."""
        return store.valider(id, statut, lignes, note, source="claude")

    # ------------------------------------------------------------------ courses
    @tool(RO)
    def get_liste_courses() -> dict[str, Any]:
        """Liste de courses calculée par l'add-on pour les prochaines courses : besoins des repas prévus
        sur les 7 jours suivant les courses, moins le stock projeté, plus le réassort des basiques,
        arrondie aux conditionnements Lidl, par rayon, avec prix estimé et repas concernés."""
        return store.liste_courses()

    @tool(WRITE)
    def ajouter_liste(texte: str) -> dict[str, Any]:
        """Ajoute un article libre à la liste (hors catalogue, ou rappel)."""
        return {"id": store.liste_ajouter(texte)}

    @tool(WRITE)
    def supprimer_liste(id: int) -> dict[str, Any]:
        """Supprime un article libre de la liste (id de get_liste_courses, champ libres)."""
        store.liste_supprimer_libre(int(id))
        return {"supprime": int(id)}

    @tool(WRITE)
    def envoyer_liste_ha() -> dict[str, Any]:
        """Envoie la liste vers la liste « À faire » de Home Assistant configurée (option todo_entity),
        en remplaçant seulement les éléments ajoutés précédemment par l'add-on."""
        try:
            return ha.envoyer_liste(options.get("todo_entity", ""), store.liste_courses())
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Envoi impossible : {exc}") from exc

    # ------------------------------------------------------------------ fiches & événements
    @tool(WRITE)
    def publish_fiche(type: str, titre: str, contenu: str, date_fiche: str | None = None,
                      id: str | None = None) -> dict[str, Any]:
        """Publie une fiche Markdown dans le panneau : semaine (conseils de la semaine, affichée en
        accueil), batch (déroulé du batch), note. Republier le même id la met à jour."""
        if type not in FICHES:
            raise ToolError(f"type : {', '.join(FICHES)}")
        return store.publier_fiche(type, titre, contenu, date_fiche, id)

    @tool(RO)
    def list_evenements(tous: bool = False) -> dict[str, Any]:
        """Ce que le panneau a remonté (repas faits, sautés, choix de dîner, stock ajusté, courses rangées)."""
        return {"evenements": store.evenements(tous)}

    @tool(WRITE)
    def marquer_evenements_traites(ids: list[int]) -> dict[str, Any]:
        """Marque des événements comme traités."""
        return {"marques": store.marquer_traites(ids)}

    _ = (NUTRI, fmt_qte, iso_week)
    return mcp
