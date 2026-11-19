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
- [Vérifications périodiques](#vérifications-périodiques)
- [Alertes par e-mail](#alertes-par-e-mail)
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
- **Vérificateurs** : liste des personnes qualifiées (R4323-100), internes ou externes, avec
  habilitation écrite (date, référence ou document, échéance éventuelle) et périmètre par
  familles d'EPI ; export CSV.
- **Vérifications périodiques** : points de contrôle du modèle figés dans l'enregistrement,
  décision en 4 états (valide, à surveiller, quarantaine, rebut) appliquée au statut de
  l'article, rapport externe joint ou date et lieu d'archivage, prochaine échéance calculée.
  Une vérification enregistrée n'est plus modifiable.
- **Fin de vie calculée** et **échéances** : fins de vie et vérifications dépassées ou
  proches, stock compris, et fins de vie impossibles à calculer faute de données.
- **Alertes par e-mail** : récapitulatif des échéances, à planifier avec cron ou systemd.
- **Interfaces** : admin Django en français, commande `gwiriek`, serveur MCP.

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

Les e-mails passent par `GWIRIEK_EMAIL_BACKEND` (par défaut la console, qui les affiche au
lieu de les envoyer) et les variables `GWIRIEK_EMAIL_*` ; les destinataires des alertes par
`GWIRIEK_ALERT_RECIPIENTS`.

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
uv run gwiriek inspectors add --person 1 --authorized-on 2026-01-15 --reference "HAB-2026-01" \
    --valid-until 2028-01-14 --all-families
uv run gwiriek inspectors add --name "Erwan Morvan" --organisation "Bureau de vérification" \
    --authorized-on 2026-03-01 --reference "Contrat VGP" --family harness --family rope
uv run gwiriek inspectors export > verificateurs.csv
uv run gwiriek inspections add DEMO-H02 --inspector 1 --decision valid \
    --point 1=ok --point 2=ok --point 3=ok --point 4=ok --point 5=ok --point 6=ok \
    --point "Test de fonctionnement des boucles de réglage=ok"
uv run gwiriek inspections add DEMO-R02 --inspector 2 --decision quarantine --comment "Gaine usée" \
    --point 1=ok --point 2=nok --point 3=ok --point 4=ok \
    --report-received 2026-11-20 --archive-location "Classeur VGP 2026"
uv run gwiriek inspections list --item DEMO-H02 --since 2026-01-01
uv run gwiriek notify --within 30
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
| `due` | fins de vie et vérifications dépassées ou proches, fins de vie à déterminer ; `--inspections-only` |
| `inspectors list`, `show`, `add` | vérificateurs et leur habilitation |
| `inspectors export` | liste des personnes qualifiées en CSV (R4323-100), ou JSON avec `--json` |
| `inspections add` | enregistrer une vérification (`--point POINT=ok\|nok\|na`, par texte ou numéro) |
| `inspections list`, `show` | historique des vérifications, filtrable par article, date, vérificateur |
| `notify` | envoyer le récapitulatif des échéances par e-mail |
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
| 3 | `due --check` : au moins un article dépassé ou à échéance, fin de vie ou vérification |

## Serveur MCP

`gwiriek mcp` lance un serveur [MCP](https://modelcontextprotocol.io) sur le transport stdio,
construit avec le SDK Python officiel.

| Outil | Rôle |
|---|---|
| `search_items` | rechercher des articles (texte, statuts, famille, personne) |
| `get_item` | fiche de vie complète d'un article |
| `list_due_items` | fins de vie et vérifications dépassées ou proches, fins de vie indéterminées |
| `list_inspections_due` | vérifications en retard ou à échéance |
| `list_inspectors` | vérificateurs, habilitation et périmètre |
| `get_inspection_history` | vérifications d'un article |
| `list_manufacturers` | fabricants |
| `search_product_models` | rechercher dans le catalogue |
| `get_product_model` | modèle : durées de vie et source, notices, points de contrôle |
| `list_people` | personnes |
| `create_item` | enregistrer un article |
| `record_inspection` | enregistrer une vérification et appliquer sa décision au statut |
| `change_item_status` | changer le statut d'un article, motif obligatoire |
| `assign_item` | affecter un article à une personne, un chantier ou au stock |

Les écritures sont attribuées à `mcp` dans le journal d'audit. `record_inspection` ne joint pas
de fichier : pour un vérificateur externe, donnez la date de réception et le lieu d'archivage du
rapport, ou joignez-le depuis l'admin.

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
> base, en lecture et en écriture (sans suppression). Ne le branchez qu'à des clients de confiance, sur la
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

## Vérifications périodiques

Les EPI contre les chutes de hauteur, en service ou en stock, doivent avoir été vérifiés depuis
moins de 12 mois (R4323-99, arrêté du 19 mars 1993) par une personne qualifiée (R4323-100).

**Vérificateurs.** Un vérificateur interne est une personne de l'entreprise ; un externe a un
nom et un organisme. Il ne peut enregistrer une vérification que s'il est actif, que son
habilitation écrite (référence ou document) a commencé et n'a pas expiré, le jour de la
vérification comme le jour de l'enregistrement, et que la famille de l'article est dans son
périmètre. On désactive un vérificateur, on ne le supprime pas.

**Saisie.** Depuis la fiche d'un article dans l'admin (« Enregistrer une vérification »), la
CLI ou l'outil MCP `record_inspection` :

- chaque point de contrôle du modèle reçoit un résultat : conforme, non conforme ou sans objet ;
  les points et le vérificateur sont figés dans l'enregistrement ;
- un point non conforme n'autorise que la quarantaine ou le rebut ; un article qui a dépassé sa
  fin de vie aussi ;
- un commentaire est obligatoire sauf pour la décision « valide », et quand le modèle n'a pas
  de points de contrôle ;
- pour un vérificateur externe, le rapport est joint, ou sa date de réception et son lieu
  d'archivage sont indiqués (R4323-102) ;
- un article rebuté n'est plus vérifié ; une vérification ne peut pas être antérieure à la
  précédente.

**Décision et statut.** Le statut est mis à jour et inscrit au journal d'audit : valide laisse un
article en stock ou en service, remet en service un article à surveiller, et sort de quarantaine
vers le service s'il est encore affecté à une personne ou un chantier, vers le stock sinon ; à
surveiller → `watch` ; quarantaine → `quarantine` ; rebut → `retired`.

**Prochaine échéance.** Date de vérification + intervalle du modèle (12 mois au plus), plus tôt
si le vérificateur le demande, jamais après la fin de vie ; aucune après un rebut.

**Article jamais vérifié.** L'échéance part de la plus ancienne des dates de mise en service et
d'achat ; sans aucune des deux, la vérification est due immédiatement.

Une vérification enregistrée n'est pas modifiable : on corrige par une nouvelle vérification.

## Alertes par e-mail

`gwiriek notify` envoie aux adresses de `GWIRIEK_ALERT_RECIPIENTS` le récapitulatif des articles
en retard ou à échéance (30 jours par défaut, `--within`), stock compris. Rien n'est envoyé
quand rien n'est dû, ni quand le même récapitulatif a déjà été envoyé le jour même, sauf avec
`--force`. Les envois sont tracés dans l'admin (« Récapitulatifs par e-mail »).

Exemple de tâche cron, chaque jour ouvré à 7 h :

```cron
0 7 * * 1-5  cd /opt/gwiriek && uv run --env-file .env gwiriek notify
```

Exemple de timer systemd, `/etc/systemd/system/gwiriek-notify.service` :

```ini
[Unit]
Description=Gwiriek: e-mail digest of PPE deadlines

[Service]
Type=oneshot
User=gwiriek
WorkingDirectory=/opt/gwiriek
EnvironmentFile=/opt/gwiriek/.env
ExecStart=/usr/local/bin/uv run gwiriek notify
```

et `/etc/systemd/system/gwiriek-notify.timer` :

```ini
[Unit]
Description=Daily Gwiriek digest

[Timer]
OnCalendar=Mon..Fri 07:00
Persistent=true

[Install]
WantedBy=timers.target
```

Activation : `systemctl enable --now gwiriek-notify.timer`.

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
