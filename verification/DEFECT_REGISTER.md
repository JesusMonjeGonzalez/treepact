# TreePact M9 Defect and Exception Register

Registro de defectos y excepciones encontrados durante el desarrollo (M1-M8,
hallados por revisión de código y ejecuciones puntuales de inspección) y
durante la campaña M9. Cada defecto preserva el fallo inicial y sus reruns
según la política de FINAL_VERIFICATION_PLAN.md.

## Defectos corregidos

| ID | Severidad | Componente | Hallazgo | Corrección | Evidencia |
|---|---|---|---|---|---|
| D-001 | Alta | adapters/git | `--git-common-dir` devuelve una ruta relativa que se resolvía contra el cwd del proceso, no contra el root del repo: MERGE_HEAD/REBASE_HEAD no se detectaban | Resolver common-dir contra el root descubierto | tests/integration test_reject_unresolved_git_operation |
| D-002 | Alta | evidence/events | `append()` no devolvía `event_sha256` en el envelope (el hash existía en DB, no en el objeto devuelto) | Devolver el envelope con digest | tests/unit test_monotonic_sequence_and_chain |
| D-003 | Media | domain/pact | La regla de path `.` (raíz) no matcheaba nada en pathspec: `readable: [.]` no exponía archivos | Regla `.` -> `**` en compilación | tests/unit test_directory_prefix_matches_children |
| D-004 | Alta | workspace/tools | Denegaciones detectadas dentro de la ejecución (paths traversal, .git, secretos, tools desconocidos) no registraban `policy.denied`; el loop nativo cortocircuitaba el broker para tools desconocidos | Registrar proposal + policy.denied siempre; todo tool call pasa por el broker | tests/evals EVAL-005/007/010, tests/security |
| D-005 | Media | storage/repository | `checks_for_run` y `leases_for_run` ordenaban por `created_at`, columna inexistente en el esquema documentado | Ordenar por rowid | tests/unit |
| D-006 | Media | domain/gates | Un check `blocked_input_changed` se clasificaba como `required_check_failed` (rejected) en vez de `check_inputs_changed` (insufficient_evidence) | Evaluar blocked antes que failed | tests/unit test_blocked_input_insufficient |
| D-007 | Media | resources/scheduler | Runs en estado `created` (no mutantes) bloqueaban el lease de otros runs | Solo cuentan estados mutantes | tests/resources |
| D-008 | Alta | Seguridad de cadena | `pip-audit` encontró PYSEC-2026-1845 en pytest 8.4.2 (dev-only) | Actualizar a pytest>=9.0.3 | verification/stage_static.json |

## Fallos de campaña preservados (reruns según política)

La primera campaña completa registró fallos en `static` (pip-audit: PYSEC-2026-1845;
búsqueda de APIs prohibidas con falsos positivos) y `runtime` (fallo ambiental del
servidor OpenCode: comparación correcta al reintentar). Ambas ejecuciones iniciales
quedan preservadas en `verification/campaign_report_initial.json`; los reruns
exitosos en los stage_*.json actuales. Política cumplida: no se re-ejecutaron
suites completas tras cada fix; solo los escenarios fallidos y sus dependencias.

## Excepciones documentadas (no defectos)

| ID | Ámbito | Razón |
|---|---|---|
| E-001 | EVAL-001..004 | Se ejecutaron contra fixtures sintéticos espejo de los oráculos (Python, Kotlin/Compose, Swift, KMP). La validación en repos reales es el piloto interno M10 (MASTER_PLAN.md M10); requiere ramas autorizadas por el operador y la restricción no-commit de la implementación lo impide en M9 |
| E-002 | OpenCode TP2 | ADR 0017: el plugin defensivo TS no se carga sin bun (instalación de toolchain prohibida antes de M9); el techo de assurance es TP2 con restricción de tools por configuración aislada |
| E-003 | Lane nativa | Los checks nativos corren en `trusted_harness`; sin claim de sandbox fuerte (SECURITY_MODEL.md) |
| E-004 | Sin Git commit de candidato | El repositorio TreePact no es un repo Git (restricción no-commit); el candidato se identifica por digest del árbol fuente + uv.lock |

## Estado de veredicto

Con todos los defectos corregidos y los stages en verde, el veredicto de la
campaña final es **go** para el piloto interno M10, con las limitaciones
honestas del informe final.
