"""Páginas públicas: inicio, resultados y verificación de comprobantes."""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request

from ..ballots import verify_receipt
from ..db import get_db
from ..elections import (
    build_winners,
    get_stored_result,
    list_closed_departments,
    list_kiosk_departments,
)
from ..integrity import verify_department_data

public_bp = Blueprint("public", __name__)


@public_bp.get("/")
def index():
    return render_template(
        "index.html",
        closed_departments=list_closed_departments(),
        kiosk_departments=list_kiosk_departments(),
    )


@public_bp.get("/resultados")
def results():
    return render_template(
        "results/list.html",
        departments=list_closed_departments(),
    )


@public_bp.get("/resultados/<int:department_id>")
def result_detail(department_id: int):
    result = get_stored_result(department_id)
    if result is None:
        # No se revela ni siquiera la existencia de resultados antes del cierre.
        abort(404)
    verification = verify_department_data(get_db(), department_id)
    winners = build_winners(result)
    return render_template(
        "results/detail.html",
        result=result,
        winners=winners,
        verification=verification,
    )


@public_bp.route("/verificar", methods=["GET", "POST"])
def verify():
    receipt = None
    submitted = ""
    not_found = False
    if request.method == "POST":
        submitted = (request.form.get("receipt") or "").strip()
        receipt = verify_receipt(submitted)
        not_found = receipt is None
    return render_template(
        "verify.html",
        receipt=receipt,
        submitted=submitted,
        not_found=not_found,
    )
