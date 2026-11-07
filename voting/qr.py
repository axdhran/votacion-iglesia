"""Generación de códigos QR para tarjetas de acceso."""

from __future__ import annotations

import base64
import io

import qrcode
import qrcode.image.svg


def qr_data_uri(data: str) -> str:
    """Devuelve un SVG en data URI, apto para ``<img src="...">``.

    Se usa el generador SVG para no depender de Pillow ni de archivos
    temporales. La CSP de la aplicación permite imágenes ``data:``.
    """
    image = qrcode.make(
        data,
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
        border=2,
    )
    buffer = io.BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
