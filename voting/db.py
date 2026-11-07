"""Conexión a SQLite y utilidades transaccionales."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from flask import current_app, g

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utc_now() -> str:
    """Marca de tiempo UTC en ISO-8601 (segundos)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db = sqlite3.connect(
            current_app.config["DATABASE"],
            isolation_level=None,  # autocommit; las escrituras usan BEGIN IMMEDIATE
            timeout=10,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        g.db = db
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """Transacción explícita.

    ``BEGIN IMMEDIATE`` serializa las escrituras y evita carreras en el
    control de doble voto y en la apertura/cierre de votaciones.
    """
    db = get_db()
    if db.in_transaction:
        raise RuntimeError("No se permiten transacciones anidadas")
    db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


def _migrate_schema(db: sqlite3.Connection) -> None:
    """Agrega columnas nuevas a bases de datos creadas con versiones anteriores.

    SQLite no permite ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``, así que se
    consulta ``PRAGMA table_info`` antes de modificar. La migración es
    idempotente: se puede ejecutar en cada arranque sin perder datos.
    """
    columns = {row["name"] for row in db.execute("PRAGMA table_info(departments)")}
    if "voting_mode" not in columns:
        db.execute(
            "ALTER TABLE departments "
            "ADD COLUMN voting_mode TEXT NOT NULL DEFAULT 'token'"
        )

    # El modo de votación solo puede cambiarse mientras el departamento está
    # en preparación. Así no se altera una votación ya abierta o cerrada.
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_departments_mode_only_draft
        BEFORE UPDATE OF voting_mode ON departments
        WHEN OLD.status <> 'draft'
        BEGIN
            SELECT RAISE(ABORT, 'El modo de votación solo se puede cambiar en preparación');
        END
        """
    )


def init_db() -> None:
    """Crea el esquema de forma idempotente y aplica migraciones seguras."""
    db = get_db()
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate_schema(db)
    # WAL mejora la concurrencia de lecturas durante la votación.
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
