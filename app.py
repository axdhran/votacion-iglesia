"""Punto de entrada para desarrollo y para el servidor WSGI.

Ejecución local:
    flask --app app run --debug
o:
    python app.py
"""

from __future__ import annotations

import os

from voting import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
