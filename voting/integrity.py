"""Criptografía de integridad: hashes, cadena de papeletas y verificación.

La clave ``INTEGRITY_KEY`` vive fuera de la base de datos (variable de entorno
o archivo local). Así, modificar directamente SQLite no basta para recalcular
hashes válidos.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from typing import Any, Iterable

from flask import current_app

from .constants import ROUND_PRINCIPAL, ROUND_SELECTION_COUNT, ROUND_TREASURER

AUDIT_GENESIS = "0" * 64

# Alfabeto sin caracteres ambiguos (sin I, O, 0 ni 1). Pensado para que una
# persona pueda leer y escribir el código con facilidad.
TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOKEN_LENGTH = 12
TOKEN_GROUP_SIZE = 4


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _key() -> bytes:
    key = current_app.config["INTEGRITY_KEY"]
    return key.encode("utf-8") if isinstance(key, str) else key


def _hmac(message: str) -> str:
    return hmac.new(_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_token(raw_token: str) -> str:
    return _hmac(f"token:{raw_token}")


def normalize_token(raw: str) -> str:
    """Normaliza un código para que mayúsculas, espacios y guiones no importen."""
    return re.sub(r"[\s-]+", "", raw).upper()


def format_token(canonical: str) -> str:
    """Presenta un código en grupos de 4 caracteres: ABCD-EFGH-IJKL."""
    return "-".join(
        canonical[index : index + TOKEN_GROUP_SIZE]
        for index in range(0, len(canonical), TOKEN_GROUP_SIZE)
    )


def token_hash_candidates(raw: str) -> list[str]:
    """Hashes posibles para un código.

    Los códigos nuevos se guardan normalizados. Los códigos antiguos se
    guardaban exactamente como se generaron, así que se conserva esa búsqueda
    para no invalidar tarjetas ya impresas.
    """
    stripped = raw.strip()
    hashes = [hash_token(stripped)]
    normalized = normalize_token(stripped)
    if normalized and normalized != stripped:
        hashes.append(hash_token(normalized))
    return list(dict.fromkeys(hashes))


def hash_receipt(raw_receipt: str) -> str:
    return _hmac(f"receipt:{raw_receipt}")


def hash_selections(candidate_ids: Iterable[int]) -> str:
    return hashlib.sha256(
        canonical_json(sorted(int(cid) for cid in candidate_ids)).encode("utf-8")
    ).hexdigest()


def genesis_hash(department_id: int) -> str:
    return _hmac(f"ballot-genesis:{department_id}")


def ballot_entry_hash(
    *,
    department_id: int,
    round_name: str,
    session_id: str,
    receipt_hash: str,
    candidate_ids: Iterable[int],
    created_at: str,
    prev_hash: str,
) -> str:
    payload = {
        "department_id": int(department_id),
        "round": round_name,
        "session_id": session_id,
        "receipt_hash": receipt_hash,
        "selections": sorted(int(cid) for cid in candidate_ids),
        "created_at": created_at,
    }
    return _hmac(f"{prev_hash}\n{canonical_json(payload)}")


def result_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Campos estables que se firman en el acta de resultados."""
    return {
        "department_id": int(result["department_id"]),
        "principal": result["principal"],
        "treasurer": result["treasurer"],
        "principal_ballots": int(result["principal_ballots"]),
        "treasurer_ballots": int(result["treasurer_ballots"]),
        "participation_count": int(result["participation_count"]),
        "chain_head": result["chain_head"],
    }


def result_hash(result: dict[str, Any]) -> str:
    return _hmac(f"result\n{canonical_json(result_payload(result))}")


def audit_entry_hash(
    *,
    prev_hash: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    details: str,
    created_at: str,
) -> str:
    payload = {
        "actor": actor,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details,
        "created_at": created_at,
    }
    return _hmac(f"{prev_hash}\n{canonical_json(payload)}")


def verify_ballot_chain(db: sqlite3.Connection, department_id: int) -> dict[str, Any]:
    """Recalcula la cadena de papeletas de un departamento."""
    errors: list[str] = []
    prev = genesis_hash(department_id)
    rows = db.execute(
        """
        SELECT b.id, b.session_id, b.department_id, b.round, b.receipt_hash,
               b.selections_hash, b.prev_hash, b.entry_hash, b.created_at
        FROM ballots b
        WHERE b.department_id = ?
        ORDER BY b.id ASC
        """,
        (department_id,),
    ).fetchall()

    for row in rows:
        selections = db.execute(
            """
            SELECT candidate_id FROM ballot_selections
            WHERE ballot_id = ? ORDER BY candidate_id ASC
            """,
            (row["id"],),
        ).fetchall()
        candidate_ids = [int(s["candidate_id"]) for s in selections]

        if row["prev_hash"] != prev:
            errors.append(f"Papeleta {row['id']}: prev_hash no coincide con la cadena.")
        if row["selections_hash"] != hash_selections(candidate_ids):
            errors.append(f"Papeleta {row['id']}: selections_hash alterado.")
        expected = ballot_entry_hash(
            department_id=row["department_id"],
            round_name=row["round"],
            session_id=row["session_id"],
            receipt_hash=row["receipt_hash"],
            candidate_ids=candidate_ids,
            created_at=row["created_at"],
            prev_hash=row["prev_hash"],
        )
        if row["entry_hash"] != expected:
            errors.append(f"Papeleta {row['id']}: entry_hash inválido.")
        if len(candidate_ids) != ROUND_SELECTION_COUNT.get(row["round"], -1):
            errors.append(f"Papeleta {row['id']}: cantidad de selecciones inválida.")
        prev = row["entry_hash"]

    return {
        "ok": not errors,
        "head": prev,
        "ballots": len(rows),
        "errors": errors,
    }


def compute_result(db: sqlite3.Connection, department_id: int) -> dict[str, Any]:
    """Calcula el acta directamente desde las papeletas almacenadas."""
    def tally(round_name: str) -> list[dict[str, Any]]:
        rows = db.execute(
            """
            SELECT c.id AS candidate_id, c.full_name,
                   COUNT(b.id) AS votes
            FROM candidate_departments cd
            JOIN candidates c ON c.id = cd.candidate_id
            LEFT JOIN ballot_selections s ON s.candidate_id = cd.candidate_id
            LEFT JOIN ballots b
                   ON b.id = s.ballot_id
                  AND b.department_id = cd.department_id
                  AND b.round = cd.round
            WHERE cd.department_id = ? AND cd.round = ?
            GROUP BY c.id, c.full_name
            ORDER BY votes DESC, c.id ASC
            """,
            (department_id, round_name),
        ).fetchall()
        return [
            {
                "candidate_id": int(r["candidate_id"]),
                "full_name": r["full_name"],
                "votes": int(r["votes"]),
            }
            for r in rows
        ]

    principal = tally(ROUND_PRINCIPAL)
    treasurer = tally(ROUND_TREASURER)
    principal_ballots = int(
        db.execute(
            "SELECT COUNT(*) FROM ballots WHERE department_id = ? AND round = ?",
            (department_id, ROUND_PRINCIPAL),
        ).fetchone()[0]
    )
    treasurer_ballots = int(
        db.execute(
            "SELECT COUNT(*) FROM ballots WHERE department_id = ? AND round = ?",
            (department_id, ROUND_TREASURER),
        ).fetchone()[0]
    )
    participation_count = int(
        db.execute(
            """
            SELECT COUNT(*) FROM ballot_sessions
            WHERE department_id = ? AND consumed = 1
            """,
            (department_id,),
        ).fetchone()[0]
    )
    chain = verify_ballot_chain(db, department_id)

    result = {
        "department_id": int(department_id),
        "principal": principal,
        "treasurer": treasurer,
        "principal_ballots": principal_ballots,
        "treasurer_ballots": treasurer_ballots,
        "participation_count": participation_count,
        "chain_head": chain["head"],
    }
    result["result_hash"] = result_hash(result)
    return result


def verify_department_data(
    db: sqlite3.Connection,
    department_id: int,
    *,
    require_result: bool = True,
) -> dict[str, Any]:
    """Verificación integral de votos, participaciones y acta certificada.

    Devuelve una lista de comprobaciones legibles para el administrador.
    No expone el contenido individual de las papeletas. ``require_result``
    se desactiva justo antes de certificar el cierre, cuando el acta aún no
    existe.
    """
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    chain = verify_ballot_chain(db, department_id)
    add(
        "Cadena criptográfica de papeletas",
        chain["ok"],
        "Cadena válida." if chain["ok"] else "; ".join(chain["errors"][:5]),
    )

    participation = int(
        db.execute(
            """
            SELECT COUNT(*) FROM ballot_sessions
            WHERE department_id = ? AND consumed = 1
            """,
            (department_id,),
        ).fetchone()[0]
    )
    principal_ballots = int(
        db.execute(
            "SELECT COUNT(*) FROM ballots WHERE department_id = ? AND round = ?",
            (department_id, ROUND_PRINCIPAL),
        ).fetchone()[0]
    )
    treasurer_ballots = int(
        db.execute(
            "SELECT COUNT(*) FROM ballots WHERE department_id = ? AND round = ?",
            (department_id, ROUND_TREASURER),
        ).fetchone()[0]
    )

    add(
        "Una papeleta principal por participación",
        principal_ballots == participation,
        f"{principal_ballots} papeletas principales / {participation} participaciones.",
    )
    add(
        "Una papeleta de tesorero por participación",
        treasurer_ballots == participation,
        f"{treasurer_ballots} papeletas de tesorero / {participation} participaciones.",
    )

    session_rows = db.execute(
        """
        SELECT s.id, s.consumed, COUNT(b.id) AS ballot_count,
               COUNT(DISTINCT b.round) AS round_count
        FROM ballot_sessions s
        LEFT JOIN ballots b ON b.session_id = s.id
        WHERE s.department_id = ?
        GROUP BY s.id, s.consumed
        """,
        (department_id,),
    ).fetchall()
    bad_sessions = [
        r["id"]
        for r in session_rows
        if (r["consumed"] == 1 and (r["ballot_count"] != 2 or r["round_count"] != 2))
        or (r["consumed"] == 0 and r["ballot_count"] != 0)
    ]
    add(
        "Integridad de sesiones anónimas",
        not bad_sessions,
        "Cada sesión consumida tiene exactamente dos papeletas."
        if not bad_sessions
        else f"{len(bad_sessions)} sesiones inconsistentes.",
    )

    selection_rows = db.execute(
        """
        SELECT b.id, b.round, COUNT(s.candidate_id) AS selection_count
        FROM ballots b
        LEFT JOIN ballot_selections s ON s.ballot_id = b.id
        WHERE b.department_id = ?
        GROUP BY b.id, b.round
        """,
        (department_id,),
    ).fetchall()
    bad_selections = [
        r["id"]
        for r in selection_rows
        if r["selection_count"] != ROUND_SELECTION_COUNT.get(r["round"], -1)
    ]
    add(
        "Cantidad exacta de selecciones",
        not bad_selections,
        "Todas las papeletas tienen 3 o 1 selección según su ronda."
        if not bad_selections
        else f"{len(bad_selections)} papeletas con selecciones inválidas.",
    )

    enrollment_rows = db.execute(
        """
        SELECT b.id
        FROM ballots b
        JOIN ballot_selections s ON s.ballot_id = b.id
        LEFT JOIN candidate_departments cd
               ON cd.candidate_id = s.candidate_id
              AND cd.department_id = b.department_id
              AND cd.round = b.round
        WHERE b.department_id = ? AND cd.id IS NULL
        """,
        (department_id,),
    ).fetchall()
    add(
        "Candidatos inscritos en su departamento y ronda",
        not enrollment_rows,
        "Todas las selecciones corresponden a candidatos inscritos."
        if not enrollment_rows
        else f"{len(enrollment_rows)} selecciones fuera de la inscripción.",
    )

    computed = compute_result(db, department_id)
    principal_vote_sum = sum(item["votes"] for item in computed["principal"])
    treasurer_vote_sum = sum(item["votes"] for item in computed["treasurer"])
    add(
        "Cuadre de la ronda principal",
        principal_vote_sum == 3 * participation,
        f"{principal_vote_sum} votos emitidos; se esperaban {3 * participation} (3 por participación).",
    )
    add(
        "Cuadre de la ronda de tesorero",
        treasurer_vote_sum == 1 * participation,
        f"{treasurer_vote_sum} votos emitidos; se esperaban {participation} (1 por participación).",
    )

    stored = db.execute(
        "SELECT * FROM results WHERE department_id = ?", (department_id,)
    ).fetchone()
    if stored is None:
        if require_result:
            add(
                "Acta certificada",
                False,
                "El departamento aún no tiene un acta de cierre.",
            )
    else:
        stored_principal = json.loads(stored["principal_json"])
        stored_treasurer = json.loads(stored["treasurer_json"])
        stored_payload = {
            "department_id": int(stored["department_id"]),
            "principal": stored_principal,
            "treasurer": stored_treasurer,
            "principal_ballots": int(stored["principal_ballots"]),
            "treasurer_ballots": int(stored["treasurer_ballots"]),
            "participation_count": int(stored["participation_count"]),
            "chain_head": stored["chain_head"],
        }
        stored_ok = hmac.compare_digest(stored["result_hash"], result_hash(stored_payload))
        matches = (
            stored_principal == computed["principal"]
            and stored_treasurer == computed["treasurer"]
            and int(stored["principal_ballots"]) == computed["principal_ballots"]
            and int(stored["treasurer_ballots"]) == computed["treasurer_ballots"]
            and int(stored["participation_count"]) == computed["participation_count"]
            and stored["chain_head"] == computed["chain_head"]
        )
        add(
            "Firma del acta certificada",
            stored_ok,
            "La firma HMAC del acta es válida." if stored_ok else "La firma del acta no es válida.",
        )
        add(
            "Acta reproducible desde las papeletas",
            matches,
            "El acta coincide con el recálculo actual."
            if matches
            else "El acta almacenada no coincide con las papeletas.",
        )

    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "computed": computed,
        "chain": chain,
    }


def verify_audit_chain(db: sqlite3.Connection) -> dict[str, Any]:
    errors: list[str] = []
    prev = AUDIT_GENESIS
    rows = db.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
    for row in rows:
        if row["prev_hash"] != prev:
            errors.append(f"Auditoría {row['id']}: prev_hash no coincide.")
        expected = audit_entry_hash(
            prev_hash=row["prev_hash"],
            actor=row["actor"],
            action=row["action"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            details=row["details"],
            created_at=row["created_at"],
        )
        if row["entry_hash"] != expected:
            errors.append(f"Auditoría {row['id']}: entry_hash inválido.")
        prev = row["entry_hash"]
    return {"ok": not errors, "entries": len(rows), "head": prev, "errors": errors}
