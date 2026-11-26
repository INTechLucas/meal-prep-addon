# Changelog

## 0.2.3
- Lecture des repas, du plan de la semaine et des recettes ouverte aux autres add-ons (réseau interne
  de Home Assistant, lecture seule), pour l'add-on « Liseuse ».

## 0.2.2
- Le panneau Cuisine est visible par les utilisateurs non administrateurs.

## 0.2.1
- Validation d'un repas : bouton « + Ajouter un ingrédient » pour indiquer un remplacement
  (mettre 0 à l'ingrédient remplacé, ajouter celui utilisé). Le stock et Claude en tiennent compte.
- Repas hors plan : saisie des ingrédients utilisés, qui sortent du stock.

## 0.2.0
- Liste de courses : n'achète que ce qui manque vraiment pour les repas prévus. Le minimum des basiques
  n'impose plus un achat pour un écart minime (réassort seulement si le stock prévu en fin de semaine
  tombe sous la moitié du minimum).
- Chaque ligne de la liste affiche la quantité prévue et ce qu'il restera au jour des courses.
- Panneau : fiche produit complète (rayon obligatoire, conservation, paquet, prix, prix au kg, au poids,
  nutrition, basique + minimum, libellé ticket), en création comme en modification (bouton « Fiche produit »
  dans le stock, « Modifier sa fiche » dans l'ajout).

## 0.1.0
- Première version : catalogue Lidl (22 produits du ticket du 20/11/2026), stock, recettes, plan de repas,
  batch cooking, liste de courses arrondie aux conditionnements, envoi vers une liste « À faire » HA.
- Panneau Cuisine (Jour, Semaine, Batch, Stock, Courses) et serveur MCP (23 outils).
