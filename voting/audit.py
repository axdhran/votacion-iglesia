"""Registro de auditoría administrativa encadenado.

Nunca se registra el contenido de una papeleta ni la identidad de un votante.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import utc_now
from .integrity import AUDIT_GENESIS, audit_entry_hash, canonical_json


def log_action(
    db: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    last = db.execute(
        "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = last["entry_hash"] if last else AUDIT_GENESIS
    created_at = utc_now()
    details_json = canonical_json(details or {})
    entity_id_text = None if entity_id is None else str(entity_id)
    entry_hash = audit_entry_hash(
        prev_hash=prev_hash,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id_text,
        details=details_json,
        created_at=created_at,
    )
    db.execute(
        """
        INSERT INTO audit_log
            (actor, action, entity_type, entity_id, details,
             prev_hash, entry_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor,
            action,
            entity_type,
            entity_id_text,
            details_json,
            prev_hash,
            entry_hash,
            created_at,
        ),
    )
