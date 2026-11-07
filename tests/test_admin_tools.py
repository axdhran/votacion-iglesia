"""Pruebas de los filtros de candidatos y del reinicio de la base."""

from __future__ import annotations

from pathlib import Path

from voting.db import get_db
from voting.elections import create_candidate, create_department, get_department
from voting.integrity import verify_audit_chain

from .conftest import get_csrf, make_department, vote


def test_candidates_filter_by_department_round_and_status(app, admin_client):
    jovenes = make_department(
        app,
        "Jóvenes",
        principal_count=3,
        treasurer_count=1,
        prefix="J",
    )
    misiones = make_department(
        app,
        "Misiones",
        principal_count=3,
        treasurer_count=1,
        prefix="M",
    )
    with app.app_context():
        create_candidate("Persona Sin Inscripción", actor="pytest")

    base = "/admin/candidates"

    # Filtro por departamento: solo aparecen sus candidatos.
    html = admin_client.get(
        f"{base}?department_id={jovenes['department_id']}"
    ).get_data(as_text=True)
    assert "J Principal 1" in html
    assert "M Principal 1" not in html

    # Filtro por ronda: solo los candidatos de esa ronda.
    html = admin_client.get(f"{base}?round=treasurer").get_data(as_text=True)
    assert "J Tesorero 1" in html
    assert "M Tesorero 1" in html
    assert "J Principal 1" not in html

    # Departamento + ronda: deben coincidir en la misma inscripción.
    html = admin_client.get(
        f"{base}?department_id={jovenes['department_id']}&round=treasurer"
    ).get_data(as_text=True)
    assert "J Tesorero 1" in html
    assert "J Principal 1" not in html
    assert "M Tesorero 1" not in html

    # Búsqueda por nombre.
    html = admin_client.get(f"{base}?q=M+Principal+2").get_data(as_text=True)
    assert "M Principal 2" in html
    assert "M Principal 1" not in html

    # Estado: solo los que no tienen ninguna inscripción.
    html = admin_client.get(f"{base}?status=unassigned").get_data(as_text=True)
    assert "Persona Sin Inscripción" in html
    assert "J Principal 1" not in html

    # Estado: solo los que tienen al menos una inscripción.
    html = admin_client.get(f"{base}?status=enrolled").get_data(as_text=True)
    assert "J Principal 1" in html
    assert "Persona Sin Inscripción" not in html

    # El formulario de inscripción no debe perder candidatos por el filtro.
    # Se crea un departamento en preparación para que el formulario aparezca.
    with app.app_context():
        create_department("Borrador para inscribir", actor="pytest")
    html = admin_client.get(
        f"{base}?department_id={jovenes['department_id']}"
    ).get_data(as_text=True)
    assert "Persona Sin Inscripción" in html


def test_candidates_filter_combination_without_matches(app, admin_client):
    make_department(app, "Jóvenes", principal_count=3, treasurer_count=1, prefix="J")
    make_department(app, "Misiones", principal_count=3, treasurer_count=1, prefix="M")
    with app.app_context():
        create_candidate("Persona Sin Inscripción", actor="pytest")

    html = admin_client.get(
        "/admin/candidates?q=NoExiste&round=principal"
    ).get_data(as_text=True)
    assert "No hay candidatos que coincidan con los filtros." in html


def test_maintenance_page_shows_counts_and_backups(app, admin_client):
    make_department(app, "Mantenimiento", principal_count=3, treasurer_count=1)
    response = admin_client.get("/admin/maintenance")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Reiniciar la base de datos" in html
    assert "Departamentos" in html
    assert "Candidatos" in html


def test_reset_requires_explicit_confirmation(app, admin_client):
    make_department(app, "No Borrar", principal_count=3, treasurer_count=1)
    csrf = get_csrf(admin_client, "/admin/maintenance")

    # Frase incorrecta: no se borra nada.
    response = admin_client.post(
        "/admin/maintenance/reset",
        data={"csrf_token": csrf, "confirm": "borrar", "understand": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 1

    # Frase correcta pero sin la casilla: tampoco se borra.
    response = admin_client.post(
        "/admin/maintenance/reset",
        data={"csrf_token": csrf, "confirm": "REINICIAR"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 1


def test_reset_clears_election_data_keeps_admin_and_creates_backup(app, admin_client):
    data = make_department(
        app,
        "Con Votos",
        principal_count=3,
        treasurer_count=1,
    )
    # Genera papeletas y sesiones para comprobar que también se borran.
    vote(app, data["principal"], [data["treasurer"][0]])

    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == 2

    csrf = get_csrf(admin_client, "/admin/maintenance")
    response = admin_client.post(
        "/admin/maintenance/reset",
        data={"csrf_token": csrf, "confirm": "reiniciar", "understand": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Datos electorales reiniciados" in response.get_data(as_text=True)

    with app.app_context():
        db = get_db()
        for table in (
            "departments",
            "candidates",
            "candidate_departments",
            "voter_tokens",
            "ballot_sessions",
            "ballots",
            "ballot_selections",
            "results",
        ):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

        # El administrador sigue existiendo.
        assert db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1

        # La auditoría anterior se limpió y el reinicio quedó registrado.
        entries = db.execute("SELECT action, details FROM audit_log").fetchall()
        assert len(entries) == 1
        assert entries[0]["action"] == "database.reset"
        assert "admins_preserved" in entries[0]["details"]
        chain = verify_audit_chain(db)
        assert chain["ok"], chain

    # Se creó el respaldo automático.
    backups = list(Path(app.config["BACKUP_DIR"]).glob("voting-antes-de-reiniciar-*.db"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0

    # El esquema y los triggers quedaron operativos para empezar de nuevo.
    with app.app_context():
        new_id = create_department("Después del reinicio", actor="pytest")
        assert get_department(new_id)["status"] == "draft"
