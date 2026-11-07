"""Flujo anónimo del votante."""

from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..ballots import (
    VoteError,
    discard_open_session,
    get_ballot_data,
    redeem_token,
    start_kiosk_session,
    submit_vote,
    validate_selection,
)
from ..constants import VOTING_MODE_KIOSK
from ..elections import get_department, list_kiosk_departments
from ..security import (
    clear_voter_cookie,
    read_voter_cookie,
    set_voter_cookie,
)

vote_bp = Blueprint("vote", __name__, url_prefix="/votar")


def _vote_start_url() -> str:
    """Pantalla de inicio que corresponde al votante actual.

    En modo mesa se vuelve a la pantalla compartida; con código personal se
    vuelve al formulario del token.
    """
    if session.get("kiosk_department_id"):
        return url_for("vote.kiosk_home")
    return url_for("vote.enter")


def _session_expired_message() -> str:
    if session.get("kiosk_department_id"):
        return "Su sesión de votación expiró. La mesa debe empezar de nuevo."
    return "Su sesión de votación expiró. Ingrese nuevamente su código."


@vote_bp.route("", methods=["GET", "POST"])
@vote_bp.route("/", methods=["GET", "POST"])
def enter():
    if request.method == "POST":
        raw_token = request.form.get("token", "")
        try:
            redeemed = redeem_token(raw_token)
        except VoteError as exc:
            flash(str(exc), "error")
            return redirect(url_for("vote.enter"))
        response = redirect(url_for("vote.ballot"))
        set_voter_cookie(
            response,
            redeemed["session_id"],
            redeemed["department_id"],
        )
        session.pop("last_receipts", None)
        session.pop("kiosk_department_id", None)
        session.pop("kiosk_confirmation", None)
        return response

    cookie = read_voter_cookie()
    return render_template(
        "vote/enter.html",
        has_session=cookie is not None,
        kiosk_departments=list_kiosk_departments(),
    )


@vote_bp.get("/mesa")
def kiosk_home():
    """Pantalla de mesa compartida para votar sin código.

    Cada computadora abre esta página. El voluntario elige el departamento y
    entrega el dispositivo al votante; al terminar, la pantalla vuelve aquí.
    """
    cookie = read_voter_cookie()
    return render_template(
        "vote/kiosk.html",
        departments=list_kiosk_departments(),
        has_session=cookie is not None,
    )


@vote_bp.post("/mesa/iniciar")
def kiosk_start():
    """Crea una sesión anónima nueva para el votante que está en la mesa."""
    try:
        department_id = int(request.form.get("department_id", "0"))
    except (TypeError, ValueError):
        department_id = 0

    # Si la pantalla quedó a medias de un votante anterior, se descarta esa
    # sesión para no acumular filas y para que el siguiente empiece limpio.
    cookie = read_voter_cookie()
    if cookie:
        discard_open_session(cookie["sid"])

    try:
        started = start_kiosk_session(department_id)
    except VoteError as exc:
        flash(str(exc), "error")
        return redirect(url_for("vote.kiosk_home"))

    response = redirect(url_for("vote.ballot"))
    set_voter_cookie(
        response,
        started["session_id"],
        started["department_id"],
    )
    session["kiosk_department_id"] = started["department_id"]
    session.pop("last_receipts", None)
    session.pop("kiosk_confirmation", None)
    return response


@vote_bp.post("/mesa/reiniciar")
def kiosk_reset():
    """Reinicia la pantalla de mesa sin registrar ningún voto."""
    cookie = read_voter_cookie()
    if cookie:
        discard_open_session(cookie["sid"])
    session.pop("kiosk_department_id", None)
    session.pop("kiosk_confirmation", None)
    session.pop("last_receipts", None)
    response = redirect(url_for("vote.kiosk_home"))
    clear_voter_cookie(response)
    return response


@vote_bp.get("/acceso")
def quick_access():
    """Página intermedia para tarjetas con QR.

    El código viaja en el fragmento de la URL (después de ``#``), que el
    navegador no envía al servidor. Un pequeño script lo mueve a un formulario
    POST; así el código no queda en los logs de acceso.
    """
    return render_template("vote/quick_access.html")


@vote_bp.get("/papeleta")
def ballot():
    cookie = read_voter_cookie()
    if not cookie:
        flash("Primero debe empezar su votación.", "error")
        return redirect(_vote_start_url())
    try:
        data = get_ballot_data(cookie["sid"], int(cookie["dept"]))
    except VoteError as exc:
        flash(str(exc), "error")
        response = redirect(_vote_start_url())
        clear_voter_cookie(response)
        return response
    return render_template(
        "vote/ballot.html",
        **data,
        selected_principal=[],
        selected_treasurer=[],
        kiosk=data["department"]["voting_mode"] == VOTING_MODE_KIOSK,
    )


@vote_bp.post("/revisar")
def review():
    cookie = read_voter_cookie()
    if not cookie:
        flash(_session_expired_message(), "error")
        return redirect(_vote_start_url())

    principal_values = request.form.getlist("principal")
    treasurer_values = request.form.getlist("treasurer")
    try:
        principal_ids, treasurer_ids = validate_selection(
            session_id=cookie["sid"],
            department_id=int(cookie["dept"]),
            principal_values=principal_values,
            treasurer_values=treasurer_values,
        )
        data = get_ballot_data(cookie["sid"], int(cookie["dept"]))
    except VoteError as exc:
        flash(str(exc), "error")
        return redirect(url_for("vote.ballot"))

    principal_selected = [
        candidate
        for candidate in data["principal"]
        if int(candidate["id"]) in set(principal_ids)
    ]
    treasurer_selected = [
        candidate
        for candidate in data["treasurer"]
        if int(candidate["id"]) in set(treasurer_ids)
    ]
    return render_template(
        "vote/review.html",
        department=data["department"],
        principal=principal_selected,
        treasurer=treasurer_selected,
        kiosk=data["department"]["voting_mode"] == VOTING_MODE_KIOSK,
    )


@vote_bp.post("/editar")
def edit():
    cookie = read_voter_cookie()
    if not cookie:
        flash(_session_expired_message(), "error")
        return redirect(_vote_start_url())

    principal_values = request.form.getlist("principal")
    treasurer_values = request.form.getlist("treasurer")
    try:
        principal_ids, treasurer_ids = validate_selection(
            session_id=cookie["sid"],
            department_id=int(cookie["dept"]),
            principal_values=principal_values,
            treasurer_values=treasurer_values,
        )
        data = get_ballot_data(cookie["sid"], int(cookie["dept"]))
    except VoteError as exc:
        flash(str(exc), "error")
        return redirect(url_for("vote.ballot"))

    return render_template(
        "vote/ballot.html",
        **data,
        selected_principal=principal_ids,
        selected_treasurer=treasurer_ids,
        kiosk=data["department"]["voting_mode"] == VOTING_MODE_KIOSK,
    )


@vote_bp.post("/papeleta")
def cast_ballot():
    cookie = read_voter_cookie()
    if not cookie:
        flash(_session_expired_message(), "error")
        return redirect(_vote_start_url())

    principal_values = request.form.getlist("principal")
    treasurer_values = request.form.getlist("treasurer")
    # Se consulta el modo antes de enviar para saber si hay que reiniciar la
    # pantalla de mesa después de mostrar la confirmación.
    department = get_department(int(cookie["dept"]))
    is_kiosk = bool(
        department and department["voting_mode"] == VOTING_MODE_KIOSK
    )
    try:
        receipts = submit_vote(
            session_id=cookie["sid"],
            department_id=int(cookie["dept"]),
            principal_values=principal_values,
            treasurer_values=treasurer_values,
        )
    except VoteError as exc:
        flash(str(exc), "error")
        return redirect(url_for("vote.ballot"))

    # Los comprobantes se muestran una sola vez. La cookie anónima se elimina:
    # la sesión ya quedó consumida en la base de datos.
    session["last_receipts"] = receipts
    session["kiosk_confirmation"] = is_kiosk
    response = redirect(url_for("vote.confirmation"))
    clear_voter_cookie(response)
    return response


@vote_bp.get("/confirmacion")
def confirmation():
    kiosk = bool(session.pop("kiosk_confirmation", False))
    session.pop("kiosk_department_id", None)
    receipts = session.pop("last_receipts", None)
    if not receipts:
        # En modo mesa, un refresco vuelve a la pantalla de inicio de la mesa.
        return redirect(
            url_for("vote.kiosk_home") if kiosk else url_for("public.index")
        )
    return render_template("vote/confirmation.html", receipts=receipts, kiosk=kiosk)
