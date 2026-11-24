# Cuisine (Claude)

L'add-on tient le **stock**, les **recettes**, le **plan de repas** et calcule la **liste de courses**.
Claude, connecté via MCP, compose les menus et le batch cooking ; vous validez les repas dans le panneau.

Répartition des rôles :

| L'add-on (calcul exact) | Claude (jugement) |
| --- | --- |
| Stock, retrait des ingrédients quand un repas est validé | Menus de la semaine selon vos plans et le stock |
| Liste de courses = besoins − stock projeté, arrondie aux paquets Lidl | Batch du dimanche (2 plats, base commune) |
| Apports estimés par personne | Options de dîner, adaptation à ce qui a été mangé |

## Configuration

| Option | Rôle |
| --- | --- |
| `access_token` | Jeton secret d'au moins 32 caractères qui forme l'URL. Laissez vide au premier démarrage : un jeton est généré et affiché **une fois** dans le journal ; recopiez-le ici. **Utilisez un jeton différent de celui du Coach.** |
| `todo_entity` | Liste « À faire » où envoyer la liste de courses (ex. `todo.shopping_list`). Facultatif. |
| `jour_courses` | Jour des courses (0 = lundi … 4 = vendredi, par défaut). |
| `ssl`, `certfile`, `keyfile` | Certificat Let's Encrypt présent dans `/ssl`, comme pour le Coach. |
| `log_level` | Niveau de journalisation. |

## Connecteur Claude

Même principe que le Coach, sur un chemin différent pour que les deux cohabitent derrière le proxy NGINX :

1. Copier `examples/nginx_proxy_default_cuisine.conf` dans le dossier `share` (à côté de celui du Coach),
   en remplaçant `<hostname-de-l-add-on>` par le hostname de l'add-on Cuisine (page de l'add-on).
2. Redémarrer l'add-on NGINX.
3. Dans Claude : ajouter un connecteur personnalisé (« No sign-in ») avec l'URL
   `https://<votre-domaine>/<access_token>/cuisine/mcp`

Le port 8767 n'a pas besoin d'être ouvert vers l'extérieur.

## Panneau Cuisine

Accessible dans la barre latérale de Home Assistant et l'appli mobile (Ingress uniquement).

- **Jour** : repas prévus par moment ; « Fait ✓ » retire les ingrédients du stock (quantités modifiables),
  « Pas fait », options de dîner au choix, recettes faisables avec le stock, repas hors plan, apports estimés.
- **Semaine** : plan jour par jour ; un nom de plat ouvre la recette avec les quantités du repas.
- **Batch** : plats du dimanche, déroulé, plats préparés restants, déjeuners de la semaine.
- **Stock** : par rayon, boutons −/+, correction exacte et date limite, ajout de produit.
- **Courses** : liste par rayon pour les prochaines courses, paquets ajustables, articles libres,
  « Courses rangées → stock » pour tout faire entrer d'un coup (quantités pesées modifiables).

Chaque action du panneau est remontée à Claude (`list_evenements`).

## Données

Base SQLite `/data/cuisine.db`, incluse dans les sauvegardes de l'add-on.

## Cycle de la semaine

- **Vendredi soir** : courses avec la liste, puis « Courses rangées → stock ».
- **Samedi** : Claude publie le plan de la semaine et le batch.
- **Dimanche** : batch → « Batch fait ✓ » (ingrédients retirés, portions ajoutées).
- **Chaque jour** : valider les repas ; la liste de la semaine suivante se met à jour toute seule.
