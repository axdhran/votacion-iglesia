# Voto Iglesia

Aplicación web de votación anónima para los departamentos de una iglesia, construida con **Python + Flask** y **SQLite**. Funciona desde teléfono, tablet o computadora, y está pensada para votación mixta: presencial en el local (con código personal o en modo mesa) y en línea.

La idea central es separar dos cosas que normalmente viven juntas:

1. **Quién puede votar** (control de participación).
2. **Qué votó** (contenido de la papeleta).

El sistema nunca guarda una relación directa entre ambas. Además, cada papeleta se encadena criptográficamente y cada acta se firma al cerrar, para que el conteo pueda recalcularse y detectar alteraciones.

---

## 1. Funcionalidades

- Administrador con usuario y contraseña.
- Departamentos con estado: **en preparación**, **abierto** o **cerrado**.
- Dos formas de acceso por departamento:
  - **Código personal:** token de un solo uso por votante.
  - **Mesa sin código:** computadoras compartidas para votación presencial con fila; la pantalla se reinicia después de cada voto.
- Registro de candidatos y relación muchos-a-muchos candidato–departamento.
- Inscripción por departamento y por ronda:
  - Ronda principal: Presidente, Vicepresidente y Secretario.
  - Ronda de Tesorero.
- Un candidato puede estar en varios departamentos, pero no en las dos rondas del mismo departamento.
- Tokens de acceso de un solo uso, por departamento, sin identidad asociada.
- Listado de candidatos con filtros por nombre, departamento, ronda e inscripción.
- Reinicio seguro de los datos de prueba con respaldo automático y confirmación explícita.
- Votación anónima en una sola sesión: se eligen exactamente 3 candidatos principales y 1 tesorero.
- Prevención de doble voto mediante token de un solo uso + sesión anónima consumida transaccionalmente.
- Cierre de votación con acta firmada y resultados reproducibles.
- Resultados públicos solo después del cierre.
- Comprobantes de papeleta verificables sin revelar las selecciones.
- Auditoría administrativa encadenada, sin contenido de votos.
- Interfaz responsive, sin dependencias de JavaScript externas.
- Pruebas automatizadas con `pytest`.

---

## 2. Estructura del proyecto

```text
votacion-iglesia/
├── app.py                     # Punto de entrada (flask --app app)
├── requirements.txt
├── pytest.ini
├── .env.example
├── README.md
├── voting/
│   ├── __init__.py            # Fábrica de la app, configuración y CLI
│   ├── constants.py
│   ├── db.py                  # Conexión SQLite y transacciones
│   ├── schema.sql             # Esquema, índices y triggers de protección
│   ├── security.py            # CSRF, cookies, cabeceras, login
│   ├── audit.py               # Auditoría administrativa encadenada
│   ├── integrity.py           # HMAC, cadena de papeletas, actas y verificación
│   ├── elections.py           # Departamentos, candidatos, tokens, apertura/cierre
│   ├── ballots.py             # Canje de token, emisión de voto, comprobantes
│   ├── maintenance.py         # Respaldos y reinicio de datos de prueba
│   ├── blueprints/
│   │   ├── public.py          # Inicio, resultados y verificación
│   │   ├── vote.py            # Flujo del votante
│   │   └── admin.py           # Panel administrativo
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── verify.html
│   │   ├── error.html
│   │   ├── vote/
│   │   ├── results/
│   │   └── admin/
│   └── static/
│       ├── css/styles.css
│       └── js/app.js
└── tests/
    ├── conftest.py
    ├── test_flows.py
    ├── test_kiosk.py
    ├── test_admin_tools.py
    ├── test_integrity.py
    └── test_security.py
```

La base de datos se crea en `instance/voting.db` por defecto. Las claves locales se generan en `instance/.secret_key` e `instance/.integrity_key` si no se pasan por variables de entorno.

---

## 3. Instalación y ejecución

Requiere Python 3.11 o superior.

### Windows PowerShell

```powershell
cd C:\Users\ELOHIM\votacion-iglesia
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

flask --app app init-db
flask --app app create-admin
flask --app app run --debug
```

Si PowerShell bloquea la activación del entorno:

```powershell
.\.venv\Scripts\python.exe -m flask --app app run --debug
```

Abra `http://127.0.0.1:5000`.

- Votantes: `http://127.0.0.1:5000/votar`
- Mesa sin código: `http://127.0.0.1:5000/votar/mesa`
- Resultados: `http://127.0.0.1:5000/resultados`
- Verificación de comprobantes: `http://127.0.0.1:5000/verificar`
- Administración: `http://127.0.0.1:5000/admin`
- Mantenimiento y reinicio: `http://127.0.0.1:5000/admin/maintenance`

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app init-db
flask --app app create-admin
flask --app app run --debug
```

### Crear el administrador

`create-admin` pide usuario y contraseña de forma interactiva. La contraseña debe tener al menos 12 caracteres. No existe registro público de administradores.

```bash
flask --app app create-admin --username admin
```

### Ejecutar las pruebas

```bash
python -m pytest -q
```

Las pruebas usan una base de datos temporal y claves de prueba; no tocan `instance/voting.db`.

### Comandos útiles

```bash
# Recalcular integridad de todas las actas y de la auditoría
flask --app app verify-all

# Recalcular un acta cerrada desde las papeletas almacenadas
flask --app app recount --department-id 1
```

`recount` solo funciona cuando el departamento está cerrado.

---

## 4. Flujo de uso

### 4.1 Preparación

1. El administrador crea un departamento y elige su **forma de acceso**: código personal o mesa sin código. Nace en estado **En preparación**.
2. Registra candidatos globalmente.
3. Inscribe cada candidato en un departamento y en una ronda:
   - `principal`: puede ser elegido para Presidente, Vicepresidente o Secretario.
   - `treasurer`: solo para Tesorero.

   El listado de candidatos se puede filtrar por **nombre**, **departamento**, **ronda/rol** y **estado de inscripción**. El formulario de inscripción siempre conserva la lista completa de candidatos.
4. Si usa **código personal**, genera tokens de acceso para ese departamento y los distribuye fuera del sistema:
   - **Presencial:** la mesa verifica la identidad de la persona y le entrega un token.
   - **En línea:** la iglesia envía un token por el canal oficial.
   Si usa **mesa sin código**, no genera tokens: prepara las computadoras y abre `/votar/mesa` en cada una.
5. Abre la votación. A partir de ese momento no se pueden inscribir más candidatos ni cambiar la forma de acceso.

La forma de acceso solo se puede cambiar mientras el departamento está **En preparación**. Un trigger de SQLite también lo impide.

Para abrir se exigen al menos **3 candidatos principales** y **1 candidato de tesorero**. El conjunto de cada ronda es independiente.

### 4.2 Votación

1. El votante entra a `/votar` e ingresa su token.
2. El sistema valida que el token exista, no haya sido usado y que el departamento esté abierto.
3. En la misma transacción:
   - Marca el token como usado.
   - Crea una **sesión anónima** con un identificador aleatorio.
4. El navegador recibe una cookie firmada con esa sesión anónima.
5. El votante elige 3 candidatos principales y 1 tesorero.
6. Al enviar, en una sola transacción:
   - Se crean dos papeletas: una principal y una de tesorero.
   - Se guardan las selecciones.
   - Se marca la sesión anónima como consumida.
7. Se muestran dos comprobantes. La cookie anónima se elimina.
8. El votante puede verificar sus comprobantes en `/verificar`.

#### Modo mesa sin código

Pensado para **4 o 5 computadoras** en el local, con los votantes haciendo fila:

1. Cada computadora abre `/votar/mesa` y queda mostrando los departamentos abiertos en modo mesa.
2. El voluntario verifica la identidad **fuera del sistema** y toca el botón del departamento.
3. Se crea una sesión anónima nueva, sin token, y se entrega el dispositivo al votante.
4. El votante elige, revisa y confirma. Al terminar, la pantalla muestra el resultado y vuelve sola a `/votar/mesa` en **12 segundos**; también hay un botón **Siguiente persona ahora**.
5. Si la persona se arrepiente antes de enviar, el voluntario usa **Reiniciar pantalla**. Esa sesión sin usar se descarta y no se guarda ningún voto.
6. Las sesiones ya consumidas nunca se borran, así que el acta sigue siendo verificable.

En este modo no hay prevención técnica de doble voto: la garantía de “una persona, un voto” depende del control físico de la mesa. Es la misma limitación que ya existe con los tokens, pero concentrada en el voluntario que entrega el dispositivo.

### 4.3 Cierre y resultados

1. El administrador cierra la votación.
2. El sistema recalcula la cadena, los cuadres y el acta.
3. Si la verificación es correcta, guarda el acta firmada y marca el departamento como cerrado.
4. Los resultados se publican en `/resultados`.
5. El administrador puede repetir la verificación desde el panel o con `verify-all`.

El cierre es definitivo: no se puede reabrir un departamento cerrado.

---

## 5. Control de acceso según la modalidad

### 5.1 Presencial en el local

- La identidad se verifica **fuera del sistema**: mesa de control, lista física o criterio que defina la iglesia.
- La mesa entrega un token de un solo uso por persona y por departamento.
- El votante ingresa el token en el dispositivo del local o en su propio teléfono.
- El sistema no registra el nombre, documento, correo ni teléfono de quien recibe el token.
- La entrega del token no queda registrada en la aplicación.

### 5.2 En línea

- La iglesia genera un lote de tokens y los distribuye por el canal oficial que considere seguro.
- Cada persona recibe un token por departamento.
- El token se ingresa en un formulario `POST`; no se coloca en la URL para evitar que quede en el historial o en los logs de acceso.
- El sistema solo guarda el HMAC del token y un indicador `used`. No guarda el valor visible ni el destinatario.

### 5.3 Mesa sin código (presencial con fila)

- La identidad se verifica **fuera del sistema**, igual que en el modo presencial con token.
- El voluntario entrega el dispositivo con la papeleta ya abierta; no hay código que escribir ni repartir.
- Cada vez que empieza una persona se crea una **sesión anónima** nueva, sin relación con tokens ni identidad.
- Al confirmar, la pantalla se reinicia sola para la siguiente persona.
- Si se reinicia antes de confirmar, la sesión se descarta y no deja voto.
- El sistema no puede impedir que una misma persona vuelva a la fila. Esa garantía es física: la mesa debe controlar quién ya votó.

Este modo simplifica la logística (no hay tarjetas ni códigos) pero concentra más responsabilidad en la mesa. Para votación en línea o con padrón estricto, use el modo con código personal.

### 5.4 Supuestos importantes del control de acceso

El sistema garantiza **un uso por token**, pero no puede saber si una misma persona recibió dos tokens. La regla “una persona = un token por departamento” depende de:

- que la iglesia mantenga un padrón o control externo de entrega;
- que no se dupliquen entregas por error;
- que los tokens no se compartan;
- que la mesa o el canal de distribución sean confiables.

Si se entregan dos tokens a la misma persona, el sistema los tratará como dos participaciones válidas. Esta es una limitación inherente al diseño sin identidad: no se puede deduplicar por persona sin volver a vincular identidad y voto.

---

## 6. Anonimato: cómo se separa la identidad del voto

### 6.1 Tablas separadas y sin referencia cruzada

| Tabla | Qué guarda | Qué NO guarda |
|---|---|---|
| `voter_tokens` | HMAC del token, departamento, `used` | nombre, correo, teléfono, sesión, papeleta |
| `ballot_sessions` | id aleatorio, departamento, `consumed` | token, identidad, selecciones |
| `ballots` | departamento, ronda, hashes, sesión anónima | identidad del votante |
| `ballot_selections` | candidato y posición de una papeleta | identidad del votante |

No existe una clave foránea entre `voter_tokens` y `ballot_sessions`, ni entre `voter_tokens` y `ballots`. Tampoco se guarda la fecha de uso del token.

### 6.2 Secuencia transaccional

1. **Canje:** se marca el token como usado y se crea la sesión anónima en la misma transacción. No se escribe ninguna relación entre ambos.
2. **Emisión:** la sesión anónima crea las dos papeletas y se marca como consumida en la misma transacción.
3. **Doble voto:** se bloquea de dos formas independientes:
   - el token ya está usado;
   - la sesión anónima ya está consumida.
4. **Papeleta:** no contiene ningún campo de votante.

El identificador de sesión es aleatorio y se envía al navegador en una cookie firmada, `HttpOnly` y `SameSite=Lax`. Si la cookie se pierde después del canje, el token ya quedó consumido y no se puede recuperar la sesión; la iglesia puede emitir otro token, pero el sistema no puede saber que corresponde a la misma persona.

### 6.3 Por qué no se guarda `used_at`

Guardar la hora exacta de uso del token permitiría correlacionarlo con la hora de creación de una sesión anónima. Por eso `voter_tokens` solo guarda `used = 0/1`. La sesión anónima sí guarda `created_at`, necesario para expirar cookies, pero no está vinculada a un token.

### 6.4 Límite real del anonimato

El sistema elimina el vínculo directo, pero no puede eliminar todos los riesgos contextuales:

- Un administrador con acceso a SQLite puede ver las papeletas y las horas de las sesiones, aunque no sepa a quién pertenecen.
- Si el electorado es muy pequeño y se conoce de antemano la intención de cada persona, un análisis estadístico podría inferir votos.
- Los logs del servidor o del proxy pueden guardar IP y hora. La aplicación no guarda IP, pero la infraestructura podría hacerlo.
- Si la entrega de tokens se registra externamente (por ejemplo, una lista de “token X a persona Y”), esa lista no se cruza con las papeletas en el sistema, pero un observador con acceso a horarios y a esa lista podría intentar correlaciones.
- El voto no es resistente a coerción: una persona puede mostrar voluntariamente por quién votó. Los comprobantes no revelan las selecciones, pero el votante sí puede contarlas.

Para un proceso de iglesia, se recomienda separar roles: quien entrega tokens no debería administrar la base de datos, y el administrador no debería conservar listas de asignación de tokens.

---

## 7. Modelo de datos

El esquema completo está en `voting/schema.sql`. Resumen:

### `departments`
- `id`, `name` único, `status` (`draft`, `open`, `closed`), `voting_mode` (`token` o `kiosk`), `created_at`, `opened_at`, `closed_at`.
- `voting_mode` solo puede cambiar mientras `status = 'draft'`; un trigger lo impone.

### `candidates`
- `id`, `full_name`, `created_at`.

### `candidate_departments` (muchos-a-muchos)
- `candidate_id`, `department_id`, `round` (`principal` o `treasurer`).
- Restricción única `(candidate_id, department_id, round)`.
- Un trigger impide que un candidato esté en las dos rondas del mismo departamento.
- Un trigger impide inscribir cuando el departamento no está en preparación.
- Las inscripciones no se pueden modificar; solo se pueden borrar mientras el departamento está en preparación.

Ejemplo: un candidato puede estar en `Jóvenes/principal` y `Misiones/treasurer`, pero no en `Jóvenes/principal` y `Jóvenes/treasurer`.

### `voter_tokens`
- `department_id`, `token_hash` único (HMAC-SHA256), `used`, `created_at`.
- No hay `used_at`, ni nombre, ni correo, ni sesión.

### `ballot_sessions`
- `id` aleatorio, `department_id`, `consumed`, `created_at`.
- No hay referencia al token.
- En modo mesa se crea una sesión sin token por cada persona que empieza a votar; las sesiones sin consumir se pueden descartar al reiniciar la pantalla.

### `ballots`
- `session_id`, `department_id`, `round`, `receipt_hash`, `selections_hash`, `prev_hash`, `entry_hash`, `created_at`.
- Restricción única `(session_id, round)`.
- Triggers de inmutabilidad para `UPDATE` y `DELETE`.

### `ballot_selections`
- `ballot_id`, `candidate_id`, `position`.
- Clave primaria `(ballot_id, position)` y única `(ballot_id, candidate_id)`.
- Trigger que valida que el candidato esté inscrito en el departamento y ronda de la papeleta.
- Trigger que limita a 3 selecciones principales o 1 de tesorero.
- Triggers de inmutabilidad.

### `results`
- Acta certificada: listas JSON de conteos, número de papeletas, participaciones, cabeza de cadena, `result_hash`, fecha.
- Inmutable mediante triggers.

### `audit_log`
- Acciones administrativas con actor, acción, entidad, detalles, `prev_hash`, `entry_hash`, fecha.
- Inmutable mediante triggers.
- No guarda contenido de votos.

---

## 8. Lógica de votación y conteo

### 8.1 Ronda principal

- Cada votante elige exactamente 3 candidatos distintos de la lista `principal` de su departamento.
- Cada selección suma 1 voto al candidato.
- Al cerrar, se ordenan los candidatos por:
  1. votos descendente;
  2. `candidate_id` ascendente (orden de inscripción) como desempate determinista.
- Los tres primeros ocupan Presidente, Vicepresidente y Secretario, en ese orden.

### 8.2 Ronda de tesorero

- Cada votante elige exactamente 1 candidato de la lista `treasurer`.
- Gana el candidato con más votos.
- Si hay empate en el primer lugar, se aplica el mismo desempate por `candidate_id`.

### 8.3 Regla de desempate

La regla se muestra en el acta y en la interfaz: **ante empate, menor `candidate_id` (orden de inscripción)**. Es una decisión explícita para que el resultado sea reproducible. Si la iglesia prefiere otra regla (por ejemplo, sorteo público o segunda vuelta), debe cambiarse antes de abrir la votación.

### 8.4 Cuadres de integridad

Para un departamento con `N` participaciones consumidas:

- `papeletas principales = N`
- `papeletas de tesorero = N`
- `suma de votos principales = 3 × N`
- `suma de votos de tesorero = 1 × N`
- cada sesión consumida tiene exactamente una papeleta de cada ronda;
- cada papeleta tiene exactamente 3 o 1 selecciones según su ronda;
- todas las selecciones corresponden a candidatos inscritos en ese departamento y ronda;
- la cadena HMAC es válida;
- el acta almacenada coincide con el recálculo actual.

Estos cuadres se muestran en el panel de integridad y se ejecutan antes de certificar el cierre.

---

## 9. Integridad y verificabilidad

### 9.1 Cadena de papeletas

Cada papeleta tiene:

- `prev_hash`: hash de la papeleta anterior del departamento, o un hash génesis derivado del departamento;
- `entry_hash`: HMAC-SHA256 de `prev_hash` + payload canónico.

El payload incluye departamento, ronda, id de sesión anónima, hash del comprobante, selecciones ordenadas y fecha. Si alguien cambia una selección, la cadena se rompe.

### 9.2 Firma del acta

Al cerrar:

1. Se recalcula el conteo desde `ballots` y `ballot_selections`.
2. Se verifica la cadena y los cuadres.
3. Se guarda un acta con los conteos y la cabeza de cadena.
4. Se calcula `result_hash` con HMAC-SHA256 sobre el acta canónica.

Los resultados públicos se recalculan al mostrarse y se comparan con el acta. Si algo no coincide, la página muestra una advertencia de integridad.

### 9.3 Clave de integridad

`INTEGRITY_KEY` se guarda fuera de SQLite:

- variable de entorno, o
- archivo local `instance/.integrity_key`.

Esto significa que modificar SQLite directamente no basta para recalcular hashes válidos. Si se pierde la clave, las actas antiguas no se podrán verificar. Si se cambia la clave, los tokens y actas existentes quedarán inválidos. **Hay que respaldar la clave por separado y no incluirla en el mismo respaldo que la base de datos.**

### 9.4 Auditoría administrativa

Cada acción administrativa se encadena con HMAC:

- creación de departamentos;
- cambio de forma de acceso mientras el departamento está en preparación;
- creación de candidatos;
- inscripción de candidatos;
- generación de tokens;
- apertura y cierre;
- inicio y cierre de sesión administrativa.

Nunca se registra el contenido de una papeleta, el token visible ni la identidad de un votante. La cadena de auditoría se verifica en `/admin/audit` y con `flask --app app verify-all`.

### 9.5 Inmutabilidad en la base de datos

Además de la lógica de la aplicación, SQLite tiene triggers que impiden:

- modificar o borrar papeletas;
- modificar o borrar selecciones;
- modificar o borrar actas;
- modificar o borrar la auditoría;
- cambiar el hash o departamento de un token;
- volver a habilitar un token ya usado;
- borrar tokens emitidos;
- volver a habilitar una sesión anónima ya consumida;
- borrar sesiones ya consumidas;
- reabrir un departamento cerrado o devolverlo a preparación;
- modificar inscripciones;
- borrar inscripciones de votaciones abiertas o cerradas.

Los triggers no sustituyen la custodia de la clave, pero elevan el costo de una manipulación accidental o directa desde la base.

### 9.6 Límite criptográfico

La verificación es **simétrica**: quien posea `INTEGRITY_KEY` puede recalcular hashes. Un administrador con la clave y acceso a SQLite podría, en teoría, reescribir la base y recalcular todo. Para una garantía pública más fuerte se necesitaría firma asimétrica con clave privada fuera del servidor, un servicio de sellado de tiempo o publicación externa del hash del acta. Esa es una mejora posible, no parte de esta entrega.

---

## 10. Resultados y comprobantes

### Resultados

- `/resultados` solo lista departamentos cerrados.
- `/resultados/<id>` devuelve 404 si el departamento no está cerrado o no tiene acta.
- El panel administrativo tampoco muestra conteos antes del cierre; solo el estado y métricas operativas de participación.
- El acta muestra ganadores, conteo completo, participaciones, hashes y el estado de integridad.
- `flask --app app recount --department-id <id>` recalcula el acta desde SQLite, solo si está cerrada.

### Comprobantes

- Cada papeleta genera un código aleatorio.
- Solo se guarda su HMAC en `receipt_hash`; el valor visible se muestra una sola vez.
- `/verificar` confirma que el comprobante existe y que la cadena del departamento es válida.
- No muestra las selecciones de la papeleta.

---

## 11. Seguridad de la aplicación

- **CSRF:** todas las peticiones `POST` requieren un token de sesión comparado con `hmac.compare_digest`. Aplica a login, votación, administración, cierre, generación de tokens y verificación.
- **SQL injection:** todas las consultas usan parámetros `?`. Los únicos fragmentos dinámicos son listas de placeholders construidas a partir de IDs enteros ya validados.
- **XSS:** Jinja escapa por defecto; no se usa `|safe`; la CSP bloquea scripts y estilos inline.
- **Cookies:** `HttpOnly`, `SameSite=Lax`; `Secure` se activa con `COOKIE_SECURE=1` en producción. La cookie del votante está firmada con `SECRET_KEY`.
- **Sesión administrativa:** se limpia al iniciar sesión para evitar fijación de sesión; expira a las 8 horas.
- **Contraseñas:** hash scrypt de Werkzeug; no se guardan en claro. La cuenta se crea solo por CLI.
- **Cabeceras:** CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` y HSTS cuando se sirve por HTTPS.
- **Caché:** las páginas de administración y votación envían `Cache-Control: no-store`.
- **Validación:** nombres con longitud y caracteres controlados; selecciones exactas y distintas; IDs existentes y pertenecientes al departamento/ronda; estado del departamento verificado dentro de la transacción.
- **Concurrencia:** `BEGIN IMMEDIATE` en escrituras; SQLite en modo WAL y `busy_timeout`.
- **Auditoría:** no incluye votos ni identidad de votantes.
- **Tokens:** 256 bits de entropía; solo se almacena HMAC.

### Limitaciones de seguridad

- No hay MFA para el administrador.
- No hay rate limiting integrado para login o intentos de token. Se recomienda configurarlo en el proxy inverso. La entropía de los tokens hace inviable la fuerza bruta, pero el login sí merece protección adicional.
- No hay recuperación de contraseña ni borrado de administradores desde la interfaz.
- SQLite está pensado para un solo servidor. Varios procesos pueden leer, pero las escrituras se serializan.
- La base no cifra el contenido de las papeletas en reposo. El anonimato es de vínculo, no confidencialidad del contenido frente a quien lea SQLite.
- La aplicación no controla la seguridad del dispositivo del votante ni evita la observación de la pantalla.
- El administrador es una figura de confianza. No hay separación de funciones ni doble custodia.

---

## 12. Pruebas automatizadas

Ejecutar:

```bash
python -m pytest -q
```

Cobertura incluida:

- conteo por rondas y asignación de puestos;
- empate resuelto de forma determinista;
- doble voto bloqueado por token y por sesión;
- voto después del cierre rechazado;
- candidato fuera de su departamento rechazado;
- candidato en las dos rondas del mismo departamento rechazado;
- listas de candidatos aisladas por departamento;
- separación entre identidad/token y papeleta;
- verificación de comprobantes sin revelar selecciones;
- modo mesa: voto completo, auto-reinicio, reinicio sin voto y descarte de sesión;
- bloqueo de tokens y de sesiones de mesa en el departamento equivocado;
- cambio de forma de acceso solo en preparación (aplicación y trigger);
- migración de una base antigua que no tenía `voting_mode`;
- renderizado de las pantallas nuevas y modificadas;
- filtros de candidatos por nombre, departamento, ronda e inscripción;
- reinicio con confirmación explícita, respaldo automático y conservación del administrador;
- CSRF obligatorio en todos los POST;
- autenticación administrativa y cabeceras de seguridad;
- flujo administrativo completo y auditoría;
- detección de manipulación de selecciones, actas y auditoría;
- triggers de inmutabilidad y de una sola vía (token usado, sesión consumida, departamento cerrado);
- cuadres de totales y acta reproducible.

Resultado esperado: **30 pruebas aprobadas**.

---

## 13. Mantenimiento y reinicio de datos de prueba

La página `/admin/maintenance` está pensada para limpiar la aplicación después de una demostración o de unas pruebas, antes de una votación real.

### Qué muestra

- Cuántos departamentos, candidatos, inscripciones, tokens, sesiones, papeletas, actas y acciones de auditoría existen.
- Los últimos respaldos automáticos creados.

### Qué borra el reinicio

- Departamentos, candidatos, inscripciones, tokens, sesiones anónimas, papeletas, selecciones, actas y auditoría.
- **No borra las cuentas de administrador**, para que pueda seguir entrando al panel.
- **No toca las claves** `SECRET_KEY` ni `INTEGRITY_KEY`.

### Cómo protege la operación

1. Exige sesión administrativa y token CSRF.
2. Exige escribir la palabra **REINICIAR**.
3. Exige marcar una casilla de confirmación.
4. Antes de borrar, crea un respaldo completo con la API de respaldo de SQLite.
5. Después de borrar, recrea el esquema y los triggers de inmutabilidad.
6. Registra el reinicio como primera acción de la nueva auditoría.

### Dónde quedan los respaldos

Por defecto, en:

```text
instance/backups/voting-antes-de-reiniciar-AAAAMMDD-HHMMSS.db
```

La carpeta se puede cambiar con la variable de entorno `BACKUP_DIR`.

### Cómo restaurar un respaldo

1. Detenga el servidor.
2. Guarde una copia del `instance/voting.db` actual.
3. Reemplace `instance/voting.db` por el archivo de respaldo elegido.
4. Arranque el servidor y ejecute `flask --app app verify-all`.

Use siempre la misma `INTEGRITY_KEY` con la que se creó el respaldo; sin ella, las actas y los tokens no se pueden verificar.

> **Importante:** no reinicie la base mientras haya una votación real en curso. El reinicio está pensado para datos de prueba.

---

## 14. Despliegue en producción

Checklist mínimo:

1. Generar claves aleatorias y definirlas por variables de entorno:
   - `SECRET_KEY` (32+ bytes aleatorios).
   - `INTEGRITY_KEY` (32+ bytes aleatorios, distinta de `SECRET_KEY`).
2. Servir siempre por **HTTPS** y poner `COOKIE_SECURE=1`.
3. Ejecutar con `FLASK_DEBUG=0` y un servidor WSGI de producción (Waitress, Gunicorn o similar), no con el servidor de desarrollo.
4. Restringir el acceso al panel `/admin` por red o proxy si es posible.
5. Configurar rate limiting para `/admin/login` y `/votar` en el proxy.
6. Respaldar `instance/voting.db` y la clave de integridad **por separado**. La clave no debe quedar dentro del mismo respaldo de la base.
7. Probar la restauración de un respaldo y ejecutar `flask --app app verify-all`.
8. No registrar cuerpos de formularios ni tokens en logs del proxy.
9. Mantener la base y la clave fuera de repositorios y de carpetas públicas.
10. Definir antes de abrir la votación la regla de desempate y el procedimiento de entrega de tokens.

Ejemplo de ejecución con Waitress (instalar aparte):

```bash
pip install waitress
waitress-serve --listen=127.0.0.1:8000 --call voting:create_app
```

Detrás de Nginx o Caddy, termine TLS y reenvíe a `127.0.0.1:8000`.

---

## 15. Supuestos y decisiones

- Cada departamento tiene dos rondas y ambas se completan en una sola sesión.
- Los candidatos de la ronda principal son elegibles para los tres puestos; no hay postulaciones separadas por puesto.
- La ronda de tesorero usa un conjunto disjunto de candidatos.
- Los nombres de departamento son únicos; los nombres de candidatos pueden repetirse (dos personas podrían llamarse igual).
- El orden de inscripción (`candidate_id`) es el criterio de desempate.
- El administrador es de confianza y custodia la clave de integridad.
- La entrega de tokens y la verificación de identidad ocurren fuera del sistema.
- La iglesia garantiza una persona = un token por departamento.
- En modo mesa, la iglesia garantiza una persona = una participación mediante control físico de la fila.
- Un token usado no se reutiliza, aunque el votante no termine la papeleta.
- En modo mesa, una sesión sin confirmar se descarta al reiniciar la pantalla; una sesión confirmada nunca se borra.
- Si el votante pierde la cookie después del canje, la sesión no se puede recuperar; se puede emitir un token nuevo.
- Los resultados se muestran en hora UTC.
- No se incluye integración de correo, SMS ni WhatsApp; la distribución de tokens es externa.
- No se incluye una segunda vuelta automática; los empates se resuelven con la regla indicada.

---

## 16. Resumen de la garantía

- **Un voto por token:** sí, mediante token de un solo uso.
- **Un voto por sesión:** sí, mediante sesión anónima consumida en transacción.
- **Un voto por persona en modo mesa:** depende del control físico de la mesa; el sistema solo garantiza una sesión = un voto.
- **Voto después del cierre:** no, se rechaza dentro de la transacción.
- **Identidad junto al voto:** no existe ese campo ni relación en el esquema.
- **Conteo editable desde la interfaz:** no; se calcula desde las papeletas.
- **Resultados antes del cierre:** no se publican; las rutas devuelven 404 y el panel solo muestra estado operativo.
- **Alteración de votos:** se detecta por cadena HMAC, acta firmada, triggers y cuadres.
- **Auditoría administrativa:** sí, encadenada y sin contenido de votos.
- **Verificación del administrador:** panel de integridad, `verify-all` y `recount`.
- **Anonimato absoluto:** no existe; el sistema elimina el vínculo directo, pero quedan riesgos contextuales, de tamaño de electorado y de custodia de la clave.
