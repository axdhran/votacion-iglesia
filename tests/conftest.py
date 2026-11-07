from __future__ import annotations

import re

import pytest
from werkzeug.security import generate_password_hash

from voting import create_app
from voting.constants import (
    ROUND_PRINCIPAL,
    ROUND_TREASURER,
    VOTING_MODE_TOKEN,
)
from voting.db import get_db, utc_now
from voting.elections import (
    close_department,
    create_candidate,
    create_department,
    enroll_candidate,
    generate_tokens,
    open_department,
)

ADMIN_USER = "admin"
ADMIN_PASSWORD = "ClaveSeguraDePrueba123!"

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def app(tmp_path):
    database = tmp_path / "voting-test.db"
    application = create_app(
        {
            "TESTING": True,
            "DATABASE": str(database),
            "SECRET_KEY": "s" * 64,
            "INTEGRITY_KEY": "i" * 64,
            "SESSION_COOKIE_SECURE": False,
            "VOTER_SESSION_MAX_AGE": 3600,
            "BACKUP_DIR": str(tmp_path / "backups"),
        }
    )
    with application.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
            (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD), utc_now()),
        )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    login(client)
    return client


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "No se encontró el token CSRF en el HTML"
    return match.group(1)


def get_csrf(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, f"{path} devolvió {response.status_code}"
    return csrf_from(response.get_data(as_text=True))


def login(client, username: str = ADMIN_USER, password: str = ADMIN_PASSWORD) -> None:
    token = get_csrf(client, "/admin/login")
    response = client.post(
        "/admin/login",
        data={"csrf_token": token, "username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")


def make_department(
    app,
    name: str = "Jóvenes",
    *,
    principal_count: int = 5,
    treasurer_count: int = 2,
    prefix: str = "C",
    voting_mode: str = VOTING_MODE_TOKEN,
) -> dict:
    with app.app_context():
        department_id = create_department(
            name, actor="pytest", voting_mode=voting_mode
        )
        principal_ids = []
        treasurer_ids = []
        for index in range(principal_count):
            candidate_id = create_candidate(
                f"{prefix} Principal {index + 1}", actor="pytest"
            )
            enroll_candidate(
                candidate_id, department_id, ROUND_PRINCIPAL, actor="pytest"
            )
            principal_ids.append(candidate_id)
        for index in range(treasurer_count):
            candidate_id = create_candidate(
                f"{prefix} Tesorero {index + 1}", actor="pytest"
            )
            enroll_candidate(
                candidate_id, department_id, ROUND_TREASURER, actor="pytest"
            )
            treasurer_ids.append(candidate_id)
        open_department(department_id, actor="pytest")
        return {
            "department_id": department_id,
            "principal": principal_ids,
            "treasurer": treasurer_ids,
        }


def issue_token(app, department_id: int) -> str:
    with app.app_context():
        return generate_tokens(department_id, 1, actor="pytest")[0]


def redeem(client, token: str):
    csrf = get_csrf(client, "/votar")
    return client.post(
        "/votar",
        data={"csrf_token": csrf, "token": token},
        follow_redirects=False,
    )


def cast(client, principal_ids: list[int], treasurer_ids: list[int]):
    csrf = get_csrf(client, "/votar/papeleta")
    return client.post(
        "/votar/papeleta",
        data={
            "csrf_token": csrf,
            "principal": [str(cid) for cid in principal_ids],
            "treasurer": [str(cid) for cid in treasurer_ids],
        },
        follow_redirects=False,
    )


def vote(app, principal_ids: list[int], treasurer_ids: list[int], *, token: str | None = None):
    department_id = None
    with app.app_context():
        # El departamento se infiere de la inscripción de los candidatos.
        row = get_db().execute(
            "SELECT department_id FROM candidate_departments WHERE candidate_id = ?",
            (principal_ids[0],),
        ).fetchone()
        department_id = int(row["department_id"])
    token = token or issue_token(app, department_id)
    voter = app.test_client()
    response = redeem(voter, token)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/votar/papeleta")
    response = cast(voter, principal_ids, treasurer_ids)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/votar/confirmacion")
    return voter


def close(app, department_id: int):
    with app.app_context():
        return close_department(department_id, actor="pytest")


# ---------------------------------------------------------------------------
# Ayudas para el modo mesa sin código
# ---------------------------------------------------------------------------
def start_kiosk(client, department_id: int):
    """Inicia una sesión anónima de mesa desde la pantalla compartida."""
    # El token CSRF es de la sesión, no del formulario; /votar siempre lo incluye.
    csrf = get_csrf(client, "/votar")
    return client.post(
        "/votar/mesa/iniciar",
        data={"csrf_token": csrf, "department_id": str(department_id)},
        follow_redirects=False,
    )


def vote_kiosk(
    app,
    department_id: int,
    principal_ids: list[int],
    treasurer_ids: list[int],
    *,
    client=None,
):
    """Recorre el flujo completo de mesa: iniciar, revisar y confirmar."""
    voter = client or app.test_client()
    response = start_kiosk(voter, department_id)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/votar/papeleta")

    review = voter.post(
        "/votar/revisar",
        data={
            "csrf_token": get_csrf(voter, "/votar/papeleta"),
            "principal": [str(cid) for cid in principal_ids],
            "treasurer": [str(cid) for cid in treasurer_ids],
        },
        follow_redirects=False,
    )
    assert review.status_code == 200

    response = cast(voter, principal_ids, treasurer_ids)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/votar/confirmacion")
    return voter
