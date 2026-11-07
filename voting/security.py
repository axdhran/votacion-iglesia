"""CSRF, sesiones, cabeceras y utilidades de acceso."""

from __future__ import annotations

import hmac
import secrets
from functools import wraps
from typing import Callable, TypeVar

from flask import (
    abort,
    current_app,
    redirect,
    request,
    session,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

F = TypeVar("F", bound=Callable)


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def rotate_csrf_token() -> str:
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return token


def init_csrf(app) -> None:
    @app.before_request
    def protect_from_csrf():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not sent or not expected or not hmac.compare_digest(str(sent), str(expected)):
            abort(400, description="Token CSRF inválido o ausente.")
        return None

    app.jinja_env.globals["csrf_token"] = csrf_token


def login_admin(admin_id: int, username: str) -> None:
    session.clear()
    session.permanent = True
    session["admin_id"] = int(admin_id)
    session["admin_username"] = username
    rotate_csrf_token()


def logout_admin() -> None:
    session.clear()


def admin_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def _voter_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="iglesia-voto-sesion-anonima-v1",
    )


def create_voter_cookie(session_id: str, department_id: int) -> str:
    return _voter_serializer().dumps(
        {"sid": session_id, "dept": int(department_id)}
    )


def read_voter_cookie() -> dict | None:
    raw = request.cookies.get(current_app.config["VOTER_COOKIE_NAME"])
    if not raw:
        return None
    try:
        data = _voter_serializer().loads(
            raw,
            max_age=int(current_app.config["VOTER_SESSION_MAX_AGE"]),
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "sid" not in data or "dept" not in data:
        return None
    return data


def set_voter_cookie(response, session_id: str, department_id: int):
    response.set_cookie(
        current_app.config["VOTER_COOKIE_NAME"],
        create_voter_cookie(session_id, department_id),
        max_age=int(current_app.config["VOTER_SESSION_MAX_AGE"]),
        httponly=True,
        samesite="Lax",
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", False)),
        path="/",
    )
    return response


def clear_voter_cookie(response):
    response.delete_cookie(
        current_app.config["VOTER_COOKIE_NAME"],
        path="/",
    )
    return response


def init_security_headers(app) -> None:
    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'",
        )
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        endpoint = request.endpoint or ""
        if endpoint.startswith(("admin.", "vote.", "public.verify")):
            response.headers["Cache-Control"] = "no-store"
        return response
