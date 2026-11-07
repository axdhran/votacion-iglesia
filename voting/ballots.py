"""Canje de tokens, emisión anónima de votos y comprobantes."""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from .constants import (
    DEPARTMENT_CLOSED,
    DEPARTMENT_OPEN,
    ROUND_LABELS,
    ROUND_PRINCIPAL,
    ROUND_SELECTION_COUNT,
    ROUND_TREASURER,
    VOTING_MODE_KIOSK,
)
from .db import get_db, transaction, utc_now
from .elections import list_round_candidates
from .integrity import (
    ballot_entry_hash,
    genesis_hash,
    hash_receipt,
    hash_selections,
    token_hash_candidates,
    verify_ballot_chain,
)


class VoteError(Exception):
    """Error de votación con mensaje apto para mostrar."""


def _parse_candidate_ids(values: list[str], expected: int, label: str) -> list[int]:
    if len(values) != expected:
        raise VoteError(f"Debe seleccionar exactamente {expected} candidato(s) para {label}.")
    parsed: list[int] = []
    for value in values:
        try:
            candidate_id = int(value)
        except (TypeError, ValueError) as exc:
            raise VoteError("Selección de candidato inválida.") from exc
        if candidate_id <= 0:
            raise VoteError("Selección de candidato inválida.")
        parsed.append(candidate_id)
    if len(set(parsed)) != len(parsed):
        raise VoteError("No se puede votar dos veces por el mismo candidato.")
    return parsed


def _ensure_enrolled(
    db: sqlite3.Connection,
    department_id: int,
    round_name: str,
    candidate_ids: list[int],
) -> None:
    placeholders = ",".join("?" for _ in candidate_ids)
    rows = db.execute(
        f"""
        SELECT candidate_id FROM candidate_departments
        WHERE department_id = ? AND round = ? AND candidate_id IN ({placeholders})
        """,
        (department_id, round_name, *candidate_ids),
    ).fetchall()
    found = {int(row["candidate_id"]) for row in rows}
    if found != set(candidate_ids):
        raise VoteError(
            "Una de las selecciones no pertenece a los candidatos inscritos en este departamento y ronda."
        )


def redeem_token(raw_token: str) -> dict[str, Any]:
    """Canjea un token de un solo uso por una sesión anónima.

    No se guarda ninguna relación entre el token y la sesión creada.
    """
    if not isinstance(raw_token, str):
        raise VoteError("Código inválido o ya utilizado.")
    token = raw_token.strip()
    if len(token) < 8 or len(token) > 200:
        raise VoteError("Código inválido o ya utilizado.")
    token_hashes = token_hash_candidates(token)

    with transaction() as tx:
        placeholders = ",".join("?" for _ in token_hashes)
        row = tx.execute(
            f"""
            SELECT t.id, t.department_id, t.used, d.name, d.status
            FROM voter_tokens t
            JOIN departments d ON d.id = t.department_id
            WHERE t.token_hash IN ({placeholders})
            """,
            tuple(token_hashes),
        ).fetchone()
        if row is None or int(row["used"]) == 1:
            raise VoteError("Código inválido o ya utilizado.")
        if row["status"] == DEPARTMENT_CLOSED:
            raise VoteError("La votación de este departamento ya cerró.")
        if row["status"] != DEPARTMENT_OPEN:
            raise VoteError("La votación de este departamento todavía no está abierta.")

        cursor = tx.execute(
            "UPDATE voter_tokens SET used = 1 WHERE id = ? AND used = 0",
            (row["id"],),
        )
        if cursor.rowcount != 1:
            raise VoteError("Código inválido o ya utilizado.")

        session_id = secrets.token_hex(32)
        tx.execute(
            """
            INSERT INTO ballot_sessions (id, department_id, consumed, created_at)
            VALUES (?, ?, 0, ?)
            """,
            (session_id, row["department_id"], utc_now()),
        )
        # Intencionalmente NO se registra en auditoría el canje: un registro
        # del token y la sesión en la misma fila podría vincularlos.
        return {
            "session_id": session_id,
            "department_id": int(row["department_id"]),
            "department_name": row["name"],
        }


def start_kiosk_session(department_id: int) -> dict[str, Any]:
    """Crea una sesión anónima para el modo mesa, sin token.

    En el modo mesa la seguridad física la pone la iglesia: un voluntario
    verifica la identidad fuera del sistema y entrega el dispositivo. El
    sistema solo garantiza que cada sesión emita un voto y que la pantalla
    vuelva a empezar para la siguiente persona.
    """
    with transaction() as tx:
        department = tx.execute(
            "SELECT * FROM departments WHERE id = ?", (department_id,)
        ).fetchone()
        if department is None:
            raise VoteError("El departamento no existe.")
        if department["status"] == DEPARTMENT_CLOSED:
            raise VoteError("La votación de este departamento ya cerró.")
        if department["status"] != DEPARTMENT_OPEN:
            raise VoteError("La votación de este departamento todavía no está abierta.")
        if department["voting_mode"] != VOTING_MODE_KIOSK:
            raise VoteError(
                "Este departamento usa código personal; ingrese su código para votar."
            )

        session_id = secrets.token_hex(32)
        tx.execute(
            """
            INSERT INTO ballot_sessions (id, department_id, consumed, created_at)
            VALUES (?, ?, 0, ?)
            """,
            (session_id, department_id, utc_now()),
        )
        # No se registra en auditoría: no hay token ni identidad que vincular.
        return {
            "session_id": session_id,
            "department_id": int(department_id),
            "department_name": department["name"],
        }


def discard_open_session(session_id: str | None) -> None:
    """Borra una sesión de mesa que quedó sin votar.

    Se usa cuando el voluntario reinicia la pantalla antes de que el votante
    termine. Las sesiones ya consumidas nunca se borran.
    """
    if not session_id:
        return
    with transaction() as tx:
        tx.execute(
            "DELETE FROM ballot_sessions WHERE id = ? AND consumed = 0",
            (session_id,),
        )


def get_open_session(session_id: str, department_id: int) -> sqlite3.Row:
    db = get_db()
    session = db.execute(
        "SELECT * FROM ballot_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise VoteError("La sesión de votación no existe o expiró.")
    if int(session["department_id"]) != int(department_id):
        raise VoteError("La sesión no corresponde a este departamento.")
    if int(session["consumed"]) == 1:
        raise VoteError("Esta sesión ya emitió su voto.")
    department = db.execute(
        "SELECT * FROM departments WHERE id = ?", (department_id,)
    ).fetchone()
    if department is None:
        raise VoteError("El departamento no existe.")
    if department["status"] == DEPARTMENT_CLOSED:
        raise VoteError("La votación de este departamento ya cerró.")
    if department["status"] != DEPARTMENT_OPEN:
        raise VoteError("La votación de este departamento todavía no está abierta.")
    return session


def get_ballot_data(session_id: str, department_id: int) -> dict[str, Any]:
    get_open_session(session_id, department_id)
    department = get_db().execute(
        "SELECT * FROM departments WHERE id = ?", (department_id,)
    ).fetchone()
    return {
        "department": department,
        "principal": list_round_candidates(department_id, ROUND_PRINCIPAL),
        "treasurer": list_round_candidates(department_id, ROUND_TREASURER),
    }


def validate_selection(
    *,
    session_id: str,
    department_id: int,
    principal_values: list[str],
    treasurer_values: list[str],
) -> tuple[list[int], list[int]]:
    """Valida una selección sin guardar nada.

    Se usa para mostrar la pantalla de revisión y para volver a la papeleta a
    corregir. La validación definitiva se repite dentro de ``submit_vote``.
    """
    principal_ids = _parse_candidate_ids(
        principal_values,
        ROUND_SELECTION_COUNT[ROUND_PRINCIPAL],
        "los cargos principales",
    )
    treasurer_ids = _parse_candidate_ids(
        treasurer_values,
        ROUND_SELECTION_COUNT[ROUND_TREASURER],
        "tesorero",
    )
    if set(principal_ids) & set(treasurer_ids):
        raise VoteError(
            "Los candidatos de los cargos principales y de tesorero deben ser distintos."
        )

    get_open_session(session_id, department_id)
    db = get_db()
    _ensure_enrolled(db, department_id, ROUND_PRINCIPAL, principal_ids)
    _ensure_enrolled(db, department_id, ROUND_TREASURER, treasurer_ids)
    return principal_ids, treasurer_ids


def submit_vote(
    *,
    session_id: str,
    department_id: int,
    principal_values: list[str],
    treasurer_values: list[str],
) -> list[dict[str, str]]:
    """Registra las dos papeletas de una sesión anónima en una transacción."""
    principal_ids = _parse_candidate_ids(
        principal_values,
        ROUND_SELECTION_COUNT[ROUND_PRINCIPAL],
        "cargos principales",
    )
    treasurer_ids = _parse_candidate_ids(
        treasurer_values,
        ROUND_SELECTION_COUNT[ROUND_TREASURER],
        "tesorero",
    )
    if set(principal_ids) & set(treasurer_ids):
        raise VoteError(
            "Los candidatos de la ronda principal y de tesorero deben ser distintos."
        )

    with transaction() as tx:
        session = tx.execute(
            "SELECT * FROM ballot_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise VoteError("La sesión de votación no existe o expiró.")
        if int(session["department_id"]) != int(department_id):
            raise VoteError("La sesión no corresponde a este departamento.")
        if int(session["consumed"]) == 1:
            raise VoteError("Esta sesión ya emitió su voto.")

        department = tx.execute(
            "SELECT * FROM departments WHERE id = ?", (department_id,)
        ).fetchone()
        if department is None:
            raise VoteError("El departamento no existe.")
        if department["status"] == DEPARTMENT_CLOSED:
            raise VoteError("La votación de este departamento ya cerró.")
        if department["status"] != DEPARTMENT_OPEN:
            raise VoteError("La votación de este departamento todavía no está abierta.")

        _ensure_enrolled(tx, department_id, ROUND_PRINCIPAL, principal_ids)
        _ensure_enrolled(tx, department_id, ROUND_TREASURER, treasurer_ids)

        head_row = tx.execute(
            "SELECT entry_hash FROM ballots WHERE department_id = ? ORDER BY id DESC LIMIT 1",
            (department_id,),
        ).fetchone()
        prev_hash = head_row["entry_hash"] if head_row else genesis_hash(department_id)
        created_at = utc_now()
        receipts: list[dict[str, str]] = []

        for round_name, ids in (
            (ROUND_PRINCIPAL, sorted(principal_ids)),
            (ROUND_TREASURER, sorted(treasurer_ids)),
        ):
            raw_receipt = secrets.token_urlsafe(24)
            receipt_digest = hash_receipt(raw_receipt)
            selections_digest = hash_selections(ids)
            entry_hash = ballot_entry_hash(
                department_id=department_id,
                round_name=round_name,
                session_id=session_id,
                receipt_hash=receipt_digest,
                candidate_ids=ids,
                created_at=created_at,
                prev_hash=prev_hash,
            )
            cursor = tx.execute(
                """
                INSERT INTO ballots
                    (session_id, department_id, round, receipt_hash,
                     selections_hash, prev_hash, entry_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    department_id,
                    round_name,
                    receipt_digest,
                    selections_digest,
                    prev_hash,
                    entry_hash,
                    created_at,
                ),
            )
            ballot_id = int(cursor.lastrowid)
            for position, candidate_id in enumerate(ids, start=1):
                tx.execute(
                    """
                    INSERT INTO ballot_selections (ballot_id, candidate_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (ballot_id, candidate_id, position),
                )
            prev_hash = entry_hash
            receipts.append(
                {
                    "round": round_name,
                    "label": ROUND_LABELS[round_name],
                    "code": raw_receipt,
                }
            )

        cursor = tx.execute(
            "UPDATE ballot_sessions SET consumed = 1 WHERE id = ? AND consumed = 0",
            (session_id,),
        )
        if cursor.rowcount != 1:
            raise VoteError("Esta sesión ya emitió su voto.")
        return receipts


def verify_receipt(raw_receipt: str) -> dict[str, Any] | None:
    """Verifica que un comprobante exista y que la cadena del departamento sea válida.

    Nunca devuelve las selecciones de la papeleta.
    """
    if not isinstance(raw_receipt, str):
        return None
    receipt = raw_receipt.strip()
    if len(receipt) < 16 or len(receipt) > 200:
        return None
    db = get_db()
    row = db.execute(
        """
        SELECT b.id, b.department_id, b.round, b.created_at, b.entry_hash,
               d.name AS department_name, d.status
        FROM ballots b
        JOIN departments d ON d.id = b.department_id
        WHERE b.receipt_hash = ?
        """,
        (hash_receipt(receipt),),
    ).fetchone()
    if row is None:
        return None
    chain = verify_ballot_chain(db, int(row["department_id"]))
    return {
        "department_id": int(row["department_id"]),
        "department_name": row["department_name"],
        "department_status": row["status"],
        "round": row["round"],
        "round_label": ROUND_LABELS.get(row["round"], row["round"]),
        "created_at": row["created_at"],
        "chain_ok": chain["ok"],
        "entry_hash": row["entry_hash"],
    }
