# Punto de restauracion previo a Claude

Proyecto de trabajo:
`C:\Users\josea\OneDrive\Escritorio\proyectos claude\telegram-signal-copier-gold-live`

Copia congelada completada y verificada:
`A:\ProjectRestorePoints\telegram-signal-copier\pre-claude-20260924-v3`

El certificado registra 92.411 archivos de proyecto y 9.940.813.176 bytes,
ademas del respaldo Git. Se completo un ensayo de restauracion independiente:
`A:\ProjectRestorePoints\telegram-signal-copier\recovery-check-20260924.restoration.json`
acredita `project_bytes_verified: true` y `git_status_verified: true`.
El ensayo es una comprobacion, no la carpeta elegida para continuar.
La guia externa esta en `A:\ProjectRestorePoints\telegram-signal-copier\LEEME.md`.
El usuario tiene acceso de lectura/ejecucion a la copia congelada; no editarla.

La existencia de `COMPLETE.json` acredita la finalizacion de la copia y su
verificacion inicial. Un intento fallido conserva `INCOMPLETE` y no debe
tratarse como punto recuperable verificado. Revalidar los hashes antes de
restaurar. La copia incluye el arbol completo (tambien archivos ignorados,
datos y sesiones locales), los almacenes Git originales y un Git independiente
para recuperar este worktree sin depender de la carpeta vecina.

## Continuar y volver

- Claude trabaja en la carpeta del proyecto actual; la copia de A: permanece
  congelada. Leer `CLAUDE.md`, `AGENTS.md` y el documento de relevo.
- No se ha cambiado de rama para el relevo. Crear una rama por si solo no
  preserva el trabajo sin commit ni los archivos ignorados. El prompt completo
  esta en `2026-09-24-claude-transfer-prompt.md`.
- Si los avances convencen, Codex vuelve a abrir esa misma carpeta y revisa
  los cambios/documentacion. No hace falta restaurar nada.
- Si se quiere recuperar este punto, crear primero otra copia del trabajo
  posterior y restaurar la copia antigua en una carpeta nueva. Comparar antes
  de decidir cual usar. La herramienta rechaza destinos que ya existan.
- No usar `git reset --hard`, borrar el trabajo posterior ni restaurar el
  almacen Git compartido sobre el original. Ninguna de estas acciones es
  necesaria para recuperar la copia independiente.

## Herramienta

El script `tools/project_restore_point.ps1` acepta `Create`, `Verify` y
`Restore`. Tambien queda una copia del script junto a `COMPLETE.json`.

```powershell
powershell -NoProfile -File "A:\ProjectRestorePoints\telegram-signal-copier\pre-claude-20260924-v3\project_restore_point.ps1" -Action Verify -Snapshot "A:\ProjectRestorePoints\telegram-signal-copier\pre-claude-20260924-v3"
```

Para recuperar se usa el mismo script con `-Action Restore` y
`-Destination` apuntando a una carpeta nueva con espacio suficiente. No
arranca el bot. Comprueba contenido, rama, HEAD y estado Git, incluidos los
cambios pendientes. La salida `<destino>.restoration.json` acredita el ensayo
o restauracion verificados.

Para congelar un futuro punto de Claude: `-Action Create -Source <proyecto>
-Snapshot <otra carpeta nueva>`. Cada copia es independiente y no sobrescribe
la anterior. Crear copias mientras no haya procesos cambiando el proyecto;
los cambios detectados durante la captura hacen fallar su verificacion.

Esto restaura el proyecto local, no el estado pasado de la VM, el broker ni
operaciones reales. Los archivos de entrada que esten fuera del proyecto y
los runtimes instalados en Windows siguen en sus ubicaciones externas. Un
entorno virtual o un informe con rutas absolutas puede necesitar reubicar sus
referencias si se trabaja desde otra ruta; no se cambia la evidencia archivada
para fingir que esas rutas eran otras. La copia contiene material privado y
se guarda con permisos restringidos en este equipo; no publicarla.

El estado y alcance de un eventual checkpoint GitHub se documentan en
`2026-09-24-github-migration-checkpoint.md`. Es complementario, no una copia
remota de sesiones y datos privados. Los documentos finales del relevo se
actualizan en el proyecto despues de la captura; el respaldo v3 no se altera.
