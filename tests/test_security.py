from __future__ import annotations

import re

import pytest

from voting.ballots import VoteError, submit_vote
from voting.db import get_db
from voting.elections import (
    ElectionError,
    create_candidate,
    create_department,
    get_stored_result,
    list_audit_entries,
)
from voting.integrity import verify_audit_chain

from .conftest import cast, close, csrf_from, get_csrf, issue_token, make_department, redeem, vote


def test_identity_and_vote_are_stored_separately(app):
    data = make_department(app, "Separación", principal_count=3, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    voter = vote(app, [p[0], p[1], p[2]], [t[0]])

    with app.app_context():
        db = get_db()
        token = db.execute("SELECT * FROM voter_tokens").fetchone()
        session = db.execute("SELECT * FROM ballot_sessions").fetchone()
        ballots = db.execute("SELECT * FROM ballots ORDER BY id").fetchall()
        assert token["used"] == 1
        assert session["consumed"] == 1
        assert len(ballots) == 2

        token_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(voter_tokens)")
        }
        session_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(ballot_sessions)")
        }
        ballot_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(ballots)")
        }
        assert not {
            "session_id",
            "ballot_id",
            "voter_name",
            "voter_email",
            "candidate_id",
        } & token_columns
        assert "token_id" not in session_columns
        assert "voter_id" not in session_columns
        assert not {
            "voter_id",
            "voter_name",
            "voter_email",
            "token_hash",
        } & ballot_columns

        # No existe ninguna referencia cruzada entre el token y la sesión.
        assert token["token_hash"] not in {
            session["id"],
            *(b["session_id"] for b in ballots),
            *(b["receipt_hash"] for b in ballots),
        }
        assert session["id"] not in {
            token["token_hash"],
            *(b["receipt_hash"] for b in ballots),
        }

        audit = db.execute("SELECT action, details FROM audit_log").fetchall()
        for row in audit:
            assert "selections" not in row["details"]
            assert "receipt_hash" not in row["details"]
            assert "session_id" not in row["details"]
            assert "vote" not in row["action"].lower()

    # El comprobante se puede verificar, pero no revela las selecciones.
    confirmation = voter.get("/votar/confirmacion").get_data(as_text=True)
    receipt = re.search(r'<code class="receipt-code">([^<]+)</code>', confirmation).group(1)
    verifier = app.test_client()
    token_csrf = get_csrf(verifier, "/verificar")
    response = verifier.post(
        "/verificar",
        data={"csrf_token": token_csrf, "receipt": receipt},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert "Comprobante válido" in html
    with app.app_context():
        names = [
            row["full_name"]
            for row in get_db().execute("SELECT full_name FROM candidates").fetchall()
        ]
    for name in names:
        assert name not in html


def test_csrf_is_required_on_every_post(app, admin_client):
    anonymous = app.test_client()
    response = anonymous.post(
        "/admin/login",
        data={"username": "admin", "password": "ClaveSeguraDePrueba123!"},
    )
    assert response.status_code == 400

    response = anonymous.post("/votar", data={"token": "x" * 40})
    assert response.status_code == 400

    # Las rutas del modo mesa también exigen CSRF.
    response = anonymous.post("/votar/mesa/iniciar", data={"department_id": "1"})
    assert response.status_code == 400
    response = anonymous.post("/votar/mesa/reiniciar")
    assert response.status_code == 400

    data = make_department(app, "CSRF", principal_count=3, treasurer_count=1)
    response = admin_client.post(f"/admin/departments/{data['department_id']}/close")
    assert response.status_code == 400

    # Con token CSRF la acción sí llega al servicio.
    csrf = get_csrf(admin_client, "/admin/departments")
    response = admin_client.post(
        f"/admin/departments/{data['department_id']}/close",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert response.status_code == 200


def test_admin_flow_and_audit(app, admin_client):
    csrf = get_csrf(admin_client, "/admin/departments")
    response = admin_client.post(
        "/admin/departments",
        data={"csrf_token": csrf, "name": "Misiones"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        department_id = get_db().execute(
            "SELECT id FROM departments WHERE name = 'Misiones'"
        ).fetchone()["id"]

    candidate_ids = {"principal": [], "treasurer": []}
    for index in range(3):
        response = admin_client.post(
            "/admin/candidates",
            data={"csrf_token": csrf, "full_name": f"Misionero {index + 1}"},
            follow_redirects=True,
        )
        assert response.status_code == 200
    for index in range(1):
        admin_client.post(
            "/admin/candidates",
            data={"csrf_token": csrf, "full_name": f"Tesorero Misiones {index + 1}"},
            follow_redirects=True,
        )
    with app.app_context():
        rows = get_db().execute(
            "SELECT id, full_name FROM candidates ORDER BY id"
        ).fetchall()
        for row in rows:
            if row["full_name"].startswith("Tesorero"):
                candidate_ids["treasurer"].append(row["id"])
            else:
                candidate_ids["principal"].append(row["id"])

    for candidate_id in candidate_ids["principal"]:
        admin_client.post(
            "/admin/candidates/enroll",
            data={
                "csrf_token": csrf,
                "candidate_id": candidate_id,
                "department_id": department_id,
                "round": "principal",
            },
            follow_redirects=True,
        )
    for candidate_id in candidate_ids["treasurer"]:
        admin_client.post(
            "/admin/candidates/enroll",
            data={
                "csrf_token": csrf,
                "candidate_id": candidate_id,
                "department_id": department_id,
                "round": "treasurer",
            },
            follow_redirects=True,
        )

    response = admin_client.post(
        f"/admin/departments/{department_id}/tokens",
        data={"csrf_token": csrf, "count": "2"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "tokens generados" in response.get_data(as_text=True).lower()

    response = admin_client.post(
        f"/admin/departments/{department_id}/open",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert get_db().execute(
            "SELECT status FROM departments WHERE id = ?", (department_id,)
        ).fetchone()["status"] == "open"

    # Antes del cierre no se muestra el acta ni los ganadores.
    before = admin_client.get(f"/admin/departments/{department_id}/results")
    assert before.status_code == 200
    assert "Acta no disponible" in before.get_data(as_text=True)
    assert "Presidente" not in before.get_data(as_text=True)

    response = admin_client.post(
        f"/admin/departments/{department_id}/close",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert response.status_code == 200
    after = admin_client.get(f"/admin/departments/{department_id}/results")
    assert after.status_code == 200
    assert "Acta certificada" in after.get_data(as_text=True)

    with app.app_context():
        db = get_db()
        actions = {row["action"] for row in list_audit_entries(1000)}
        assert {
            "department.create",
            "candidate.create",
            "candidate.enroll",
            "tokens.generate",
            "department.open",
            "department.close",
        } <= actions
        assert verify_audit_chain(db)["ok"]
        assert get_stored_result(department_id) is not None


def test_admin_authentication_and_headers(app):
    client = app.test_client()
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/admin/login" in response.headers["Location"]

    token = get_csrf(client, "/admin/login")
    response = client.post(
        "/admin/login",
        data={"csrf_token": token, "username": "admin", "password": "incorrecta"},
    )
    assert response.status_code == 200
    assert "incorrectos" in response.get_data(as_text=True)

    home = client.get("/")
    assert home.status_code == 200
    assert "default-src 'self'" in home.headers["Content-Security-Policy"]
    assert home.headers["X-Frame-Options"] == "DENY"
    assert home.headers["X-Content-Type-Options"] == "nosniff"


def test_duplicate_and_invalid_selections_are_rejected(app):
    data = make_department(app, "Validación", principal_count=4, treasurer_count=1)
    p = data["principal"]
    t = data["treasurer"]
    token = issue_token(app, data["department_id"])
    voter = app.test_client()
    assert redeem(voter, token).status_code == 302
    csrf = get_csrf(voter, "/votar/papeleta")

    response = voter.post(
        "/votar/papeleta",
        data={
            "csrf_token": csrf,
            "principal": [str(p[0]), str(p[0]), str(p[1])],
            "treasurer": [str(t[0])],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "dos veces por el mismo candidato" in response.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0


def test_business_validation(app):
    with app.app_context():
        with pytest.raises(ElectionError):
            create_department(" ", actor="pytest")
        with pytest.raises(ElectionError):
            create_candidate("A", actor="pytest")
        department_id = create_department("Validación de tokens", actor="pytest")
        from voting.elections import generate_tokens

        with pytest.raises(ElectionError):
            generate_tokens(department_id, 0, actor="pytest")
