"""Fábrica de la aplicación de votación anónima."""

from __future__ import annotations

import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, render_template, session
from werkzeug.security import generate_password_hash

from .blueprints.admin import admin_bp
from .blueprints.public import public_bp
from .blueprints.vote import vote_bp
from .constants import (
    DEPARTMENT_STATUS_LABELS,
    ROUND_LABELS,
    ROUND_SHORT_LABELS,
    VOTING_MODE_HELP,
    VOTING_MODE_LABELS,
)
from .db import get_db, init_app as init_db_app, init_db, utc_now
from .qr import qr_data_uri
from .security import init_csrf, init_security_headers


def _load_or_create_key(path: Path, env_name: str) -> str:
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        DATABASE=os.environ.get(
            "DATABASE", str(Path(app.instance_path) / "voting.db")
        ),
        SECRET_KEY=None,
        INTEGRITY_KEY=None,
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        BACKUP_DIR=os.environ.get(
            "BACKUP_DIR", str(Path(app.instance_path) / "backups")
        ),
        MAX_CONTENT_LENGTH=1_000_000,
        SESSION_COOKIE_NAME="iglesia_admin_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        VOTER_COOKIE_NAME="iglesia_voto",
        VOTER_SESSION_MAX_AGE=3600,
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_or_create_key(
            Path(app.instance_path) / ".secret_key", "SECRET_KEY"
        )
    if not app.config.get("INTEGRITY_KEY"):
        app.config["INTEGRITY_KEY"] = _load_or_create_key(
            Path(app.instance_path) / ".integrity_key", "INTEGRITY_KEY"
        )
    app.config["SESSION_COOKIE_SECURE"] = bool(
        os.environ.get("COOKIE_SECURE", "0") == "1"
        or app.config.get("SESSION_COOKIE_SECURE")
    )

    if os.environ.get("BEHIND_PROXY", "0") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    init_db_app(app)
    init_csrf(app)
    init_security_headers(app)

    app.register_blueprint(public_bp)
    app.register_blueprint(vote_bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_globals():
        return {
            "current_admin": session.get("admin_username"),
            "round_labels": ROUND_LABELS,
            "round_short_labels": ROUND_SHORT_LABELS,
            "department_status_labels": DEPARTMENT_STATUS_LABELS,
            "voting_mode_labels": VOTING_MODE_LABELS,
            "voting_mode_help": VOTING_MODE_HELP,
        }

    @app.template_filter("qr_data_uri")
    def qr_filter(value: str) -> str:
        return qr_data_uri(value)

    @app.template_filter("short_hash")
    def short_hash(value: str | None) -> str:
        if not value:
            return "—"
        return f"{value[:12]}…{value[-8:]}"

    @app.template_filter("friendly_datetime")
    def friendly_datetime(value: str | None) -> str:
        if not value:
            return "—"
        return value.replace("T", " ").replace("+00:00", " UTC")

    @app.errorhandler(400)
    def bad_request(error):
        return (
            render_template(
                "error.html",
                code=400,
                title="Solicitud inválida",
                message="La solicitud no pudo validarse. Recargue la página e intente de nuevo.",
            ),
            400,
        )

    @app.errorhandler(403)
    def forbidden(_error):
        return (
            render_template(
                "error.html",
                code=403,
                title="Acceso denegado",
                message="No tiene permisos para acceder a esta sección.",
            ),
            403,
        )

    @app.errorhandler(404)
    def not_found(_error):
        return (
            render_template(
                "error.html",
                code=404,
                title="No encontrado",
                message="La página solicitada no existe o todavía no está disponible.",
            ),
            404,
        )

    @app.errorhandler(413)
    def too_large(_error):
        return (
            render_template(
                "error.html",
                code=413,
                title="Solicitud demasiado grande",
                message="El formulario enviado supera el tamaño permitido.",
            ),
            413,
        )

    @app.errorhandler(500)
    def server_error(_error):
        return (
            render_template(
                "error.html",
                code=500,
                title="Error interno",
                message="Ocurrió un error inesperado. No se registró ningún voto parcial.",
            ),
            500,
        )

    @app.cli.command("init-db")
    def init_db_command():
        """Crea las tablas si no existen."""
        init_db()
        click.echo("Base de datos inicializada.")

    @app.cli.command("verify-all")
    def verify_all_command():
        """Recalcula la integridad de todas las actas y de la auditoría."""
        from .integrity import verify_audit_chain, verify_department_data

        db = get_db()
        audit = verify_audit_chain(db)
        click.echo(
            f"Auditoría: {'VÁLIDA' if audit['ok'] else 'CON ERRORES'} "
            f"({audit['entries']} acciones)"
        )
        all_ok = audit["ok"]
        departments = db.execute(
            "SELECT id, name, status FROM departments ORDER BY name COLLATE NOCASE"
        ).fetchall()
        if not departments:
            click.echo("No hay departamentos.")
        for department in departments:
            verification = verify_department_data(
                db,
                int(department["id"]),
                require_result=(department["status"] == "closed"),
            )
            status = "VÁLIDA" if verification["ok"] else "CON ERRORES"
            click.echo(f"- {department['name']} ({department['status']}): {status}")
            if not verification["ok"]:
                all_ok = False
                for check in verification["checks"]:
                    if not check["ok"]:
                        click.echo(f"    ✕ {check['name']}: {check['detail']}")
        if not all_ok:
            raise click.ClickException("La verificación encontró errores de integridad.")

    @app.cli.command("recount")
    @click.option("--department-id", type=int, required=True)
    def recount_command(department_id: int):
        """Recalcula y muestra el acta desde las papeletas almacenadas."""
        import json

        from .elections import get_department
        from .integrity import compute_result

        department = get_department(department_id)
        if department is None:
            raise click.ClickException("El departamento no existe.")
        if department["status"] != "closed":
            raise click.ClickException(
                "El acta solo se puede recalcular después del cierre."
            )
        result = compute_result(get_db(), department_id)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    @app.cli.command("create-admin")
    @click.option("--username", prompt="Usuario administrador")
    @click.password_option(confirmation_prompt=True)
    def create_admin_command(username: str, password: str):
        """Crea una cuenta de administrador."""
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}", username):
            raise click.ClickException(
                "El usuario debe tener entre 3 y 50 caracteres (letras, números, punto, guion o guion bajo)."
            )
        if len(password) < 12:
            raise click.ClickException("La contraseña debe tener al menos 12 caracteres.")
        db = get_db()
        if db.execute(
            "SELECT 1 FROM admins WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone():
            raise click.ClickException("Ese usuario ya existe.")
        db.execute(
            "INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), utc_now()),
        )
        click.echo(f"Administrador '{username}' creado.")

    return app
