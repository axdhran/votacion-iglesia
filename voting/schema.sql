PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Administración
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status     TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft', 'open', 'closed')),
    -- 'token' = código personal de un solo uso.
    -- 'kiosk' = mesa compartida sin código; la pantalla se reinicia por voto.
    voting_mode TEXT NOT NULL DEFAULT 'token'
               CHECK (voting_mode IN ('token', 'kiosk')),
    created_at TEXT NOT NULL,
    opened_at  TEXT,
    closed_at  TEXT,
    CHECK (length(trim(name)) BETWEEN 2 AND 80)
);

-- Un departamento cerrado es terminal: no puede reabrirse ni volver a
-- preparación, aunque se intente modificar la base directamente.
CREATE TRIGGER IF NOT EXISTS trg_departments_no_reopen
BEFORE UPDATE OF status ON departments
WHEN (OLD.status = 'closed' AND NEW.status <> 'closed')
   OR (OLD.status = 'open' AND NEW.status = 'draft')
BEGIN
    SELECT RAISE(ABORT, 'El estado de la votación no puede retroceder');
END;

CREATE TABLE IF NOT EXISTS candidates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (length(trim(full_name)) BETWEEN 2 AND 120)
);

-- Relación muchos-a-muchos candidato <-> departamento, con la ronda como
-- atributo de la inscripción. Un candidato puede estar en varios
-- departamentos, pero no en las dos rondas del mismo departamento.
CREATE TABLE IF NOT EXISTS candidate_departments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id  INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    round         TEXT NOT NULL CHECK (round IN ('principal', 'treasurer')),
    created_at    TEXT NOT NULL,
    UNIQUE (candidate_id, department_id, round)
);

CREATE INDEX IF NOT EXISTS idx_candidate_departments_dept_round
    ON candidate_departments (department_id, round);

CREATE TRIGGER IF NOT EXISTS trg_candidate_departments_disjoint
BEFORE INSERT ON candidate_departments
WHEN EXISTS (
    SELECT 1 FROM candidate_departments
    WHERE candidate_id = NEW.candidate_id
      AND department_id = NEW.department_id
      AND round <> NEW.round
)
BEGIN
    SELECT RAISE(ABORT, 'El candidato ya está inscrito en la otra ronda de este departamento');
END;

CREATE TRIGGER IF NOT EXISTS trg_candidate_departments_only_draft
BEFORE INSERT ON candidate_departments
WHEN (SELECT status FROM departments WHERE id = NEW.department_id) <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'Solo se pueden inscribir candidatos mientras el departamento está en preparación');
END;

CREATE TRIGGER IF NOT EXISTS trg_candidate_departments_no_update
BEFORE UPDATE ON candidate_departments
BEGIN
    SELECT RAISE(ABORT, 'Las inscripciones de candidatos no se pueden modificar');
END;

CREATE TRIGGER IF NOT EXISTS trg_candidate_departments_no_delete
BEFORE DELETE ON candidate_departments
WHEN (SELECT status FROM departments WHERE id = OLD.department_id) <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'No se pueden borrar inscripciones de una votación abierta o cerrada');
END;

-- ---------------------------------------------------------------------------
-- Control de acceso sin identidad: tokens de un solo uso
-- ---------------------------------------------------------------------------
-- Se guarda únicamente el HMAC del token. No hay nombre, correo, teléfono ni
-- cualquier otro dato del votante. Tampoco se guarda la fecha de uso.
CREATE TABLE IF NOT EXISTS voter_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    used          INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0, 1)),
    created_at    TEXT NOT NULL,
    CHECK (length(token_hash) = 64)
);

CREATE INDEX IF NOT EXISTS idx_voter_tokens_dept_used
    ON voter_tokens (department_id, used);

CREATE TRIGGER IF NOT EXISTS trg_voter_tokens_immutable
BEFORE UPDATE OF token_hash, department_id, created_at ON voter_tokens
BEGIN
    SELECT RAISE(ABORT, 'El token solo puede marcarse como usado');
END;

CREATE TRIGGER IF NOT EXISTS trg_voter_tokens_no_delete
BEFORE DELETE ON voter_tokens
BEGIN
    SELECT RAISE(ABORT, 'Los tokens emitidos no se pueden borrar');
END;

-- El uso es de una sola vía: un token usado no puede volver a habilitarse.
CREATE TRIGGER IF NOT EXISTS trg_voter_tokens_used_one_way
BEFORE UPDATE OF used ON voter_tokens
WHEN OLD.used = 1 AND NEW.used = 0
BEGIN
    SELECT RAISE(ABORT, 'Un token usado no puede volver a habilitarse');
END;

-- ---------------------------------------------------------------------------
-- Sesión anónima de papeleta
-- ---------------------------------------------------------------------------
-- Se crea al canjear un token. No contiene ninguna referencia al token ni al
-- votante. La cookie firmada solo lleva el id aleatorio de esta sesión.
CREATE TABLE IF NOT EXISTS ballot_sessions (
    id            TEXT PRIMARY KEY,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    consumed      INTEGER NOT NULL DEFAULT 0 CHECK (consumed IN (0, 1)),
    created_at    TEXT NOT NULL,
    CHECK (length(id) BETWEEN 32 AND 128)
);

CREATE INDEX IF NOT EXISTS idx_ballot_sessions_dept_consumed
    ON ballot_sessions (department_id, consumed);

CREATE TRIGGER IF NOT EXISTS trg_ballot_sessions_immutable
BEFORE UPDATE OF id, department_id, created_at ON ballot_sessions
BEGIN
    SELECT RAISE(ABORT, 'La sesión anónima solo puede marcarse como consumida');
END;

CREATE TRIGGER IF NOT EXISTS trg_ballot_sessions_no_delete_consumed
BEFORE DELETE ON ballot_sessions
WHEN OLD.consumed = 1
BEGIN
    SELECT RAISE(ABORT, 'No se puede borrar una sesión que ya emitió voto');
END;

-- La sesión solo puede pasar de no consumida a consumida.
CREATE TRIGGER IF NOT EXISTS trg_ballot_sessions_consumed_one_way
BEFORE UPDATE OF consumed ON ballot_sessions
WHEN OLD.consumed = 1 AND NEW.consumed = 0
BEGIN
    SELECT RAISE(ABORT, 'Una sesión consumida no puede volver a habilitarse');
END;

-- ---------------------------------------------------------------------------
-- Papeletas y selecciones
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ballots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES ballot_sessions(id) ON DELETE RESTRICT,
    department_id   INTEGER NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
    round           TEXT NOT NULL CHECK (round IN ('principal', 'treasurer')),
    receipt_hash    TEXT NOT NULL UNIQUE,
    selections_hash TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    entry_hash      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (session_id, round),
    CHECK (length(receipt_hash) = 64),
    CHECK (length(selections_hash) = 64),
    CHECK (length(prev_hash) = 64),
    CHECK (length(entry_hash) = 64)
);

CREATE INDEX IF NOT EXISTS idx_ballots_dept_id
    ON ballots (department_id, id);

CREATE TABLE IF NOT EXISTS ballot_selections (
    ballot_id    INTEGER NOT NULL REFERENCES ballots(id) ON DELETE CASCADE,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE RESTRICT,
    position     INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
    PRIMARY KEY (ballot_id, position),
    UNIQUE (ballot_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_ballot_selections_candidate
    ON ballot_selections (candidate_id);

-- Una selección solo es válida si el candidato está inscrito en el
-- departamento y la ronda de la papeleta.
CREATE TRIGGER IF NOT EXISTS trg_ballot_selection_enrolled
BEFORE INSERT ON ballot_selections
WHEN NOT EXISTS (
    SELECT 1
    FROM ballots b
    JOIN candidate_departments cd
      ON cd.department_id = b.department_id
     AND cd.round = b.round
     AND cd.candidate_id = NEW.candidate_id
    WHERE b.id = NEW.ballot_id
)
BEGIN
    SELECT RAISE(ABORT, 'El candidato no está inscrito en la ronda de esta papeleta');
END;

CREATE TRIGGER IF NOT EXISTS trg_ballot_selection_position
BEFORE INSERT ON ballot_selections
WHEN (
    (SELECT round FROM ballots WHERE id = NEW.ballot_id) = 'principal'
    AND NEW.position > 3
)
OR (
    (SELECT round FROM ballots WHERE id = NEW.ballot_id) = 'treasurer'
    AND NEW.position > 1
)
BEGIN
    SELECT RAISE(ABORT, 'Posición de selección inválida para la ronda');
END;

CREATE TRIGGER IF NOT EXISTS trg_ballot_selection_max
AFTER INSERT ON ballot_selections
WHEN (SELECT COUNT(*) FROM ballot_selections WHERE ballot_id = NEW.ballot_id) >
     CASE (SELECT round FROM ballots WHERE id = NEW.ballot_id)
         WHEN 'principal' THEN 3
         ELSE 1
     END
BEGIN
    SELECT RAISE(ABORT, 'Demasiadas selecciones para la ronda');
END;

CREATE TRIGGER IF NOT EXISTS trg_ballots_no_update
BEFORE UPDATE ON ballots
BEGIN
    SELECT RAISE(ABORT, 'Las papeletas son inmutables');
END;

CREATE TRIGGER IF NOT EXISTS trg_ballots_no_delete
BEFORE DELETE ON ballots
BEGIN
    SELECT RAISE(ABORT, 'Las papeletas son inmutables');
END;

CREATE TRIGGER IF NOT EXISTS trg_ballot_selections_no_update
BEFORE UPDATE ON ballot_selections
BEGIN
    SELECT RAISE(ABORT, 'Las selecciones son inmutables');
END;

CREATE TRIGGER IF NOT EXISTS trg_ballot_selections_no_delete
BEFORE DELETE ON ballot_selections
BEGIN
    SELECT RAISE(ABORT, 'Las selecciones son inmutables');
END;

-- ---------------------------------------------------------------------------
-- Resultados certificados al cierre
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS results (
    department_id       INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE RESTRICT,
    principal_json      TEXT NOT NULL,
    treasurer_json      TEXT NOT NULL,
    principal_ballots   INTEGER NOT NULL,
    treasurer_ballots   INTEGER NOT NULL,
    participation_count INTEGER NOT NULL,
    chain_head          TEXT NOT NULL,
    result_hash         TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_results_no_update
BEFORE UPDATE ON results
BEGIN
    SELECT RAISE(ABORT, 'Los resultados certificados son inmutables');
END;

CREATE TRIGGER IF NOT EXISTS trg_results_no_delete
BEFORE DELETE ON results
BEGIN
    SELECT RAISE(ABORT, 'Los resultados certificados son inmutables');
END;

-- ---------------------------------------------------------------------------
-- Auditoría administrativa encadenada
-- ---------------------------------------------------------------------------
-- Nunca contiene contenido de votos ni datos personales de votantes.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT,
    details     TEXT NOT NULL DEFAULT '{}',
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    CHECK (length(prev_hash) = 64),
    CHECK (length(entry_hash) = 64)
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created
    ON audit_log (id);

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'La auditoría es inmutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'La auditoría es inmutable');
END;
