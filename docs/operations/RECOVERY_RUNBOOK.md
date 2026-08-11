# TreePact Recovery Runbook

Runbook de recuperación para operadores. La recuperación nunca asume que un
efecto incierto tuvo éxito; ante duda, se preserva la evidencia y se crea un
run nuevo.

## Principios

1. Nunca editar SQLite manualmente. El estado se modifica solo por comandos.
2. La evidencia (bundles, eventos, artifacts) se preserva siempre; el
   `cleanup` solo elimina el worktree y transitorios.
3. Un efecto incierto bloquea la reanudación automática.
4. Un run terminal (accepted/rejected/needs_review/cancelled/failed/
   infrastructure_error) no se reanuda nunca.

## Runs interrumpidos

Un run cuyo proceso propietario ya no existe se marca `interrupted`
automáticamente en el siguiente comando (`status`, `doctor`, etc.).

Verificar:

```bash
treepact status <run-id>
treepact logs <run-id> --events
```

Decisión:

- Si la causa fue un crash del terminal/proceso y el worktree sigue
  consistente: `treepact resume <run-id> [--wait-for-resources]`.
- Si `resume` rechaza, lee el motivo (estado, worktree, base, Pact, lock,
  efectos inciertos), preserva la evidencia y crea un run nuevo.

`resume` verifica antes de continuar:

- identidad del repositorio;
- disponibilidad del base commit;
- presencia y consistencia del worktree;
- snapshot del Pact disponible;
- sin proceso propietario vivo;
- sin efectos inciertos (tool execution sin estado terminal).

Cualquier fallo termina el run en `needs_review`, nunca adivina.

## Cancelación

```bash
treepact cancel <run-id> [--reason "motivo"]
```

- Escribe la petición de cancelación y avisa al proceso propietario.
- Termina solo los process groups registrados del run; nunca busca procesos
  por nombre.
- Un run cancelado es terminal; no se reanuda.

## Locks stale

Los lock files bajo `<data-dir>/locks/` cuyo PID propietario está muerto se
recuperan automáticamente al inicio de cada comando.

Si un lock permanece a pesar de un propietario muerto, inspeccionar:

```bash
cat <data-dir>/locks/<run-id>.lock
```

y, solo tras confirmar que el PID no existe, el siguiente comando lo limpia.

## Worktree dañado o mutado externamente

- `treepact diff <run-id>` muestra el patch frente al base.
- Una mutación externa se detecta en la reconciliación final; el gate
  `worktree_consistent` falla o el run termina `needs_review`.
- Opciones: revisar el diff, descartar el worktree con
  `treepact cleanup <run-id> --force` (con advertencia explícita), y crear un
  run nuevo.

## Check bloqueado por cambio de inputs

El snapshot de inputs (scripts de checks y configuración de build) se captura
al crear el workspace. Si el agente los modificó, la ejecución se bloquea y el
check queda `blocked_input_changed`:

```bash
treepact diff <run-id>
treepact evidence <run-id>
```

Revisa el cambio manualmente; un run nuevo solo se crea tras revisar el
script.

## Fallo del proveedor de modelo

- `treepact provider status` muestra salud sin enviar contenido del repo.
- Un run que requiere inferencia con Hearthia caído falla explícitamente
  (exit 15) y queda `failed`; nunca hay fallback remoto silencioso.
- Observa/validación/evidence siguen funcionando sin proveedor.

## Presión de memoria

- `treepact doctor` muestra la zona de presión.
- Un run espera en `waiting_resource` (con `--wait-for-resources`) o falla
  explícitamente (exit 14).
- Esperar recursos no consume un attempt.

## Fallo del bundle (storage/evidencia)

Si la generación del Decision Bundle falla tras calcular gates, el run termina
`infrastructure_error` con exit 20 y el resultado calculado queda como estado
provisional de diagnóstico. Inspeccionar:

```bash
treepact status <run-id>
treepact logs <run-id> --events
```

## Base de datos corrupta o copia

- `doctor` y `evidence --verify-hashes` detectan corrupción de cadena de
  eventos y artifacts.
- La restauración usa el contrato de backup/restore de
  STATE_AND_DATA_MODEL.md: solo en un data root vacío, tras verificar
  manifest, schema, cabeceras de cadena, conteos, tamaños y digests.
- Nunca se fusiona una restauración con estado vivo.
