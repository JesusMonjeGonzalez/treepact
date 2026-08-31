# TreePact Implementation Status

Estado vivo de la implementacion. Fuentes de autoridad: MASTER_PLAN.md, MEGA_PROMPT_IMPLEMENTATION.txt y el paquete documental de M0.

## Política de verificación (ADR 0011)

Los exit gates M1-M8 significan `implemented_unverified`. Ningún criterio de aceptación se marca `verified` hasta M9, cuando se ejecute una única campaña consolidada contra un candidato congelado. Durante M1-M8 no se crean suites ni se ejecuta pytest.

## Milestones

| Milestone | Estado | Exit gate | Notas |
|---|---|---|---|
| M0 Diseño | `verified` | Paquete documental completo | Revisión exclusivamente documental; sin tests, schemas ni toolchains ejecutados |
| M1 Fundación | `implemented_unverified` | Módulos públicos y fronteras de ownership | pyproject + uv.lock (CPython 3.12.13); taxonomía de errores con códigos estables; enums de estados; IDs UUIDv7; reloj UTC/RFC3339; config TOML con precedencia documentada; superficie CLI completa (17 comandos + provider status) con stubs honestos; runner de migraciones forward-only con ledger y rechazo de schema nuevo; doctor parcial read-only; `config show` operativo |
| M2 Estado y evidencia | `implemented_unverified` | Estado reconstruible; sin credenciales ni prompts completos | Migración 001 con las 17 tablas del modelo; runner transaccional statement-a-statement con digest; máquinas de estado run/attempt/check/proposal/lease con validación de transiciones ilegales; EventStore append-only con secuencia monotónica, hash chain SHA-256, registro estricto de payloads por tipo de evento (72 tipos) y rechazo de eventos desconocidos; artifact store content-addressed con límite de tamaño; ledger de comandos con idempotencia y reglas de replay; descubrimiento de runs interrumpidos; projectors JSON/Markdown y generación de Decision Bundle |
| M3 Pact engine | `implemented_unverified` | Semántica independiente del runtime | Schema estricto v1 en Pydantic (extra=forbid, version Literal[1]); parseo YAML SafeLoader; forma canónica + SHA-256; reglas de paths compiladas con pathspec y denies implícitos (.git/, .treepact.yaml) que prevalecen siempre; validación semántica de argv: shells, `-c`, env-to-shell e inline-eval de intérpretes rechazados; límites, fases, modos, network separado runtime/checks; gates con IDs estables; actions.unavailable con aserción completa de los 10; `treepact validate` operativo con diagnóstico por campo y `--explain`/`--print-canonical` |
| M4 Workspace y tool broker | `implemented_unverified` | Sin shell ni Git expuestos al runtime | Adaptador Git con argv fijo (sin shell), rechazo de operaciones Git sin resolver (MERGE_HEAD etc.), worktree detached bajo datos de TreePact con lock por run y cleanup verificado bajo storage; PathBroker con rechazo de absolutos, `..`, NUL, symlink escape, `.git`, case-folding y paths confusables; superficie tipada list_files/read_file/search_text/apply_patch/run_check/finish con proposals, decisiones policy.allowed/denied y executions registradas; apply_patch con git apply --check, rechazo de mode changes, submodules y paths protegidos; entorno hijo allowlist + HOME temporal sin SSH_AUTH_SOCK ni secretos; process groups con timeout/cancelación y truncado explícito; snapshot de inputs de checks (argv relativos + config de build) con bloqueo blocked_input_changed ante drift; dedup de artifacts content-addressed; `treepact init` con borrador conservador validable |
| M5 Runtime nativo y local gateway | `implemented_unverified` | Loop acotado; sin decisión final del modelo | Ports ModelProvider/RuntimeAdapter; provider OpenAI-compatible no-streaming con HTTPX, endpoint loopback obligatorio (rechaza egress), errores mapeados y sin fallback silencioso; adapter local gateway con perfiles classify/fast-code/deep-code (IDs de modelo solo en config); loop nativo tipado con system prompt de jerarquía de autoridad + contenido no confiable, tool calls estructurados, observaciones, límites de turns/tiempo/contexto/tokens, malformed tool calls fail-closed; run engine con transiciones, snapshot inmutable del Pact, workspace, attempts (máx. 3 del Pact), cancelación; `provider status` operativo |
| M6 Recursos y gates | `implemented_unverified` | Un run mutante; gates por hechos capturados | ResourceScheduler local: un run mutante, un lease grande, presión de memoria (psutil) con zona critical/elevated, reservas de macOS, waiting_resource sin consumir attempts, resource.queued/denied/granted/activated; 5 gates evaluados desde hechos (required_checks_pass sin checks no-terminales, no_denied_paths_changed, no_secrets_in_diff, worktree_consistent con digest de árbol reconciliado, evidence_complete con cadena y artifacts); decisión accepted/rejected/needs_review; checks finales requeridos en verifying con handling de modos no permitidos; `treepact run` completo con exit codes 0/15/16/17/20; bundle generado en runs/<id>/report.json |
| M7 Operación y recuperación | `implemented_unverified` | Sin reanudación automática ante efectos inciertos | `status`/`runs`/`projects`/`logs` (redactados, `--events`)/`diff` (`--output` registra evidence.exported)/`evidence` (`--verify-hashes`)/`cancel` (archivo .cancel + SIGTERM al propietario, cancelación cooperativa del engine y del process group del check activo, exit 18)/`resume` (verificación estricta: identidad, base, worktree, Pact, lock, efectos inciertos; rechazo → needs_review, nunca adivina)/`cleanup` (verifica storage, preserva evidencia, --force con advertencia)/`verify` (recalcula gates sin rerun de checks; --check-artifacts/--check-events); recuperación de locks stale; terminación limitada a process groups registrados; retención de temp homes; `doctor` con descubrimiento de interrupciones y locks; runbook de recuperación en docs/operations/ |
| M8 Primer runtime externo (OpenCode) | `implemented_unverified` | Mismo Pact en native y externo | Adaptador OpenCode 1.18.15 verificado contra API real (OpenAPI + SSE): servidor dedicado loopback con password efímero (OPENCODE_SERVER_PASSWORD + Basic auth), puerto aleatorio, mDNS off, CORS vacío; aislamiento completo XDG_CONFIG/CACHE/DATA/STATE por run (nunca toca el storage real del operador — verificado); config generada con solo el provider loopback y tools de bypass deshabilitadas (bash/write/edit/patch/web/http/task/subagents); preflight con inventario de providers (solo treepact-local + opencode interno), skills vacías, sin plugins, sin drift de config; descubrimiento verificado: el loop de agente solo corre con el stream global /api/event suscrito; prompt delivery steer; eventos SSE traducidos a eventos TreePact; cancelación vía /interrupt; reconciliación Git independiente; assurance TP2 honesta (ADR 0017: plugin TS fijado como fuente, carga requiere bun → M9+); `run --runtime opencode` con version check y runtime_incompatible exit 15 |
| M9 Verificación consolidada | `verified` | Campaña única; go/narrow/no-go | Candidato congelado (digest árbol + uv.lock, sin Git por restricción no-commit); dependencias de test añadidas (pytest, hypothesis, coverage, ruff, mypy, pip-audit); 170 tests (unit 105, property 8, integración 18, seguridad 16, recuperación 12, recursos 5, evals 6); Stage 1 estático (ruff, mypy, pip-audit, lock --check, compileall, búsqueda de APIs prohibidas, inventario de migraciones, schemas), Stages 2-7 pytest, Stage 8 comparación native vs OpenCode (mismo pact, mismos gates, misma decisión), Stage 9 auditoría (54 filas de trazabilidad requirement→risk→test, scan de secretos, consistencia de candidato); 8 defectos reales corregidos (D-001..D-008) con fallos iniciales preservados en campaign_report_initial.json; DEFECT_REGISTER.md; informe final con veredicto **go** para piloto M10 |

## Post-M9 additions

- **v0.2.0 read-only review contract:** `treepact review` projects run summaries and
  evidence metadata through a strict schema-versioned JSON document. It opens the
  existing SQLite store read-only, validates the schema, and never recalculates
  gates, runs checks, generates bundles, or writes files.
- **Current source verification:** 183 project tests pass with `uv run pytest tests`,
  plus ruff and mypy. This is an integration addition, not a new consolidated M9
  campaign or a new M10 pilot decision.

## Decisiones de implementación

Pendiente de registro conforme se tomen. Toda desviación de M0 se documenta en un ADR nuevo que supersede, nunca reescribiendo la historia.

## Bloqueos

- Ninguno conocido al inicio de M1.

## Siguiente acción

M10: piloto interno en Python, Swift, KMP y Kotlin/Compose (30 runs reales, 6 semanas). Requiere decisión del operador. Paquete M10 listo: `examples/pacts/` (4 toolchains) y `scripts/pilot_metrics.py`.

## Handoff

`HANDOFF.md` en la raíz: estado completo, decisiones tomadas y pendientes, limitaciones honestas, reglas de trabajo para cualquier agente futuro.

## Resumen final

- **Candidato**: `8724421aa2fadfa2` (frozen 2026-08-11, digest árbol fuente + uv.lock; sin Git commit por restricción no-commit).
- **Campaña**: 9/9 stages PASS. Ver **verification/FINAL_REPORT.md**, **verification/DEFECT_REGISTER.md**, **verification/campaign_report_initial.json** (fallos iniciales preservados).
- **Veredicto**: **go** para piloto interno M10, con limitaciones honestas (EVAL-001..004 sintéticos; OpenCode TP2 por ADR 0017; lane nativa trusted_harness).
