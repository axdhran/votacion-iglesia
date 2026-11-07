"""Reglas de administración: candidatos, inscripciones, tokens y cierre."""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from .audit import log_action
from .constants import (
    DEPARTMENT_CLOSED,
    DEPARTMENT_DRAFT,
    DEPARTMENT_OPEN,
    PRINCIPAL_POSITIONS,
    ROUND_PRINCIPAL,
    ROUND_SELECTION_COUNT,
    ROUND_TREASURER,
    ROUNDS,
    TREASURER_POSITION,
    VOTING_MODE_KIOSK,
    VOTING_MODE_TOKEN,
    VOTING_MODES,
)
from .db import get_db, transaction, utc_now
from .integrity import (
    TOKEN_ALPHABET,
    TOKEN_LENGTH,
    canonical_json,
    compute_result,
    format_token,
    hash_token,
    verify_department_data,
)


class ElectionError(Exception):
    """Error de negocio con mensaje apto para mostrar al usuario."""


def normalize_text(value: str, *, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ElectionError(f"{field} inválido.")
    value = " ".join(value.strip().split())
    if len(value) < minimum or len(value) > maximum:
        raise ElectionError(
            f"{field} debe tener entre {minimum} y {maximum} caracteres."
        )
    if any(ord(char) < 32 for char in value):
        raise ElectionError(f"{field} contiene caracteres no permitidos.")
    return value


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def get_department(department_id: int) -> sqlite3.Row | None:
    return get_db().execute(
        "SELECT * FROM departments WHERE id = ?", (department_id,)
    ).fetchone()


def list_departments() -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM departments ORDER BY name COLLATE NOCASE"
    ).fetchall()


def list_kiosk_departments() -> list[sqlite3.Row]:
    """Departamentos abiertos que usan el modo mesa sin código."""
    return get_db().execute(
        """
        SELECT * FROM departments
        WHERE status = ? AND voting_mode = ?
        ORDER BY name COLLATE NOCASE
        """,
        (DEPARTMENT_OPEN, VOTING_MODE_KIOSK),
    ).fetchall()


def list_closed_departments() -> list[sqlite3.Row]:
    return get_db().execute(
        """
        SELECT d.*, r.created_at AS result_created_at
        FROM departments d
        JOIN results r ON r.department_id = d.id
        WHERE d.status = 'closed'
        ORDER BY d.name COLLATE NOCASE
        """
    ).fetchall()


def list_candidates() -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM candidates ORDER BY full_name COLLATE NOCASE"
    ).fetchall()


def get_candidate(candidate_id: int) -> sqlite3.Row | None:
    return get_db().execute(
        "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
    ).fetchone()


def list_departments_with_stats() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT d.*,
          (SELECT COUNT(*) FROM candidate_departments cd
            WHERE cd.department_id = d.id AND cd.round = 'principal') AS principal_candidates,
          (SELECT COUNT(*) FROM candidate_departments cd
            WHERE cd.department_id = d.id AND cd.round = 'treasurer') AS treasurer_candidates,
          (SELECT COUNT(*) FROM voter_tokens t
            WHERE t.department_id = d.id) AS tokens_total,
          (SELECT COUNT(*) FROM voter_tokens t
            WHERE t.department_id = d.id AND t.used = 1) AS tokens_used,
          (SELECT COUNT(*) FROM ballot_sessions s
            WHERE s.department_id = d.id AND s.consumed = 1) AS participations,
          (SELECT COUNT(*) FROM ballots b
            WHERE b.department_id = d.id) AS ballots
        FROM departments d
        ORDER BY d.name COLLATE NOCASE
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_candidates_with_enrollments(
    *,
    department_id: int | None = None,
    round_name: str | None = None,
    query: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Lista candidatos con sus inscripciones, aplicando filtros opcionales.

    - ``department_id``: solo candidatos inscritos en ese departamento.
    - ``round_name``: solo candidatos inscritos en esa ronda.
    - ``query``: búsqueda parcial por nombre (sin distinguir mayúsculas).
    - ``status``: ``enrolled`` (con alguna inscripción) o ``unassigned``
      (sin ninguna inscripción).
    """
    db = get_db()
    conditions: list[str] = []
    params: list[Any] = []

    # Si se piden departamento y ronda a la vez, deben coincidir en la misma
    # inscripción; no basta con que el candidato esté en ambos por separado.
    if department_id is not None and round_name:
        conditions.append(
            """
            EXISTS (
                SELECT 1 FROM candidate_departments cd
                WHERE cd.candidate_id = c.id
                  AND cd.department_id = ? AND cd.round = ?
            )
            """
        )
        params.extend([department_id, round_name])
    elif department_id is not None:
        conditions.append(
            """
            EXISTS (
                SELECT 1 FROM candidate_departments cd
                WHERE cd.candidate_id = c.id AND cd.department_id = ?
            )
            """
        )
        params.append(department_id)
    elif round_name:
        conditions.append(
            """
            EXISTS (
                SELECT 1 FROM candidate_departments cd
                WHERE cd.candidate_id = c.id AND cd.round = ?
            )
            """
        )
        params.append(round_name)

    if query:
        conditions.append("c.full_name LIKE ? COLLATE NOCASE")
        params.append(f"%{query.strip()}%")

    if status == "enrolled":
        conditions.append(
            "EXISTS (SELECT 1 FROM candidate_departments cd WHERE cd.candidate_id = c.id)"
        )
    elif status == "unassigned":
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM candidate_departments cd WHERE cd.candidate_id = c.id)"
        )

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    candidates = db.execute(
        f"SELECT c.* FROM candidates c {where_sql} ORDER BY c.full_name COLLATE NOCASE",
        params,
    ).fetchall()

    enrollments = db.execute(
        """
        SELECT cd.candidate_id, cd.round, d.id AS department_id, d.name AS department_name
        FROM candidate_departments cd
        JOIN departments d ON d.id = cd.department_id
        ORDER BY d.name COLLATE NOCASE, cd.round
        """
    ).fetchall()
    by_candidate: dict[int, list[sqlite3.Row]] = {}
    for enrollment in enrollments:
        by_candidate.setdefault(enrollment["candidate_id"], []).append(enrollment)

    result: list[dict[str, Any]] = []
    for candidate in candidates:
        visible = by_candidate.get(candidate["id"], [])
        # Al filtrar, las etiquetas muestran solo las inscripciones que
        # coinciden con el filtro para que la lista sea fácil de leer.
        if department_id is not None:
            visible = [e for e in visible if int(e["department_id"]) == department_id]
        if round_name:
            visible = [e for e in visible if e["round"] == round_name]
        result.append({"candidate": candidate, "enrollments": visible})
    return result


def list_round_candidates(department_id: int, round_name: str) -> list[sqlite3.Row]:
    return get_db().execute(
        """
        SELECT c.id, c.full_name
        FROM candidate_departments cd
        JOIN candidates c ON c.id = cd.candidate_id
        WHERE cd.department_id = ? AND cd.round = ?
        ORDER BY c.full_name COLLATE NOCASE, c.id
        """,
        (department_id, round_name),
    ).fetchall()


def token_stats(department_id: int) -> dict[str, int]:
    row = get_db().execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(used), 0) AS used
        FROM voter_tokens WHERE department_id = ?
        """,
        (department_id,),
    ).fetchone()
    return {"total": int(row["total"]), "used": int(row["used"])}


def list_audit_entries(limit: int = 500) -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Escrituras administrativas
# ---------------------------------------------------------------------------
def create_department(
    name: str, *, actor: str, voting_mode: str = VOTING_MODE_TOKEN
) -> int:
    """Crea un departamento en preparación.

    ``voting_mode`` decide si se usará código personal o mesa compartida.
    """
    name = normalize_text(name, field="Nombre del departamento", minimum=2, maximum=80)
    if voting_mode not in VOTING_MODES:
        raise ElectionError("Modo de votación inválido.")
    db = get_db()
    try:
        with transaction() as tx:
            cursor = tx.execute(
                """
                INSERT INTO departments (name, status, voting_mode, created_at)
                VALUES (?, 'draft', ?, ?)
                """,
                (name, voting_mode, utc_now()),
            )
            department_id = int(cursor.lastrowid)
            log_action(
                tx,
                actor=actor,
                action="department.create",
                entity_type="department",
                entity_id=department_id,
                details={"name": name, "voting_mode": voting_mode},
            )
        return department_id
    except sqlite3.IntegrityError as exc:
        raise ElectionError("Ya existe un departamento con ese nombre.") from exc


def update_department_mode(department_id: int, voting_mode: str, *, actor: str) -> None:
    """Cambia el modo de acceso mientras el departamento está en preparación."""
    if voting_mode not in VOTING_MODES:
        raise ElectionError("Modo de votación inválido.")
    department = get_department(department_id)
    if department is None:
        raise ElectionError("El departamento no existe.")
    if department["status"] != DEPARTMENT_DRAFT:
        raise ElectionError(
            "El modo de votación solo se puede cambiar antes de abrir la votación."
        )
    if department["voting_mode"] == voting_mode:
        return
    with transaction() as tx:
        cursor = tx.execute(
            """
            UPDATE departments SET voting_mode = ?
            WHERE id = ? AND status = 'draft'
            """,
            (voting_mode, department_id),
        )
        if cursor.rowcount != 1:
            raise ElectionError("El departamento cambió de estado. Recargue la página.")
        log_action(
            tx,
            actor=actor,
            action="department.mode",
            entity_type="department",
            entity_id=department_id,
            details={
                "department_name": department["name"],
                "previous_mode": department["voting_mode"],
                "voting_mode": voting_mode,
            },
        )


def create_candidate(full_name: str, *, actor: str) -> int:
    full_name = normalize_text(
        full_name, field="Nombre del candidato", minimum=2, maximum=120
    )
    with transaction() as tx:
        cursor = tx.execute(
            "INSERT INTO candidates (full_name, created_at) VALUES (?, ?)",
            (full_name, utc_now()),
        )
        candidate_id = int(cursor.lastrowid)
        log_action(
            tx,
            actor=actor,
            action="candidate.create",
            entity_type="candidate",
            entity_id=candidate_id,
            details={"full_name": full_name},
        )
    return candidate_id


def enroll_candidate(
    candidate_id: int,
    department_id: int,
    round_name: str,
    *,
    actor: str,
) -> None:
    if round_name not in ROUNDS:
        raise ElectionError("Ronda inválida.")
    db = get_db()
    candidate = get_candidate(candidate_id)
    department = get_department(department_id)
    if candidate is None:
        raise ElectionError("El candidato no existe.")
    if department is None:
        raise ElectionError("El departamento no existe.")
    if department["status"] != DEPARTMENT_DRAFT:
        raise ElectionError(
            "Solo se puede inscribir mientras el departamento está en preparación."
        )
    existing = db.execute(
        """
        SELECT round FROM candidate_departments
        WHERE candidate_id = ? AND department_id = ?
        """,
        (candidate_id, department_id),
    ).fetchone()
    if existing is not None:
        if existing["round"] == round_name:
            raise ElectionError("El candidato ya está inscrito en esa ronda.")
        raise ElectionError(
            "Un candidato no puede estar en las dos rondas del mismo departamento."
        )

    try:
        with transaction() as tx:
            tx.execute(
                """
                INSERT INTO candidate_departments
                    (candidate_id, department_id, round, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (candidate_id, department_id, round_name, utc_now()),
            )
            log_action(
                tx,
                actor=actor,
                action="candidate.enroll",
                entity_type="candidate_department",
                entity_id=f"{candidate_id}:{department_id}:{round_name}",
                details={
                    "candidate_id": candidate_id,
                    "candidate_name": candidate["full_name"],
                    "department_id": department_id,
                    "department_name": department["name"],
                    "round": round_name,
                },
            )
    except sqlite3.IntegrityError as exc:
        raise ElectionError(
            "Inscripción inválida: revise la ronda y que el candidato no esté ya inscrito."
        ) from exc


def generate_tokens(department_id: int, count: int, *, actor: str) -> list[str]:
    department = get_department(department_id)
    if department is None:
        raise ElectionError("El departamento no existe.")
    if department["status"] == DEPARTMENT_CLOSED:
        raise ElectionError("No se pueden generar tokens para una votación cerrada.")
    # En modo mesa no se usan códigos: la pantalla se comparte y se reinicia.
    if department["voting_mode"] == VOTING_MODE_KIOSK:
        raise ElectionError(
            "Este departamento usa el modo mesa sin código; no necesita tokens."
        )
    if not isinstance(count, int) or count < 1 or count > 500:
        raise ElectionError("La cantidad de tokens debe estar entre 1 y 500.")

    raw_tokens: list[str] = []
    with transaction() as tx:
        for _ in range(count):
            # 12 caracteres de un alfabeto de 32 símbolos = 60 bits de entropía.
            # Se muestra en grupos de 4 para facilitar la lectura y la escritura.
            canonical = "".join(
                secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH)
            )
            display = format_token(canonical)
            token_digest = hash_token(canonical)
            try:
                tx.execute(
                    """
                    INSERT INTO voter_tokens
                        (department_id, token_hash, used, created_at)
                    VALUES (?, ?, 0, ?)
                    """,
                    (department_id, token_digest, utc_now()),
                )
            except sqlite3.IntegrityError:
                # Colisión criptográficamente improbable; se evita guardar
                # un token duplicado y se informa para reintentar.
                raise ElectionError("No se pudo generar un token único. Reintente.")
            raw_tokens.append(display)
        log_action(
            tx,
            actor=actor,
            action="tokens.generate",
            entity_type="department",
            entity_id=department_id,
            details={
                "department_name": department["name"],
                "count": count,
                "note": "Solo se almacenan hashes HMAC; no se registra a quién se entrega.",
            },
        )
    return raw_tokens


def open_department(department_id: int, *, actor: str) -> None:
    department = get_department(department_id)
    if department is None:
        raise ElectionError("El departamento no existe.")
    if department["status"] != DEPARTMENT_DRAFT:
        raise ElectionError("Solo se puede abrir un departamento en preparación.")

    principal_count = int(
        get_db().execute(
            """
            SELECT COUNT(*) FROM candidate_departments
            WHERE department_id = ? AND round = ?
            """,
            (department_id, ROUND_PRINCIPAL),
        ).fetchone()[0]
    )
    treasurer_count = int(
        get_db().execute(
            """
            SELECT COUNT(*) FROM candidate_departments
            WHERE department_id = ? AND round = ?
            """,
            (department_id, ROUND_TREASURER),
        ).fetchone()[0]
    )
    if principal_count < ROUND_SELECTION_COUNT[ROUND_PRINCIPAL]:
        raise ElectionError(
            "La ronda principal necesita al menos 3 candidatos inscritos."
        )
    if treasurer_count < ROUND_SELECTION_COUNT[ROUND_TREASURER]:
        raise ElectionError(
            "La ronda de tesorero necesita al menos 1 candidato inscrito."
        )

    with transaction() as tx:
        cursor = tx.execute(
            """
            UPDATE departments SET status = 'open', opened_at = ?
            WHERE id = ? AND status = 'draft'
            """,
            (utc_now(), department_id),
        )
        if cursor.rowcount != 1:
            raise ElectionError("El departamento cambió de estado. Recargue la página.")
        log_action(
            tx,
            actor=actor,
            action="department.open",
            entity_type="department",
            entity_id=department_id,
            details={
                "department_name": department["name"],
                "principal_candidates": principal_count,
                "treasurer_candidates": treasurer_count,
            },
        )


def close_department(department_id: int, *, actor: str) -> dict[str, Any]:
    department = get_department(department_id)
    if department is None:
        raise ElectionError("El departamento no existe.")
    if department["status"] != DEPARTMENT_OPEN:
        raise ElectionError("Solo se puede cerrar una votación abierta.")

    with transaction() as tx:
        # Se recalcula dentro de la misma transacción de escritura.
        verification = verify_department_data(
            tx, department_id, require_result=False
        )
        if not verification["ok"]:
            failed = [c["name"] for c in verification["checks"] if not c["ok"]]
            raise ElectionError(
                "No se puede certificar el cierre: falló la verificación de integridad ("
                + ", ".join(failed)
                + ")."
            )

        computed = compute_result(tx, department_id)
        tx.execute(
            """
            INSERT INTO results
                (department_id, principal_json, treasurer_json,
                 principal_ballots, treasurer_ballots, participation_count,
                 chain_head, result_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                department_id,
                canonical_json(computed["principal"]),
                canonical_json(computed["treasurer"]),
                computed["principal_ballots"],
                computed["treasurer_ballots"],
                computed["participation_count"],
                computed["chain_head"],
                computed["result_hash"],
                utc_now(),
            ),
        )
        cursor = tx.execute(
            """
            UPDATE departments SET status = 'closed', closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (utc_now(), department_id),
        )
        if cursor.rowcount != 1:
            raise ElectionError("El departamento cambió de estado. Recargue la página.")
        log_action(
            tx,
            actor=actor,
            action="department.close",
            entity_type="department",
            entity_id=department_id,
            details={
                "department_name": department["name"],
                "participation_count": computed["participation_count"],
                "principal_ballots": computed["principal_ballots"],
                "treasurer_ballots": computed["treasurer_ballots"],
                "chain_head": computed["chain_head"],
                "result_hash": computed["result_hash"],
            },
        )
    return computed


def get_stored_result(department_id: int) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT d.id, d.name, d.status, d.closed_at,
               r.principal_json, r.treasurer_json,
               r.principal_ballots, r.treasurer_ballots,
               r.participation_count, r.chain_head, r.result_hash,
               r.created_at AS result_created_at
        FROM departments d
        JOIN results r ON r.department_id = d.id
        WHERE d.id = ? AND d.status = 'closed'
        """,
        (department_id,),
    ).fetchone()
    if row is None:
        return None
    import json

    return {
        "department_id": int(row["id"]),
        "department_name": row["name"],
        "closed_at": row["closed_at"],
        "principal": json.loads(row["principal_json"]),
        "treasurer": json.loads(row["treasurer_json"]),
        "principal_ballots": int(row["principal_ballots"]),
        "treasurer_ballots": int(row["treasurer_ballots"]),
        "participation_count": int(row["participation_count"]),
        "chain_head": row["chain_head"],
        "result_hash": row["result_hash"],
        "result_created_at": row["result_created_at"],
    }


def build_winners(result: dict[str, Any]) -> dict[str, Any]:
    """Asigna puestos con desempate determinista por id de inscripción."""
    principal = result["principal"]
    treasurer = result["treasurer"]
    principal_winners = []
    for index, position in enumerate(PRINCIPAL_POSITIONS):
        if index < len(principal):
            item = dict(principal[index])
            item["position"] = position
            principal_winners.append(item)
    treasurer_winners = []
    if treasurer:
        item = dict(treasurer[0])
        item["position"] = TREASURER_POSITION
        treasurer_winners.append(item)

    principal_tie = (
        len(principal) > 3 and principal[2]["votes"] == principal[3]["votes"]
    )
    treasurer_tie = len(treasurer) > 1 and treasurer[0]["votes"] == treasurer[1]["votes"]
    return {
        "principal": principal_winners,
        "treasurer": treasurer_winners,
        "principal_tie": principal_tie,
        "treasurer_tie": treasurer_tie,
        "tie_rule": "Ante empate se usa el orden de inscripción (menor ID de candidato).",
    }
