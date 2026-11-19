# Gwiriek

Gestion libre et auto-hébergeable des équipements de protection individuelle (EPI) pour les
petites entreprises de travaux sur cordes : inventaire, fiches de vie, vérifications périodiques
et registre de sécurité.

*Gwiriek* vient du breton *gwiriañ*, « vérifier ».

> Free, self-hosted PPE management for small rope access companies: inventory, life records,
> periodic inspections and safety register. Django application driven from a French admin
> interface, a `gwiriek` command line and an MCP server, all calling the same business
> services. Install with `uv sync`, then see the commands below.

## Sommaire

- [Fonctions](#fonctions)
- [Installation](#installation)
- [Configuration](#configuration)
- [Lancement](#lancement)
- [Ligne de commande](#ligne-de-commande)
- [Serveur MCP](#serveur-mcp)
- [Fin de vie calculée](#fin-de-vie-calculée)
- [Développement](#développement)
- [Licence](#licence)

## Fonctions

- **Catalogue** : fabricants, modèles, famille, normes EN, durées de vie du fabricant (depuis
  la fabrication, stockage maximal, durée d'utilisation), notice, procédure et points de
  contrôle.
- **Articles** : n° de série ou de lot, marquage interne, dates de fabrication, d'achat et de
  mise en service, champs propres aux cordes (diamètre, longueur, type A/B, terminaisons),
  pièces jointes.
- **Statuts** : en stock, en service, à surveiller, quarantaine, rebuté. Chaque changement
  est inscrit dans un journal d'audit non modifiable (auteur, date, motif).
- **Affectations** datées à une personne, un chantier ou au stock, avec historique.
- **Fin de vie calculée** et **échéances** : articles dépassés, proches de leur fin de vie, ou
  dont la fin de vie ne peut pas être calculée faute de données.
- **Interfaces** : admin Django en français, commande `gwiriek`, serveur MCP.

Les vérifications périodiques ont leur modèle de données, sans écran ni commande de saisie.

## Installation

Prérequis : Python 3.12 ou plus récent et [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Snowballe/gwiriek.git
cd gwiriek
uv sync                      # ajouter --extra postgres pour PostgreSQL
uv run gwiriek manage migrate
uv run gwiriek manage load_demo_data   # facultatif : catalogue et articles de démonstration
```

## Configuration

La configuration passe par des variables d'environnement `GWIRIEK_*`, décrites dans
[`.env.example`](.env.example). Pour utiliser un fichier :

```bash
cp .env.example .env
uv run --env-file .env gwiriek articles list
```

Sans `GWIRIEK_DATABASE_URL`, la base est le fichier SQLite `db.sqlite3` du projet. Sans
`GWIRIEK_SECRET_KEY`, une clé éphémère est générée : la CLI et le serveur MCP fonctionnent,
mais les sessions de l'admin ne survivent pas à un redémarrage (`check --deploy` le signale).

## Lancement

```bash
uv run gwiriek manage createsuperuser
uv run gwiriek manage runserver      # admin sur http://127.0.0.1:8000/
```

`gwiriek manage <commande>` transmet la commande à Django, comme `python manage.py`.

## Ligne de commande

```bash
uv run gwiriek --help
uv run gwiriek articles list --status in_service --json
uv run gwiriek articles show DEMO-H01          # id, marquage interne ou n° de série
uv run gwiriek articles add --model 1 --serial 24F0001111 --manufactured 2024-06-01
uv run gwiriek articles status DEMO-H01 quarantine --reason "Chute"
uv run gwiriek articles assign DEMO-H01 --person 2      # ou --site NOM, ou --stock [LIEU]
uv run gwiriek due --within 90
uv run gwiriek catalog models --family rope
uv run gwiriek catalog add-model --manufacturer Petzl --name "AVAO BOD FAST" \
    --family harness --standard "EN 361" --lifetime-months 120
uv run gwiriek people list
```

| Commande | Rôle |
|---|---|
| `articles list` | lister, filtrer par statut, famille, texte ou personne |
| `articles show` | fiche de vie : dates, fin de vie, affectations, historique des statuts |
| `articles add` | enregistrer un article |
| `articles status` | changer le statut (motif obligatoire pour quarantaine et rebut) |
| `articles assign` | affecter à une personne, un chantier ou au stock |
| `due` | fins de vie dépassées, proches, ou à déterminer manuellement |
| `catalog manufacturers`, `models`, `show` | lire le catalogue |
| `catalog add-manufacturer`, `add-model` | compléter le catalogue |
| `people list`, `people add` | personnes |
| `mcp` | serveur MCP sur stdio |
| `manage` | commande de gestion Django |

`articles` a pour alias `items`. Les références ambiguës se préfixent par `id:`, `label:` ou
`serial:`. Les écritures sont attribuées à `cli:<utilisateur>` dans le journal d'audit ;
`--actor` change ce nom.

Sortie lisible par défaut ; `--json` écrit du JSON sur la sortie standard et, en cas d'erreur,
un objet `{"error": {...}}` sur la sortie d'erreur.

| Code de retour | Signification |
|---|---|
| 0 | succès |
| 1 | erreur métier : introuvable, donnée invalide, opération interdite |
| 2 | erreur d'utilisation (arguments) |
| 3 | `due --check` : au moins un article dépassé ou à échéance (utile en tâche cron) |

## Serveur MCP

`gwiriek mcp` lance un serveur [MCP](https://modelcontextprotocol.io) sur le transport stdio,
construit avec le SDK Python officiel.

| Outil | Rôle |
|---|---|
| `search_items` | rechercher des articles (texte, statuts, famille, personne) |
| `get_item` | fiche de vie complète d'un article |
| `list_due_items` | fins de vie dépassées, proches ou indéterminées |
| `list_manufacturers` | fabricants |
| `search_product_models` | rechercher dans le catalogue |
| `get_product_model` | modèle : durées de vie et source, notices, points de contrôle |
| `list_people` | personnes |
| `create_item` | enregistrer un article (auteur `mcp` dans le journal d'audit) |

Aucun outil ne supprime de données. Les erreurs métier reviennent comme erreurs d'outil
(`isError: true`) avec un message lisible par le modèle.

Exemple de configuration pour un client MCP qui lance des serveurs stdio (la clé
`mcpServers` est la plus répandue ; adaptez au format de votre client) :

```json
{
  "mcpServers": {
    "gwiriek": {
      "command": "uv",
      "args": ["run", "--directory", "/chemin/vers/gwiriek", "gwiriek", "mcp"],
      "env": {
        "GWIRIEK_DATABASE_URL": "sqlite:////chemin/vers/gwiriek/db.sqlite3"
      }
    }
  }
}
```

Passez la base de données explicitement dans `env` : les clients ne transmettent en général
pas votre environnement au processus qu'ils lancent.

> **Sécurité** : le transport stdio donne à quiconque lance le processus un accès complet à la
> base, en lecture et en création. Ne le branchez qu'à des clients de confiance, sur la
> machine qui héberge la base. Un transport HTTP avec authentification est prévu pour un
> accès distant.

## Fin de vie calculée

Pour chaque durée fournie par le fabricant :

- **fabrication** : date de fabrication + durée de vie maximale ;
- **stockage** : date de fabrication + durée maximale de stockage, si l'article n'est pas
  encore en service ou a été mis en service après cette date ;
- **utilisation** : date de mise en service + durée maximale d'utilisation.

La fin de vie est la plus proche de ces dates. Les cas sans durée ou sans date sont explicites :
`unlimited` (illimitée selon le fabricant), `undefined` (aucune durée au catalogue), `pending`
(démarre à la mise en service), `unknown` (dates manquantes), `partial` (calculée avec une
partie des données : la fin de vie réelle peut être plus tôt). La date d'achat ne remplace
jamais la date de fabrication. Un article qui a dépassé sa fin de vie ne peut plus être mis en
utilisation ni remis en dotation.

**Ces résultats sont indicatifs et toujours à confirmer selon la notice du fabricant.** Les
durées de vie des données de démonstration citent leur source et sont données à titre
indicatif.

## Développement

```bash
uv run pytest                         # tests (services, CLI, MCP, admin)
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run pydoclint gwiriek
uv run python manage.py makemigrations --check
```

L'architecture, les conventions et les règles pour contribuer sont dans
[`AGENTS.md`](AGENTS.md) ; les choix structurants dans [`docs/decisions.md`](docs/decisions.md).

## Licence

[AGPL-3.0](LICENSE)
