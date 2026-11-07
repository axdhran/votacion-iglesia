"""Mantenimiento de la base de datos: respaldos y reinicio de datos de prueba.

El reinicio está pensado para borrar los datos de una demostración o de unas
pruebas, no para una votación real. Siempre crea un respaldo antes de borrar
y conserva las cuentas de administrador para no dejar el sistema inaccesible.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from .audit import log_action
from .db import get_db, init_db

# Orden de borrado: primero las tablas hijas y al final las que no dependen
# de otras. Los administradores no se tocan.
ELECTION_TABLES = (
    "ballot_selections",
    "ballots",
    "ballot_sessions",
    "voter_tokens",
    "candidate_departments",
    "candidates",
    "results",
    "departments",
    "audit_log",
)

TABLE_LABELS = {
    "departments": "Departamentos",
    "candidates": "Candidatos",
    "candidate_departments": "Inscripciones",
    "voter_tokens": "Tokens",
    "ballot_sessions": "Sesiones anónimas",
    "ballots": "Papeletas",
    "ballot_selections": "Selecciones",
    "results": "Actas",
    "audit_log": "Acciones de auditoría",
}


class MaintenanceError(Exception):
    """Error de mantenimiento con mensaje apto para mostrar al usuario."""


def _backup_dir() -> Path:
    directory = Path(current_app.config["BACKUP_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def backup_database(*, label: str = "manual") -> Path:
    """Copia consistente de la base usando la API de respaldo de SQLite."""
    db = get_db()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = _backup_dir() / f"voting-{label}-{stamp}.db"
    target = sqlite3.connect(destination)
    try:
        db.backup(target)
    finally:
        target.close()
    return destination


def data_counts() -> dict[str, int]:
    """Cantidad actual de filas por tabla, para mostrar en mantenimiento."""
    db = get_db()
    counts: dict[str, int] = {}
    for table in ELECTION_TABLES:
        counts[table] = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def list_backups(limit: int = 5) -> list[dict[str, object]]:
    """Últimos respaldos creados, ordenados del más reciente al más antiguo."""
    directory = _backup_dir()
    files = sorted(
        directory.glob("voting-*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    return [
        {
            "name": path.name,
            "size_kb": max(1, round(path.stat().st_size / 1024)),
            "modified": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC"),
        }
        for path in files
    ]


def reset_election_data(*, actor: str) -> dict[str, object]:
    """Borra todos los datos electorales y conserva los administradores.

    Pasos:
    1. Crear un respaldo completo antes de tocar nada.
    2. Quitar temporalmente los triggers de inmutabilidad.
    3. Borrar las tablas electorales y reiniciar sus contadores.
    4. Registrar el reinicio como primera acción de la nueva auditoría.
    5. Recrear el esquema y los triggers.

    Devuelve las cantidades borradas y la ruta del respaldo.
    """
    db = get_db()
    backup_path = backup_database(label="antes-de-reiniciar")
    counts: dict[str, int] = {}

    # Los triggers de inmutabilidad impiden borrar papeletas, actas y
    # auditoría. Para un reinicio autorizado se quitan y se recrean al final.
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        triggers = [
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        ]
        for name in triggers:
            # Los nombres vienen del propio esquema de SQLite, no del usuario.
            db.execute(f'DROP TRIGGER IF EXISTS "{name}"')

        for table in ELECTION_TABLES:
            counts[table] = int(
                db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            db.execute(f"DELETE FROM {table}")

        placeholders = ", ".join("?" for _ in ELECTION_TABLES)
        db.execute(
            f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
            ELECTION_TABLES,
        )

        # La nueva auditoría empieza con el propio reinicio, sin datos previos.
        log_action(
            db,
            actor=actor,
            action="database.reset",
            entity_type="database",
            details={
                "deleted": counts,
                "backup": backup_path.name,
                "admins_preserved": True,
            },
        )
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")

    # Recrea tablas faltantes, índices, triggers y la migración de modo.
    init_db()
    return {"counts": counts, "backup": backup_path}
