# API Signalement BAN

Script d'exploitation pour integrer les signalements BAN dans une base PostgreSQL/GEO, puis renvoyer les statuts de traitement vers l'API nationale.

## Cloner le repository

```bash
git clone https://github.com/naomis/api_signalement.git
cd api_signalement
```

## Demarrage rapide

```bash
pip install requests psycopg2 pyproj
python main.py collecte
python main.py push
```

Avant execution:
- renseigner `config_env.json`,
- verifier que la table cible existe,
- verifier que `api_key` est actif.

## Objectif

Permettre a une collectivite de reproduire le meme processus sur son propre environnement, sans modifier le code Python, uniquement via un fichier de configuration.

Le processus couvre:
- collecte des signalements,
- stockage en base dans une table de travail,
- traitement dans GEO,
- push des statuts vers l'API,
- automatisation via BAT + Planificateur Windows.

## Contexte API data.gouv.fr

Le script consomme l'API Signalement de la BAN:
- portail: https://adresse.data.gouv.fr/
- Swagger: https://plateforme-bal.adresse.data.gouv.fr/api-signalement/api

Cette API centralise des signalements issus du grand public, de contributeurs OpenStreetMap et de structures partenaires (ex: SDIS).

Pour obtenir un token (`api_key`), il faut faire une demande d'identification et d'acces aupres de data.gouv.fr / equipe BAN.

## Fonctionnement

Le script principal `main.py` expose 2 modes:

1. `collecte`
- lit les signalements au statut `pending_status` (par defaut `PENDING`),
- recharge la table `target_table`.

2. `push`
- envoie les statuts modifies dans GEO (tout statut different de `pending_status`),
- envoie aussi `rejectionReason` si `status = ignored_status`.

## Prerequis

- Python 3.10+
- PostgreSQL + PostGIS
- GEO connecte a la base cible

Dependances Python:
- requests
- psycopg2
- pyproj

Installation:

```bash
pip install requests psycopg2 pyproj
```

## Configuration

Le script lit un fichier JSON:
- variable d'environnement: `API_SIGNALEMENT_CONFIG`
- fichier par defaut: `config_env.json`

Un template est fourni dans `config_env.json`.

Exemple:

```json
{
  "db": {
    "dbname": "DB_NAME",
    "user": "DB_USER",
    "password": "DB_PASSWORD",
    "host": "DB_HOST",
    "port": 5432
  },
  "api_url": "https://plateforme-bal.adresse.data.gouv.fr/api-signalement",
  "api_key": "API_TOKEN",
  "target_table": "signalement_ban",
  "pending_status": "PENDING",
  "ignored_status": "IGNORED",
  "processed_by": "Nom de la collectivite",
  "codes_insee": [],
  "codes_insee_sql": "SELECT code_insee FROM signalement_ban",
  "limit": 100
}
```

Priorite de selection des codes INSEE:
- `codes_insee` (liste ou CSV),
- sinon `codes_insee_sql`,
- sinon requete interne par defaut.

## Execution

Depuis le dossier du projet:

```bash
python main.py collecte
python main.py push
```

Avec un autre fichier de config:

```bash
set API_SIGNALEMENT_CONFIG=config_env_prod.json
python main.py collecte
python main.py push
```

## Automatisation recommandee

- collecte: toutes les heures,
- push: 1 fois par jour (ex: 02:00),
- avant le push: s'assurer que la BAL a ete publiee avec les changements GEO.

## Logs

Les logs sont ecrits dans `logs/`:
- `collecte_YYYY-MM-DD.log`
- `push_YYYY-MM-DD.log`
- `log_errors.log`
- `err_alerts.csv`
- `err_signalements.csv`

## Documentation detaillee

Pour le guide complet exploitation (prerequis SQL, declaration GEO, BAT, planificateur, recette):

- `DOCUMENTATION_EXPLOITATION.md`
