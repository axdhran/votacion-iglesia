from __future__ import annotations

import re

import pytest

from voting.ballots import VoteError, submit_vote
from voting.db import get_db
from voting.elections import (
    ElectionError,
    enroll_candidate,
    get_stored_result,
    open_department,
)
from voting.constants import ROUND_TREASURER
from voting.integrity import verify_department_data

from .conftest import cast, close, issue_token, make_department, redeem, vote


def test_counts_by_round_and_winners(app, client):
    data = make_department(app, "Jóvenes", principal_count=5, treasurer_count=2)
    p = data["principal"]
    t = data["treasurer"]
    department_id = data["department_id"]

    vote(app, [p[0], p[1], p[2]], [t[0]])
    vote(app, [p[0], p[1], p[3]], [t[1]])
    vote(app, [p[0], p[2], p[3]], [t[0]])

    # Antes del cierre no se publica ni el acta ni el detalle.
    assert client.get(f"/resultados/{department_id}").status_code == 404
    listing = client.get("/resultados")
    assert listing.status_code == 200
    assert "Jóvenes" not in listing.get_data(as_text=True)

    result = close(app, department_id)
    assert result["participation_count"] == 3
    assert result["principal_ballots"] == 3
    assert result["treasurer_ballots"] == 3

    with app.app_context():
        stored = get_stored_result(department_id)
        assert stored is not None
        principal = {item["candidate_id"]: item["votes"] for item in stored["principal"]}
        treasurer = {item["candidate_id"]: item["votes"] for item in stored["treasurer"]}
        assert principal[p[0]] == 3
        assert principal[p[1]] == 2
        assert principal[p[2]] == 2
        assert principal[p[3]] == 2
        assert principal[p[4]] == 0
        assert treasurer[t[0]] == 2
        assert treasurer[t[1]] == 1

        verification = verify_department_data(get_db(), department_id)
        assert verification["ok"], verification["checks"]

        # Desempate determinista: p2 (ID menor) queda tercero frente a p3.
        assert [item["candidate_id"] for item in stored["principal"][:3]] == [
            p[0],
            p[1],
            p[2],
        ]
        assert stored["treasurer"][0]["candidate_id"] == t[0]

    detail = client.get(f"/resultados/{department_id}")
    assert detail.status_code == 200
    html = detail.get_data(as_text=True)
    assert "Presidente" in html
    assert "Vicepresidente" in html
    assert "Secretario" in html
    assert "Tesorero" in html
    assert "Integridad verificada" in html


def test_double_vote_is_blocked_by_token_and_session(app):
    data = make_department(app, "Doble voto", principal_count=3, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    token = issue_token(app, data["department_id"])

    voter = app.test_client()
    response = redeem(voter, token)
    assert response.status_code == 302
    response = cast(voter, [p[0], p[1], p[2]], [t[0]])
    assert response.status_code == 302

    # La cookie anónima se eliminó; un segundo envío no crea otra papeleta.
    csrf = voter.get("/votar").get_data(as_text=True)
    token_csrf = re.search(r'name="csrf_token" value="([^"]+)"', csrf).group(1)
    response = voter.post(
        "/votar/papeleta",
        data={
            "csrf_token": token_csrf,
            "principal": [str(p[0]), str(p[1]), str(p[2])],
            "treasurer": [str(t[0])],
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/votar" in response.headers["Location"]

    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM ballot_sessions").fetchone()[0] == 1
        assert db.execute("SELECT used FROM voter_tokens").fetchone()[0] == 1

    # El mismo token tampoco se puede canjear desde otro navegador.
    other = app.test_client()
    response = redeem(other, token)
    assert response.status_code == 302
    assert "/votar" in response.headers["Location"]
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM ballot_sessions").fetchone()[0] == 1


def test_vote_after_close_is_rejected(app):
    data = make_department(app, "Cierre", principal_count=3, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    token = issue_token(app, data["department_id"])
    later_token = issue_token(app, data["department_id"])

    voter = app.test_client()
    assert redeem(voter, token).status_code == 302
    ballot_page = voter.get("/votar/papeleta")
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"', ballot_page.get_data(as_text=True)
    ).group(1)

    close(app, data["department_id"])

    response = voter.post(
        "/votar/papeleta",
        data={
            "csrf_token": csrf,
            "principal": [str(p[0]), str(p[1]), str(p[2])],
            "treasurer": [str(t[0])],
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM ballot_sessions WHERE consumed = 1"
        ).fetchone()[0] == 0

    # Tampoco se puede canjear un token emitido antes del cierre.
    later = app.test_client()
    response = redeem(later, later_token)
    assert response.status_code == 302
    assert "/votar" in response.headers["Location"]


def test_candidate_lists_are_scoped_to_department_and_round(app):
    a = make_department(app, "Departamento A", principal_count=3, treasurer_count=1, prefix="A")
    b = make_department(app, "Departamento B", principal_count=3, treasurer_count=1, prefix="B")

    with app.app_context():
        db = get_db()
        a_names = {
            row["full_name"]
            for row in db.execute(
                """
                SELECT c.full_name FROM candidate_departments cd
                JOIN candidates c ON c.id = cd.candidate_id
                WHERE cd.department_id = ?
                """,
                (a["department_id"],),
            ).fetchall()
        }
        b_names = {
            row["full_name"]
            for row in db.execute(
                """
                SELECT c.full_name FROM candidate_departments cd
                JOIN candidates c ON c.id = cd.candidate_id
                WHERE cd.department_id = ?
                """,
                (b["department_id"],),
            ).fetchall()
        }
        assert a_names.isdisjoint(b_names)

    # El candidato A no está inscrito en B: la papeleta de B lo rechaza.
    token = issue_token(app, b["department_id"])
    voter = app.test_client()
    assert redeem(voter, token).status_code == 302
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"',
        voter.get("/votar/papeleta").get_data(as_text=True),
    ).group(1)
    response = voter.post(
        "/votar/papeleta",
        data={
            "csrf_token": csrf,
            "principal": [str(a["principal"][0]), str(b["principal"][0]), str(b["principal"][1])],
            "treasurer": [str(b["treasurer"][0])],
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0

    # Un candidato no puede inscribirse en las dos rondas del mismo departamento.
    with app.app_context():
        with pytest.raises(ElectionError):
            enroll_candidate(
                a["principal"][0],
                a["department_id"],
                ROUND_TREASURER,
                actor="pytest",
            )


def test_open_requires_minimum_candidates(app):
    with app.app_context():
        from voting.elections import create_candidate, create_department

        department_id = create_department("Insuficiente", actor="pytest")
        for index in range(2):
            candidate_id = create_candidate(f"Poco {index}", actor="pytest")
            from voting.elections import enroll_candidate

            enroll_candidate(
                candidate_id, department_id, "principal", actor="pytest"
            )
        with pytest.raises(ElectionError):
            open_department(department_id, actor="pytest")


def test_receipt_verification_does_not_reveal_selections(app):
    data = make_department(app, "Comprobantes", principal_count=3, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    voter = vote(app, [p[0], p[1], p[2]], [t[0]])

    confirmation = voter.get("/votar/confirmacion")
    assert confirmation.status_code == 200
    receipts = re.findall(
        r'<code class="receipt-code">([^<]+)</code>',
        confirmation.get_data(as_text=True),
    )
    assert len(receipts) == 2

    verifier = app.test_client()
    csrf_page = verifier.get("/verificar").get_data(as_text=True)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', csrf_page).group(1)
    response = verifier.post(
        "/verificar",
        data={"csrf_token": csrf, "receipt": receipts[0]},
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Comprobante válido" in html
    assert "Comprobantes" in html
    # No se revela por quién votó.
    for candidate_id in p + t:
        with app.app_context():
            name = get_db().execute(
                "SELECT full_name FROM candidates WHERE id = ?", (candidate_id,)
            ).fetchone()["full_name"]
        assert name not in html
