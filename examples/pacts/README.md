# TreePact Pact templates - notas de uso

Cada plantilla es un punto de partida conservador. Antes de usarla en un
repositorio real:

1. Sustituir los placeholders `{project_id}`, `{project}`, `{Scheme}`.
2. Ajustar `readable`/`writable` a la estructura real del repo.
3. Verificar que cada check funciona desde un worktree limpio
   (`git worktree add --detach` o simplemente `treepact validate`).
4. Recordar: TreePact nunca ejecuta shell, git ni comandos de publicacion;
   los checks son argv fijo. Un check que necesite `&&` o `|` debe
   descomponerse en checks separados o en un script del repo (que quedara
   protegido por el snapshot de inputs).
5. El network de checks es `denied` por defecto; builds que requieran
   descargas remotas (Gradle sin cache) fallaran explicitamente y el run
   terminara rejected/needs_review con la evidencia del fallo. Para el
   piloto, usar caches locales u `--offline`.

## Reglas de oro

- `attempts` maximo 3 (schema v1).
- `readable: [.]` permite leer todo el repo (menos denies implicitos:
  `.git/` y `.treepact.yaml`).
- Los archivos de config de build (pyproject.toml, package.json,
  build.gradle.kts, etc.) y los scripts de los checks son inputs
  protegidos: el agente no puede modificarlos (deny en apply_patch) y su
  drift bloquea la ejecucion.
- `gates` completos: los cinco gates son el default recomendado.
