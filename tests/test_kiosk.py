"""Pruebas del modo mesa sin código (kiosco) y de la migración de esquema."""

from __future__ import annotations

import sqlite3

import pytest

from voting import create_app
from voting.ballots import VoteError, start_kiosk_session
from voting.constants import VOTING_MODE_KIOSK, VOTING_MODE_TOKEN
from voting.db import get_db
from voting.elections import (
    ElectionError,
    generate_tokens,
    list_kiosk_departments,
    update_department_mode,
)

from .conftest import (
    get_csrf,
    make_department,
    start_kiosk,
    vote_kiosk,
)


def test_kiosk_vote_saves_both_rounds_and_auto_reset(app):
    """Un votante de mesa completa el flujo y la pantalla queda en auto-reinicio."""
    data = make_department(
        app,
        "Mesa Jóvenes",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_KIOSK,
    )
    p = data["principal"]
    t = data["treasurer"]

    voter = vote_kiosk(app, data["department_id"], [p[0], p[1], p[2]], [t[0]])

    # La confirmación incluye el reinicio automático hacia la pantalla de mesa.
    confirmation = voter.get("/votar/confirmacion")
    assert confirmation.status_code == 200
    html = confirmation.get_data(as_text=True)
    assert 'data-auto-reset="' in html
    assert "/votar/mesa" in html
    assert "Siguiente persona" in html

    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT COUNT(*) FROM ballot_sessions WHERE consumed = 1"
            ).fetchone()[0]
            == 1
        )


def test_kiosk_reset_discards_unused_session(app):
    """Reiniciar la pantalla borra la sesión a medias y no guarda votos."""
    data = make_department(
        app,
        "Mesa Reinicio",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_KIOSK,
    )
    voter = app.test_client()
    response = start_kiosk(voter, data["department_id"])
    assert response.status_code == 302

    with app.app_context():
        assert (
            get_db()
            .execute("SELECT COUNT(*) FROM ballot_sessions WHERE consumed = 0")
            .fetchone()[0]
            == 1
        )

    csrf = get_csrf(voter, "/votar/papeleta")
    response = voter.post(
        "/votar/mesa/reiniciar",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/votar/mesa")

    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM ballot_sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 0


def test_kiosk_does_not_need_or_allow_tokens(app):
    """El modo mesa rechaza la generación de tokens y no los necesita para votar."""
    data = make_department(
        app,
        "Mesa Sin Tokens",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_KIOSK,
    )
    with app.app_context():
        with pytest.raises(ElectionError):
            generate_tokens(data["department_id"], 1, actor="pytest")


def test_token_department_cannot_start_kiosk_session(app):
    """Un departamento con código personal no puede abrir una sesión de mesa."""
    data = make_department(
        app,
        "Solo Código",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_TOKEN,
    )
    voter = app.test_client()
    response = start_kiosk(voter, data["department_id"])
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/votar/mesa")

    with app.app_context():
        with pytest.raises(VoteError):
            start_kiosk_session(data["department_id"])
        assert get_db().execute("SELECT COUNT(*) FROM ballot_sessions").fetchone()[0] == 0


def test_list_kiosk_departments_only_open_kiosk(app):
    """El listado de mesa incluye solo departamentos abiertos en modo mesa."""
    make_department(
        app,
        "Mesa Visible",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_KIOSK,
    )
    make_department(
        app,
        "Código Oculto",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_TOKEN,
    )
    with app.app_context():
        from voting.elections import create_department

        # Este queda en preparación: no debe aparecer aunque sea modo mesa.
        create_department("Mesa Borrador", actor="pytest", voting_mode=VOTING_MODE_KIOSK)

        listed = {row["name"] for row in list_kiosk_departments()}
    assert "Mesa Visible" in listed
    assert "Código Oculto" not in listed
    assert "Mesa Borrador" not in listed


def test_mode_change_only_allowed_in_draft(app):
    """El modo se puede cambiar en preparación, pero no con la votación abierta."""
    with app.app_context():
        from voting.elections import create_department

        department_id = create_department("Cambio de Modo", actor="pytest")
        update_department_mode(department_id, VOTING_MODE_KIOSK, actor="pytest")
        assert (
            get_db()
            .execute(
                "SELECT voting_mode FROM departments WHERE id = ?", (department_id,)
            )
            .fetchone()["voting_mode"]
            == VOTING_MODE_KIOSK
        )

    data = make_department(
        app,
        "Modo Bloqueado",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_TOKEN,
    )
    with app.app_context():
        with pytest.raises(ElectionError):
            update_department_mode(
                data["department_id"], VOTING_MODE_KIOSK, actor="pytest"
            )
        # El trigger de SQLite también impide el cambio directo.
        with pytest.raises(sqlite3.IntegrityError):
            get_db().execute(
                "UPDATE departments SET voting_mode = ? WHERE id = ?",
                (VOTING_MODE_KIOSK, data["department_id"]),
            )


def test_migration_adds_voting_mode_without_touching_closed_department(tmp_path):
    """Una base antigua recibe la columna nueva y conserva sus datos."""
    database = tmp_path / "antigua.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE departments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
            status     TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'open', 'closed')),
            created_at TEXT NOT NULL,
            opened_at  TEXT,
            closed_at  TEXT,
            CHECK (length(trim(name)) BETWEEN 2 AND 80)
        );
        INSERT INTO departments (name, status, created_at)
        VALUES ('Departamento Antiguo', 'closed', '2024-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    application = create_app(
        {
            "TESTING": True,
            "DATABASE": str(database),
            "SECRET_KEY": "s" * 64,
            "INTEGRITY_KEY": "i" * 64,
        }
    )
    with application.app_context():
        columns = {
            row["name"] for row in get_db().execute("PRAGMA table_info(departments)")
        }
        assert "voting_mode" in columns
        row = get_db().execute(
            "SELECT name, status, voting_mode FROM departments"
        ).fetchone()
        assert row["name"] == "Departamento Antiguo"
        assert row["status"] == "closed"
        assert row["voting_mode"] == VOTING_MODE_TOKEN


def test_new_screens_render(app, admin_client):
    """Las pantallas nuevas y modificadas se renderizan sin errores."""
    kiosk = make_department(
        app,
        "Mesa Render",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_KIOSK,
    )
    token = make_department(
        app,
        "Código Render",
        principal_count=3,
        treasurer_count=1,
        voting_mode=VOTING_MODE_TOKEN,
    )

    # Pantallas públicas y de votante.
    index = admin_client.get("/")
    assert index.status_code == 200
    assert "Abrir mesa de votación" in index.get_data(as_text=True)

    kiosk_page = admin_client.get("/votar/mesa")
    assert kiosk_page.status_code == 200
    assert "Mesa Render" in kiosk_page.get_data(as_text=True)

    enter_page = admin_client.get("/votar")
    assert enter_page.status_code == 200
    assert "Ir a la mesa de votación" in enter_page.get_data(as_text=True)

    quick = admin_client.get("/votar/acceso")
    assert quick.status_code == 200
    assert "Abriendo su papeleta" in quick.get_data(as_text=True)

    # Administración: selector de modo, ayuda de tokens y bloqueo en mesa.
    departments = admin_client.get("/admin/departments")
    assert departments.status_code == 200
    html = departments.get_data(as_text=True)
    assert "Mesa sin código" in html
    assert "Código personal" in html

    kiosk_tokens = admin_client.get(
        f"/admin/departments/{kiosk['department_id']}/tokens"
    )
    assert kiosk_tokens.status_code == 200
    assert "No necesita generar tokens" in kiosk_tokens.get_data(as_text=True)

    token_tokens = admin_client.get(
        f"/admin/departments/{token['department_id']}/tokens"
    )
    assert token_tokens.status_code == 200
    assert "Generar tokens" in token_tokens.get_data(as_text=True)
