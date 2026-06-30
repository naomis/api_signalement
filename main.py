
# Script principal orchestrant l'appel API et l'insertion en base ou le push status
import json
import os
import re
import sys
import requests
from modules.api_client import SignalementAPIClient, AlertAPIClient
from modules.db_utils import PostgresDB
from pyproj import Transformer


def load_config():
    # Priorite: variable d'environnement, sinon config_env.json
    config_filename = os.environ.get("API_SIGNALEMENT_CONFIG", "config_env.json")
    config_path = os.path.join(os.path.dirname(__file__), config_filename)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_codes_insee(raw_codes):
    if raw_codes is None:
        return []
    if isinstance(raw_codes, str):
        return [code.strip() for code in raw_codes.split(",") if code.strip()]
    if isinstance(raw_codes, list):
        return [str(code).strip() for code in raw_codes if str(code).strip()]
    raise ValueError("Le parametre 'codes_insee' doit etre une chaine CSV ou une liste.")


def resolve_codes_insee(config, db):
    # Priorite 1: liste explicite de codes INSEE dans le fichier de configuration
    if config.get("codes_insee"):
        return parse_codes_insee(config.get("codes_insee")), "config.codes_insee"

    # Priorite 2: requete SQL personnalisee dans le fichier de configuration
    if config.get("codes_insee_sql"):
        return db.fetch_codes_insee(config.get("codes_insee_sql")), "config.codes_insee_sql"

    # Priorite 3: comportement historique (requete par defaut)
    return db.fetch_codes_insee(), "requete SQL par defaut"


def validate_table_name(table_name):
    if not isinstance(table_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", table_name):
        raise ValueError("Le parametre 'target_table' est invalide. Format attendu: schema.table ou table.")
    return table_name


def get_runtime_settings(config):
    return {
        "target_table": validate_table_name(config.get("target_table", "bar.adresse_alerte_ban")),
        "pending_status": config.get("pending_status", "PENDING"),
        "ignored_status": config.get("ignored_status", "IGNORED"),
        "processed_by": config.get("processed_by", "Region des Pays de la Loire")
    }

def collecte():
    # --- Lecture du fichier de configuration ---
    config = load_config()

    DB_PARAMS = config["db"]

    BASE_URL = config["api_url"].rstrip("/")
    LIMIT = config.get("limit", 100)
    settings = get_runtime_settings(config)
    target_table = settings["target_table"]
    pending_status = settings["pending_status"]

    db = PostgresDB(**DB_PARAMS)
    codes_insee, codes_source = resolve_codes_insee(config, db)
    print(f"[INFO] Source des codes INSEE: {codes_source}")
    print(f"[INFO] Nombre de codes INSEE utilises: {len(codes_insee)}")
    if not codes_insee:
        db.close()
        raise ValueError("Aucun code INSEE fourni. Verifier 'codes_insee' ou 'codes_insee_sql' dans la configuration.")

    # --- Appel API ---
    signalement_api = SignalementAPIClient(BASE_URL)
    alert_api = AlertAPIClient(BASE_URL)
    data_signalements = signalement_api.fetch_signalements(codes_insee, status=pending_status, limit=LIMIT)
    data_alerts = alert_api.fetch_alerts(codes_insee, status=pending_status, limit=LIMIT)
    data = data_signalements + data_alerts

    # --- Préparation des données à insérer ---
    # On récupère les ids déjà présents en base avec un status différent de PENDING
    db.cur.execute(
        f"SELECT id FROM {target_table} WHERE status != %s;",
        (pending_status,)
    )
    ids_non_pending = set(row[0] for row in db.cur.fetchall())

    # Préparation du log journalier
    from datetime import datetime
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Log global du jour
    global_log_path = os.path.join(log_dir, f"collecte_{datetime.now().strftime('%Y-%m-%d')}.log")
    ignored_ids = []

    import csv
    all_rows = []
    error_rows_alerts = []
    error_rows_signalements = []
    error_log_path = os.path.join(log_dir, "log_errors.log")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    for item in data:
        # Si l'id existe déjà en base avec un status différent de PENDING, on ignore ce signalement
        if item.get("id") in ids_non_pending:
            ignored_ids.append(item.get("id"))
            continue
        # Extraction des champs de existingLocation (correspondance table bar.adresse_alerte_ban)
        existing_location = item.get("existingLocation")
        # Pour log CSV en cas d'erreur
        item_type = item.get("type", "")
        item_id = item.get("id", "")
        existing_location_type = existing_location.get("type") if isinstance(existing_location, dict) else None
        existing_location_ban_id = existing_location.get("banId") if isinstance(existing_location, dict) else None
        existing_location_numero = existing_location.get("numero") if isinstance(existing_location, dict) else None
        existing_location_suffixe = existing_location.get("suffixe") if isinstance(existing_location, dict) else None
        existing_location_nom = existing_location.get("nom") if isinstance(existing_location, dict) else None
        existing_location_geom = None
        existing_location_position = None
        import math
        if (
            isinstance(existing_location, dict)
            and existing_location.get("position")
            and isinstance(existing_location["position"], dict)
        ):
            existing_location_position = existing_location["position"].get("type")
            if (
                existing_location["position"].get("point")
                and isinstance(existing_location["position"]["point"], dict)
                and existing_location["position"]["point"].get("type") == "Point"
                and isinstance(existing_location["position"]["point"].get("coordinates"), list)
                and len(existing_location["position"]["point"].get("coordinates")) == 2
            ):
                x, y = existing_location["position"]["point"]["coordinates"]
                # Détection automatique du système de coordonnées
                if 100000 < x < 1000000 and 6000000 < y < 7300000:
                    # Déjà en Lambert 93
                    x_l93, y_l93 = x, y
                else:
                    # WGS84 -> Lambert 93
                    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
                    x_l93, y_l93 = transformer.transform(x, y)
                if all(isinstance(v, (int, float)) and not math.isnan(v) and not math.isinf(v) for v in [x_l93, y_l93]):
                    existing_location_geom = f"SRID=2154;POINT({x_l93} {y_l93})"
                else:
                    existing_location_geom = None
        # toponyme.nom (colonne existingLocation_toponyme_nom)
        existing_location_toponyme_nom = existing_location.get("toponyme", {}).get("nom") if isinstance(existing_location, dict) else None
        # toponyme.type (colonne existingLocation_toponyme_type)
        existing_location_toponyme_type = existing_location.get("toponyme", {}).get("type") if isinstance(existing_location, dict) else None
        # parcelles (colonne existingLocation_parcelles)
        existing_location_parcelles = ",".join(existing_location.get("parcelles", [])) if isinstance(existing_location, dict) else None

        # Extraction de la position (Point) pour PostGIS selon la hiérarchie
        changes_requested = item.get("changesRequested", {})
        changes_requested_positions = changes_requested.get("positions", []) if isinstance(changes_requested, dict) else []
        changes_requested_position = None
        changes_requested_geom = None
        hierarchy = ['entrée', 'segment', 'bâtiment', 'parcelle']
        selected_pos = None
        if changes_requested_positions and isinstance(changes_requested_positions, list):
            for t in hierarchy:
                for pos in changes_requested_positions:
                    if isinstance(pos, dict) and pos.get('type') == t:
                        selected_pos = pos
                        break
                if selected_pos:
                    break
            if not selected_pos:
                selected_pos = changes_requested_positions[0]
            if (
                isinstance(selected_pos, dict)
                and selected_pos.get("point")
                and isinstance(selected_pos["point"], dict)
                and selected_pos["point"].get("type") == "Point"
                and isinstance(selected_pos["point"].get("coordinates"), list)
                and len(selected_pos["point"].get("coordinates")) == 2
            ):
                x, y = selected_pos["point"]["coordinates"]
                if 100000 < x < 1000000 and 6000000 < y < 7300000:
                    x_l93, y_l93 = x, y
                else:
                    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
                    x_l93, y_l93 = transformer.transform(x, y)
                if all(isinstance(v, (int, float)) and not math.isnan(v) and not math.isinf(v) for v in [x_l93, y_l93]):
                    changes_requested_geom = f"SRID=2154;POINT({x_l93} {y_l93})"
                else:
                    changes_requested_geom = None
                changes_requested_position = selected_pos.get('type')

        # Création de la geom pour la position principale du signalement (colonne geom)
        geom = None
        try:
            if (
                isinstance(item.get("point"), dict)
                and item["point"].get("type") == "Point"
                and isinstance(item["point"].get("coordinates"), list)
                and len(item["point"].get("coordinates")) == 2
            ):
                x, y = item["point"].get("coordinates")
                if 100000 < x < 1000000 and 6000000 < y < 7300000:
                    x_l93, y_l93 = x, y
                else:
                    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
                    x_l93, y_l93 = transformer.transform(x, y)
                if all(isinstance(v, (int, float)) and not math.isnan(v) and not math.isinf(v) for v in [x_l93, y_l93]):
                    geom = f"SRID=2154;POINT({x_l93} {y_l93})"
        except Exception as e:
            geom = None

        # Extraction sécurisée des champs changesRequested et comment
        changes_requested_numero = changes_requested.get("numero") if isinstance(changes_requested, dict) else None
        changes_requested_suffixe = changes_requested.get("suffixe") if isinstance(changes_requested, dict) else None
        changes_requested_nom = changes_requested.get("nom") if isinstance(changes_requested, dict) else None
        changes_requested_nom_voie = changes_requested.get("nomVoie") if isinstance(changes_requested, dict) else None
        changes_requested_nom_complement = changes_requested.get("nomComplement") if isinstance(changes_requested, dict) else None
        changes_requested_parcelles = ",".join(changes_requested.get("parcelles", [])) if isinstance(changes_requested, dict) and isinstance(changes_requested.get("parcelles"), list) else None
        changes_requested_comment = changes_requested.get("comment") if isinstance(changes_requested, dict) else None

        # Extraction sécurisée du champ comment à la racine (pour MISSING_ADDRESS)
        comment = item.get("comment")

        # Sécurisation des autres champs géométriques

        def valid_geom(val, label=None, item_id=None):
            if isinstance(val, str) and val.startswith("SRID=2154;POINT(") and ")" in val:
                try:
                    coords = val.split("(")[-1].split(")")[0].split()
                    if len(coords) == 2:
                        x, y = coords
                        float(x)
                        float(y)
                        return val
                except Exception:
                    pass
            if val not in (None, "") and label:
                print(f"[WARN] Géométrie invalide ignorée pour {label} (id={item_id}): {val}")
            return None

        existing_location_geom = valid_geom(existing_location_geom, "existing_location_geom", item.get("id"))
        changes_requested_geom = valid_geom(changes_requested_geom, "changes_requested_geom", item.get("id"))
        geom = valid_geom(geom, "geom", item.get("id"))

        try:
            all_rows.append((
            item.get("id"),
            item.get("createdAt", None),
            item.get("updatedAt", None),
            item.get("deletedAt", None),
            item.get("codeCommune", None),
            item.get("type", None),
            existing_location_type,
            existing_location_ban_id,
            existing_location_numero,
            existing_location_suffixe,
            existing_location_nom,
            existing_location_geom,  # validé
            existing_location_position,
            existing_location_toponyme_nom,
            existing_location_toponyme_type,
            existing_location_parcelles,
            changes_requested_numero,
            changes_requested_suffixe,
            changes_requested_nom,
            changes_requested_nom_voie,
            changes_requested_nom_complement,
            changes_requested_geom,  # validé
            changes_requested_position,
            changes_requested_parcelles,
            changes_requested_comment if changes_requested_comment is not None else comment,
            item.get("status", None),
            item.get("rejectionReason", None),
            geom,  # validé
            item.get("source", {}).get("id", None),
            item.get("source", {}).get("nom", None),
            item.get("source", {}).get("type", None),
            item.get("processedBy", None),
            item.get("nomCommune", None)
            ))
        except Exception as err:
            # Log fichier
            with open(error_log_path, "a", encoding="utf-8") as ferr:
                ferr.write(f"Erreur insertion id={item_id} type={item_type}: {err}\n")
            # Log CSV
            if item_type == "MISSING_ADDRESS":
                error_rows_alerts.append(item)
            else:
                error_rows_signalements.append(item)

    # --- Nettoyage et insertion en base ---
    db.clear_pending(target_table, pending_status)
    sql = f"""
    INSERT INTO {target_table} (
        id, created_at, updated_at, deleted_at, code_commune, type, existing_location_type, existing_location_ban_id, existing_location_numero, existing_location_suffixe, existing_location_nom, existing_location_geom, existing_location_position, existing_location_toponyme_nom, existing_location_toponyme_type, existing_location_parcelles,
        changes_requested_numero, changes_requested_suffixe, changes_requested_nom, changes_requested_nom_voie, changes_requested_nom_complement, changes_requested_geom, changes_requested_position, changes_requested_parcelles,
        changes_requested_comment, status, rejection_reason, geom, source_id, source_nom, source_type, processed_by, nom_commune
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s), %s, %s, %s, %s,
        %s, %s, %s, %s, %s, ST_GeomFromText(%s), %s, %s,
        %s, %s, %s, ST_GeomFromText(%s), %s, %s, %s, %s, %s
    )
    ON CONFLICT (id) DO NOTHING;
    """
    db.insert_signalements(sql, all_rows)
    db.close()

    # Export CSV des erreurs
    if error_rows_alerts:
        with open(os.path.join(log_dir, "err_alerts.csv"), "w", encoding="utf-8", newline="") as fcsv:
            writer = csv.DictWriter(fcsv, fieldnames=error_rows_alerts[0].keys())
            writer.writeheader()
            writer.writerows(error_rows_alerts)
    if error_rows_signalements:
        with open(os.path.join(log_dir, "err_signalements.csv"), "w", encoding="utf-8", newline="") as fcsv:
            writer = csv.DictWriter(fcsv, fieldnames=error_rows_signalements[0].keys())
            writer.writeheader()
            writer.writerows(error_rows_signalements)

    # Logs globaux
    total = len(data)
    inserted = len(all_rows)
    ignored = len(ignored_ids)
    now_str = datetime.now().isoformat()
    log_lines = [
        f"{now_str} - Début collecte",
        f"{now_str} - Signalements récupérés : {total}",
        f"{now_str} - Signalements insérés : {inserted}",
        f"{now_str} - Signalements ignores (deja en base, status!={pending_status}) : {ignored}",
        f"{now_str} - Fin collecte"
    ]
    # Console
    for l in log_lines:
        print(l)
    # Fichier global
    with open(global_log_path, "a", encoding="utf-8") as f:
        for l in log_lines:
            f.write(l + "\n")

def push():
    # Lecture de la configuration
    config = load_config()

    DB_PARAMS = config["db"]
    API_URL = config["api_url"]
    settings = get_runtime_settings(config)
    target_table = settings["target_table"]
    pending_status = settings["pending_status"]
    ignored_status = settings["ignored_status"]

    # Optionnel : clé API si besoin
    API_KEY = config.get("api_key")
    PROCESSED_BY = settings["processed_by"]

    from datetime import datetime
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"push_{datetime.now().strftime('%Y-%m-%d')}.log")


    db = PostgresDB(**DB_PARAMS)
    # Récupérer tous les signalements à mettre à jour (status != PENDING), avec leur type, sauf ceux déjà traités
    db.cur.execute(
        f"""
        SELECT id, status, rejection_reason, type FROM {target_table}
        WHERE status != %s AND (processed_by IS NULL OR processed_by != %s);
        """,
        (pending_status, PROCESSED_BY)
    )
    rows = db.cur.fetchall()

    total = len(rows)
    success = 0
    errors = []
    now_str = datetime.now().isoformat()
    log_lines = [
        f"{now_str} - Début push",
        f"{now_str} - Signalements à pousser : {total}"
    ]

    for row in rows:
        id_signalement, status, rejection_reason, type_signalement = row
        # Choix de l'endpoint selon le type
        if type_signalement == "MISSING_ADDRESS":
            endpoint = f"{API_URL.rstrip('/')}/alerts/{id_signalement}"
        else:
            endpoint = f"{API_URL.rstrip('/')}/signalements/{id_signalement}"
        try:
            headers = {"Content-Type": "application/json"}
            if API_KEY:
                headers["Authorization"] = f"Bearer {API_KEY}"
            data = {"status": status}
            if status == ignored_status and rejection_reason:
                data["rejectionReason"] = rejection_reason
            resp = requests.put(endpoint, json=data, headers=headers)
            resp.raise_for_status()
            success += 1
            # Mise à jour processed_by en base
            db2 = PostgresDB(**DB_PARAMS)
            db2.cur.execute(
                f"UPDATE {target_table} SET processed_by = %s WHERE id = %s;",
                (PROCESSED_BY, id_signalement)
            )
            db2.conn.commit()
            db2.close()
            msg = f"Signalement {id_signalement} mis à jour: {status} (type={type_signalement})"
            print(msg)
            log_lines.append(f"{datetime.now().isoformat()} - {msg}")
        except Exception as e:
            err_msg = f"Erreur lors du push du signalement {id_signalement} (type={type_signalement}): {e}"
            print(err_msg)
            log_lines.append(f"{datetime.now().isoformat()} - {err_msg}")
            errors.append((id_signalement, str(e)))

    db.close()

    now_str = datetime.now().isoformat()
    log_lines.append(f"{now_str} - Signalements poussés avec succès : {success}")
    log_lines.append(f"{now_str} - Signalements en erreur : {len(errors)}")
    log_lines.append(f"{now_str} - Fin push")

    # Console
    for l in log_lines:
        print(l)
    # Fichier global
    with open(log_path, "a", encoding="utf-8") as f:
        for l in log_lines:
            f.write(l + "\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py [collecte|push]")
        sys.exit(1)
    action = sys.argv[1].lower()
    if action == "collecte":
        collecte()
    elif action == "push":
        push()
    else:
        print("Argument inconnu. Utilisez 'collecte' ou 'push'.")
        sys.exit(1)