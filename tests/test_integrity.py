from __future__ import annotations

import sqlite3

import pytest

from voting.constants import ROUND_PRINCIPAL
from voting.db import get_db
from voting.elections import ElectionError, close_department
from voting.integrity import (
    compute_result,
    verify_audit_chain,
    verify_department_data,
)

from .conftest import close, make_department, vote


def _tamper_selection(db):
    ballot = db.execute(
        "SELECT id FROM ballots WHERE round = ? ORDER BY id LIMIT 1",
        (ROUND_PRINCIPAL,),
    ).fetchone()
    selected = {
        row["candidate_id"]
        for row in db.execute(
            "SELECT candidate_id FROM ballot_selections WHERE ballot_id = ?",
            (ballot["id"],),
        ).fetchall()
    }
    replacement = db.execute(
        """
        SELECT cd.candidate_id FROM candidate_departments cd
        WHERE cd.round = ? AND cd.candidate_id NOT IN ({})
        LIMIT 1
        """.format(",".join("?" for _ in selected)),
        (ROUND_PRINCIPAL, *selected),
    ).fetchone()
    db.execute("DROP TRIGGER trg_ballot_selections_no_update")
    db.execute(
        "UPDATE ballot_selections SET candidate_id = ? WHERE ballot_id = ? AND position = 1",
        (replacement["candidate_id"], ballot["id"]),
    )


def test_ballot_chain_and_acta_detect_tampering(app):
    data = make_department(app, "Integridad", principal_count=5, treasurer_count=2)
    p = data["principal"]
    t = data["treasurer"]
    vote(app, [p[0], p[1], p[2]], [t[0]])
    vote(app, [p[0], p[1], p[3]], [t[1]])
    close(app, data["department_id"])

    with app.app_context():
        db = get_db()
        verification = verify_department_data(db, data["department_id"])
        assert verification["ok"], verification["checks"]

        computed = compute_result(db, data["department_id"])
        stored = db.execute(
            "SELECT * FROM results WHERE department_id = ?", (data["department_id"],)
        ).fetchone()
        assert computed["result_hash"] == stored["result_hash"]
        assert computed["chain_head"] == stored["chain_head"]

        _tamper_selection(db)
        verification = verify_department_data(db, data["department_id"])
        assert not verification["ok"]
        assert any("Cadena" in check["name"] and not check["ok"] for check in verification["checks"])


def test_close_refuses_to_certify_tampered_votes(app):
    data = make_department(app, "Cierre seguro", principal_count=5, treasurer_count=2)
    p = data["principal"]
    t = data["treasurer"]
    vote(app, [p[0], p[1], p[2]], [t[0]])

    with app.app_context():
        db = get_db()
        _tamper_selection(db)
        with pytest.raises(ElectionError):
            close_department(data["department_id"], actor="pytest")
        assert db.execute(
            "SELECT status FROM departments WHERE id = ?", (data["department_id"],)
        ).fetchone()["status"] == "open"
        assert db.execute(
            "SELECT COUNT(*) FROM results WHERE department_id = ?",
            (data["department_id"],),
        ).fetchone()[0] == 0


def test_result_signature_and_audit_chain_detect_tampering(app):
    data = make_department(app, "Firmas", principal_count=3, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    vote(app, [p[0], p[1], p[2]], [t[0]])
    close(app, data["department_id"])

    with app.app_context():
        db = get_db()
        db.execute("DROP TRIGGER trg_results_no_update")
        db.execute(
            "UPDATE results SET result_hash = ? WHERE department_id = ?",
            ("0" * 64, data["department_id"]),
        )
        verification = verify_department_data(db, data["department_id"])
        assert not verification["ok"]
        assert any("Firma" in check["name"] and not check["ok"] for check in verification["checks"])

        assert verify_audit_chain(db)["ok"]
        db.execute("DROP TRIGGER trg_audit_no_update")
        db.execute("UPDATE audit_log SET actor = 'intruso' WHERE id = 1")
        assert not verify_audit_chain(db)["ok"]


def test_immutability_triggers_block_normal_updates(app):
    data = make_department(app, "Inmutabilidad", principal_count=3, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    vote(app, [p[0], p[1], p[2]], [t[0]])

    with app.app_context():
        db = get_db()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE ballots SET entry_hash = ?", ("0" * 64,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE ballot_selections SET candidate_id = candidate_id")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM ballots")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM audit_log")

    close(app, data["department_id"])
    with app.app_context():
        db = get_db()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE results SET result_hash = ? WHERE department_id = ?",
                ("0" * 64, data["department_id"]),
            )


def test_one_way_triggers_block_reuse_and_reopen(app):
    data = make_department(app, "Una sola vía", principal_count=3, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    vote(app, [p[0], p[1], p[2]], [t[0]])

    with app.app_context():
        db = get_db()
        # Un token usado no puede volver a habilitarse.
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE voter_tokens SET used = 0")
        # Una sesión consumida no puede volver a habilitarse.
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE ballot_sessions SET consumed = 0")

    close(app, data["department_id"])
    with app.app_context():
        db = get_db()
        # Un departamento cerrado no puede reabrirse.
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE departments SET status = 'open' WHERE id = ?",
                (data["department_id"],),
            )
        assert db.execute(
            "SELECT status FROM departments WHERE id = ?", (data["department_id"],)
        ).fetchone()["status"] == "closed"
