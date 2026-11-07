"""Panel de administración."""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from ..audit import log_action
from ..constants import (
    DEPARTMENT_CLOSED,
    ROUND_LABELS,
    ROUNDS,
    VOTING_MODES,
)
from ..db import get_db, transaction
from ..elections import (
    ElectionError,
    build_winners,
    close_department,
    create_candidate,
    create_department,
    enroll_candidate,
    generate_tokens,
    get_candidate,
    get_department,
    get_stored_result,
    list_audit_entries,
    list_candidates_with_enrollments,
    list_departments_with_stats,
    open_department,
    token_stats,
    update_department_mode,
)
from ..integrity import verify_audit_chain, verify_department_data
from ..maintenance import TABLE_LABELS, data_counts, list_backups, reset_election_data
from ..qr import qr_data_uri
from ..security import login_admin, logout_admin

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def require_admin():
    if request.endpoint == "admin.login":
        return None
    if not session.get("admin_id"):
        return redirect(url_for("admin.login"))
    return None


def _actor() -> str:
    return str(session.get("admin_username", "admin"))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_id"):
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        admin = get_db().execute(
            "SELECT * FROM admins WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if admin is not None and check_password_hash(admin["password_hash"], password):
            with transaction():
                log_action(
                    get_db(),
                    actor=admin["username"],
                    action="admin.login",
                    entity_type="admin",
                    entity_id=admin["id"],
                )
            login_admin(int(admin["id"]), admin["username"])
            return redirect(url_for("admin.dashboard"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("admin/login.html")


@admin_bp.post("/logout")
def logout():
    actor = _actor()
    with transaction():
        log_action(
            get_db(),
            actor=actor,
            action="admin.logout",
            entity_type="admin",
            entity_id=session.get("admin_id"),
        )
    logout_admin()
    flash("Sesión administrativa cerrada.", "success")
    return redirect(url_for("public.index"))


@admin_bp.get("")
@admin_bp.get("/")
def dashboard():
    departments = list_departments_with_stats()
    audit_chain = verify_audit_chain(get_db())
    return render_template(
        "admin/dashboard.html",
        departments=departments,
        audit_chain=audit_chain,
    )


@admin_bp.route("/departments", methods=["GET", "POST"])
def departments():
    if request.method == "POST":
        try:
            create_department(
                request.form.get("name", ""),
                actor=_actor(),
                voting_mode=request.form.get("voting_mode", "token"),
            )
        except ElectionError as exc:
            flash(str(exc), "error")
        else:
            flash("Departamento creado en estado de preparación.", "success")
        return redirect(url_for("admin.departments"))
    return render_template(
        "admin/departments.html",
        departments=list_departments_with_stats(),
        voting_modes=VOTING_MODES,
    )


@admin_bp.post("/departments/<int:department_id>/mode")
def department_mode(department_id: int):
    """Cambia entre código personal y mesa compartida antes de abrir."""
    try:
        update_department_mode(
            department_id,
            request.form.get("voting_mode", ""),
            actor=_actor(),
        )
    except ElectionError as exc:
        flash(str(exc), "error")
    else:
        flash("Modo de votación actualizado.", "success")
    return redirect(url_for("admin.departments"))


@admin_bp.post("/departments/<int:department_id>/open")
def department_open(department_id: int):
    try:
        open_department(department_id, actor=_actor())
    except ElectionError as exc:
        flash(str(exc), "error")
    else:
        flash("Votación abierta. Los resultados permanecerán ocultos hasta el cierre.", "success")
    return redirect(url_for("admin.departments"))


@admin_bp.post("/departments/<int:department_id>/close")
def department_close(department_id: int):
    try:
        result = close_department(department_id, actor=_actor())
    except ElectionError as exc:
        flash(str(exc), "error")
    else:
        flash(
            f"Votación cerrada y acta certificada con {result['participation_count']} participaciones.",
            "success",
        )
    return redirect(url_for("admin.departments"))


@admin_bp.route("/departments/<int:department_id>/tokens", methods=["GET", "POST"])
def tokens(department_id: int):
    department = get_department(department_id)
    if department is None:
        abort(404)
    generated: list[str] = []
    if request.method == "POST":
        try:
            count = int(request.form.get("count", "0"))
        except ValueError:
            count = 0
        try:
            generated = generate_tokens(department_id, count, actor=_actor())
        except ElectionError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.tokens", department_id=department_id))
        flash(
            f"Se generaron {len(generated)} tokens. Cópielos o imprímalos ahora: no se vuelven a mostrar.",
            "success",
        )
    public_base_url = (
        current_app.config.get("PUBLIC_BASE_URL") or request.url_root.rstrip("/")
    )
    cards = []
    for token in generated:
        vote_url = f"{public_base_url}/votar/acceso#{token}"
        cards.append(
            {
                "token": token,
                "vote_url": vote_url,
                "qr": qr_data_uri(vote_url),
            }
        )
    qr_public = not any(
        host in public_base_url for host in ("localhost", "127.0.0.1", "0.0.0.0")
    )
    return render_template(
        "admin/tokens.html",
        department=department,
        stats=token_stats(department_id),
        generated=generated,
        cards=cards,
        public_base_url=public_base_url,
        public_base_configured=bool(current_app.config.get("PUBLIC_BASE_URL")),
        qr_public=qr_public,
    )


@admin_bp.get("/departments/<int:department_id>/results")
def results(department_id: int):
    department = get_department(department_id)
    if department is None:
        abort(404)
    result = get_stored_result(department_id)
    if result is None:
        return render_template(
            "admin/results.html",
            department=department,
            result=None,
            winners=None,
            verification=None,
        )
    verification = verify_department_data(get_db(), department_id)
    return render_template(
        "admin/results.html",
        department=department,
        result=result,
        winners=build_winners(result),
        verification=verification,
    )


@admin_bp.get("/departments/<int:department_id>/integrity")
def integrity(department_id: int):
    department = get_department(department_id)
    if department is None:
        abort(404)
    verification = verify_department_data(
        get_db(),
        department_id,
        require_result=(department["status"] == DEPARTMENT_CLOSED),
    )
    return render_template(
        "admin/integrity.html",
        department=department,
        verification=verification,
    )


@admin_bp.route("/candidates", methods=["GET", "POST"])
def candidates():
    if request.method == "POST":
        try:
            create_candidate(request.form.get("full_name", ""), actor=_actor())
        except ElectionError as exc:
            flash(str(exc), "error")
        else:
            flash("Candidato registrado.", "success")
        return redirect(url_for("admin.candidates"))

    # Filtros del listado. Se leen de la URL para poder compartir o recargar
    # la página sin perder la selección.
    department_id: int | None = None
    raw_department = request.args.get("department_id", "").strip()
    if raw_department:
        try:
            department_id = int(raw_department)
        except ValueError:
            department_id = None

    round_name = request.args.get("round", "").strip() or None
    if round_name not in ROUNDS:
        round_name = None

    query = request.args.get("q", "").strip() or None

    status = request.args.get("status", "").strip() or None
    if status not in {"enrolled", "unassigned"}:
        status = None

    all_candidates = list_candidates_with_enrollments()
    filtered_candidates = list_candidates_with_enrollments(
        department_id=department_id,
        round_name=round_name,
        query=query,
        status=status,
    )
    has_filters = bool(department_id or round_name or query or status)
    return render_template(
        "admin/candidates.html",
        candidates=filtered_candidates,
        all_candidates=all_candidates,
        total_candidates=len(all_candidates),
        departments=list_departments_with_stats(),
        rounds=ROUNDS,
        round_labels=ROUND_LABELS,
        filters={
            "department_id": department_id,
            "round": round_name,
            "q": query or "",
            "status": status,
        },
        has_filters=has_filters,
    )


@admin_bp.post("/candidates/enroll")
def enroll():
    try:
        candidate_id = int(request.form.get("candidate_id", "0"))
        department_id = int(request.form.get("department_id", "0"))
    except ValueError:
        flash("Selección inválida.", "error")
        return redirect(url_for("admin.candidates"))
    round_name = request.form.get("round", "")
    try:
        enroll_candidate(
            candidate_id,
            department_id,
            round_name,
            actor=_actor(),
        )
    except ElectionError as exc:
        flash(str(exc), "error")
    else:
        candidate = get_candidate(candidate_id)
        flash(
            f"{candidate['full_name'] if candidate else 'Candidato'} inscrito correctamente.",
            "success",
        )
    return redirect(url_for("admin.candidates"))


@admin_bp.get("/audit")
def audit():
    return render_template(
        "admin/audit.html",
        entries=list_audit_entries(),
        chain=verify_audit_chain(get_db()),
    )


@admin_bp.get("/maintenance")
def maintenance():
    """Página de mantenimiento con conteos, respaldos y reinicio de datos."""
    return render_template(
        "admin/maintenance.html",
        counts=data_counts(),
        table_labels=TABLE_LABELS,
        backups=list_backups(),
    )


@admin_bp.post("/maintenance/reset")
def maintenance_reset():
    """Reinicia los datos electorales después de una confirmación explícita."""
    confirmation = (request.form.get("confirm", "") or "").strip().upper()
    understood = request.form.get("understand")
    if confirmation != "REINICIAR" or not understood:
        flash(
            "Para reiniciar debe escribir REINICIAR y marcar la casilla de confirmación.",
            "error",
        )
        return redirect(url_for("admin.maintenance"))

    result = reset_election_data(actor=_actor())
    flash(
        "Datos electorales reiniciados. Se conservaron los administradores. "
        f"Respaldo creado: {result['backup'].name}",
        "success",
    )
    return redirect(url_for("admin.dashboard"))
