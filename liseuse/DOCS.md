# Liseuse (Kobo)

Sert le panneau de contrôle de la liseuse Kobo, affiché en plein écran par le plugin KOReader
« Panneau Maison » (dossier `koplugin/` de cet add-on).

## Ce que montre la liseuse
- **Maison** : météo, températures, thermostat (consigne et mode de `climate.thermostat_salon`),
  et tous les appareils portant l'étiquette `kobo`, regroupés par pièce. Un appui bascule l'appareil.
- **Repas** : repas du jour ou de la semaine (add-on Cuisine). Un appui ouvre la recette, avec les
  quantités calculées pour les portions prévues.
- **Séances** : séances du jour ou de la semaine de chaque athlète (add-on Coach). Un appui ouvre la fiche.

Pour ajouter ou retirer un appareil de la liseuse, il suffit de lui mettre ou de lui enlever l'étiquette
`kobo` dans Home Assistant.

## Options
| Option | Rôle |
| --- | --- |
| `access_token` | Jeton demandé par la liseuse (en-tête `X-Jeton`). Généré automatiquement s'il est vide. |
| `etiquette` | Étiquette des appareils affichés (défaut `kobo`). |
| `thermostat` | Entité `climate` pilotée depuis la liseuse. |
| `temperatures` | Capteurs de température affichés en haut de l'écran Maison. |
| `meteo` | Entité `weather`. |
| `cuisine_url`, `coach_url` | Adresses internes des add-ons Cuisine et Coach. |

## Sécurité
Le port 8768 n'est destiné qu'au réseau local : ne le redirigez pas sur votre box.
Toutes les requêtes exigent le jeton. L'add-on ne peut basculer que les appareils étiquetés.

## Plugin KOReader
Installation initiale : copier `koplugin/maison.koplugin` dans `.adds/koreader/plugins/` de la liseuse,
puis créer `config.lua` dans ce dossier :

```lua
return {
    url = "http://192.168.x.x:8768",
    jeton = "…",
    demarrage_auto = true,   -- ouvre le panneau au lancement de KOReader
    garder_eveille = true,   -- pas de mise en veille tant que le panneau est affiché
}
```

Ensuite, le plugin se met à jour tout seul : à l'ouverture du panneau, il compare sa version avec celle
embarquée dans l'add-on, télécharge les fichiers modifiés et redémarre KOReader.
