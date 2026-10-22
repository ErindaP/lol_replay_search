# LoL Replay Multimodal Search

Prototype local pour retrouver des moments de parties de **League of Legends** à partir des timelines Riot, puis comparer des extraits de replay selon leur contexte de jeu, leur description et leur contenu visuel.

Le projet réunit deux usages :

- **Recherche dans les matchs** : requêtes en langage naturel, filtres sur les événements et visualisation PCA des fenêtres retrouvées.
- **Similarité entre clips** : classement de replays courts à partir de trois signaux (texte dérivé des événements, variables de jeu et images vidéo).

> **État du projet.** La similarité multimodale est un prototype non supervisé. Elle combine des descripteurs conçus à la main ; aucun encodeur vidéo ni espace multimodal commun n'est encore entraîné. Les données et les clips locaux ne sont pas inclus dans ce dépôt.

## Démarrage

Le lanceur `start.sh` utilise **Python 3.12** et crée automatiquement `.venv`. Il fonctionne sous Linux ou WSL. Le client League sous Windows n'est nécessaire que pour ouvrir et enregistrer de nouveaux replays ; la recherche sur des données déjà indexées fonctionne hors ligne.

Depuis la racine du dépôt :

```bash
./start.sh setup
cp .env.example .env
```

Renseigner `RIOT_API_KEY` dans `.env` pour importer de nouveaux matchs. Cette clé n'est pas nécessaire pour interroger un index déjà construit. Le fichier `.env` est ignoré par Git.

Un clone GitHub ne contient pas d'index. Il faut soit restaurer ses propres données dans `data/`, soit suivre le pipeline ci-dessous. Une fois l'index créé :

```bash
./start.sh doctor
./start.sh
```

L'interface Streamlit est accessible sur <http://127.0.0.1:8501>. `./start.sh doctor --live` vérifie aussi la clé Riot si une partie archivée est disponible.

## Construire l'index à partir de Riot

```bash
./start.sh cli ingest-summoner --riot-id 'GameName#EUW' --count 20
./start.sh cli build-windows --window-seconds 30 --step-seconds 10
./start.sh cli build-index
```

Le pipeline écrit les matchs et timelines dans `data/raw/`, les fenêtres dans `data/processed/windows.jsonl`, puis les vecteurs et métadonnées de recherche dans `data/index/`. Les répertoires `data/` restent locaux et sont ignorés par Git.

Par défaut, les descriptions des fenêtres sont encodées par un `HashingVectorizer` de 512 dimensions. L'option `--use-pca --pca-dim 128` réduit facultativement cet index au moment de sa construction. Elle est distincte de la PCA en 2D affichée dans l'interface, recalculée sur les résultats d'une recherche pour aider à les explorer.

## Rechercher des moments

```bash
./start.sh cli query --text 'teamfight autour du dragon' --top-k 5
./start.sh cli query-precise --requires-dragon --min-kills 3 --top-k 5
./start.sh cli query-precise --no-kills --window-seconds 30 --top-k 10
```

Les fenêtres indexées contiennent notamment les kills, objectifs, variations d'or, phase de partie, zones approximatives, invasions du jungler et déplacements du midlaner. L'interface combine recherche textuelle, filtres, tableau de résultats et projection PCA. Les positions de la timeline sont échantillonnées à basse fréquence : les signaux de déplacement décrivent des tendances macro, pas une trajectoire seconde par seconde.

## Comparer des clips de replay

Après avoir placé au moins deux clips exportés et alignés avec l'index dans `data/clips/` :

```bash
./start.sh cli similar-clips
./start.sh cli similar-clips \
  --clip NOM_DU_CLIP.mp4.webm \
  --top-k 3 \
  --text-weight 1 --gameplay-weight 1 --video-weight 1
```

Le nom du clip contient son identifiant de match et son intervalle temporel ; le programme l'associe à la fenêtre indexée qui recouvre le plus cet intervalle. Pour chaque clip, il calcule :

| Signal | Représentation actuelle |
| --- | --- |
| Texte | Résumé court dérivé des événements, encodé avec le même `HashingVectorizer` que l'index (512 dimensions par défaut). |
| Gameplay | 14 variables sur les kills, objectifs, variation d'or, phase, invasion et roam. |
| Vidéo | 5 frames échantillonnées ; histogrammes de couleur, contours et variation temporelle (257 dimensions). |

La similarité cosinus est calculée **séparément** pour chaque signal. Les trois scores sont ensuite ramenés à une échelle relative de 0 à 1 sur les clips candidats, puis combinés selon les poids choisis. Ils servent au classement : **1 ne signifie pas une similarité parfaite**. Le texte provient des événements de jeu et n'est donc pas une modalité indépendante. La requête de cette fonction est un *clip de référence* ; une requête textuelle libre utilise la recherche de fenêtres décrite plus haut.

Dans l'interface, la section **Plays similaires** affiche les scores par signal et permet de modifier leurs poids. Les clips incomplets ou sans fenêtre correspondante dans l'index sont ignorés.

## Ouvrir ou exporter un replay

L'export vidéo demande le client League, un replay disponible et l'API locale du replay activée dans `game.cfg` :

```ini
[General]
EnableReplayApi=1
```

Exemple pour un intervalle connu :

```bash
./start.sh cli export-clip \
  --match-id EUW1_1234567890 \
  --start-ms 1250000 --end-ms 1280000 \
  --auto-fetch --auto-load
```

Pour rechercher un moment, ouvrir le replay et enregistrer le résultat sélectionné :

```bash
./start.sh cli query-open-result \
  --text 'teamfight autour du dragon' \
  --top-k 8 --result-index 1 --auto-fetch
```

Sous WSL, le projet peut déléguer au client Windows l'ouverture du replay et le pilotage de l'API locale. Si nécessaire, régler dans `.env` :

```dotenv
REPLAY_USE_LCU=1
REPLAY_LCU_EXECUTOR=windows
REPLAY_API_EXECUTOR=windows
```

`RIOT_CLIENT_PATH` et `LEAGUE_DIR` permettent de remplacer la détection automatique de l'installation. Les autres options d'intégration sont documentées dans [.env.example](.env.example). Le dossier `web/` contient une ancienne interface Next.js ; le parcours principal utilise Streamlit.

## Limites et piste de recherche

Les neuf clips de l'archive locale suffisent à montrer que les signaux ne classent pas toujours les mêmes plays, mais pas à mesurer la qualité du système. Les descripteurs vidéo actuels capturent surtout l'apparence et la variation des images ; ils ne représentent pas explicitement l'intention stratégique d'une action. Aucun jugement humain de similarité n'est encore disponible.

Une suite scientifique serait de constituer un jeu annoté de paires de plays, d'encoder séparément la séquence vidéo et la séquence d'événements, puis d'apprendre leur alignement. L'évaluation devrait comparer vidéo seule, événements seuls et fusion (par exemple avec Recall@K et nDCG), sur des matchs tenus à l'écart de l'entraînement.
