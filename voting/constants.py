"""Constantes compartidas por la aplicación."""

ROUND_PRINCIPAL = "principal"
ROUND_TREASURER = "treasurer"
ROUNDS = (ROUND_PRINCIPAL, ROUND_TREASURER)

ROUND_LABELS = {
    ROUND_PRINCIPAL: "Cargos principales",
    ROUND_TREASURER: "Tesorero",
}

ROUND_SHORT_LABELS = {
    ROUND_PRINCIPAL: "Presidente, Vicepresidente y Secretario",
    ROUND_TREASURER: "Tesorero",
}

# Cantidad exacta de selecciones permitidas por papeleta.
ROUND_SELECTION_COUNT = {
    ROUND_PRINCIPAL: 3,
    ROUND_TREASURER: 1,
}

# Puestos que se asignan según el orden de votos de la ronda principal.
PRINCIPAL_POSITIONS = ("Presidente", "Vicepresidente", "Secretario")
TREASURER_POSITION = "Tesorero"

DEPARTMENT_DRAFT = "draft"
DEPARTMENT_OPEN = "open"
DEPARTMENT_CLOSED = "closed"
DEPARTMENT_STATUS_LABELS = {
    DEPARTMENT_DRAFT: "En preparación",
    DEPARTMENT_OPEN: "Votación abierta",
    DEPARTMENT_CLOSED: "Votación cerrada",
}

# Formas de dar acceso a la papeleta:
# - token: cada votante recibe un código personal de un solo uso.
# - kiosk: modo mesa; la pantalla se comparte y se reinicia después de cada voto.
VOTING_MODE_TOKEN = "token"
VOTING_MODE_KIOSK = "kiosk"
VOTING_MODES = (VOTING_MODE_TOKEN, VOTING_MODE_KIOSK)
VOTING_MODE_LABELS = {
    VOTING_MODE_TOKEN: "Código personal",
    VOTING_MODE_KIOSK: "Mesa sin código",
}
VOTING_MODE_HELP = {
    VOTING_MODE_TOKEN: "Cada votante recibe un código de un solo uso.",
    VOTING_MODE_KIOSK: "Varias computadoras comparten la pantalla; el votante hace fila y la pantalla se reinicia sola.",
}
