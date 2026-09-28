# Punto de control GitHub del relevo

## Estado comprobado

El 24/09/2026 se consulto el repositorio remoto mediante la API autenticada:
`Jose-BF/telegram-signal-copier` es **publico**, su rama por defecto es
`master` y la cuenta dispone de permiso de escritura. La rama local actual
es `feature/gold-555-live-trial`, con upstream `origin/main` y numerosos
cambios pendientes. No son tres nombres intercambiables.

El codigo local `tools/run_bot_watch.py` consulta `origin/main` y
`tools/git_sync.py` usa `main` para sincronizacion. Esto no constituye una
comprobacion nueva del watcher instalado en la VM ni de su estado operativo.

Con autorizacion explicita del usuario se ha creado y publicado un repositorio
**privado separado**:
`https://github.com/Jose-BF/telegram-signal-copier-migration-checkpoint`.
No se ha cambiado el remoto, la rama ni el HEAD del proyecto de trabajo.
Los commits del respaldo pertenecen a un repositorio independiente, no al
almacen Git compartido del bot. No se ha publicado en el remoto de produccion.

Rama del respaldo: `checkpoint/pre-claude-2026-09-24`.
Etiqueta de cierre: `pre-claude-2026-09-24` (no moverla para incluir trabajo futuro).
El primer commit de contenido publicado y descargado para verificacion fue
`56d458baefd41da64d64f15e93e5597fbb40cf6b`. El cierre anade documentacion
del resultado a esa instantanea; la etiqueta identifica el punto final.

## Contenido y verificaciones

- 1.157 archivos del proyecto, aproximadamente 45 MB, bajo `project/`.
- Codigo, pruebas, herramientas, documentacion y los informes JSON derivados
  de `reports/expanded-native-controls-20260924/`, incluidos los cambios sin commit.
- `CHECKPOINT-MANIFEST.json` en la raiz del respaldo conserva procedencia,
  estado Git del origen, bytes, hashes SHA256 y exclusiones. No normalizar
  finales de linea ni interpretar el HEAD original como todo el contenido.
- Gitleaks 8.30.1 no detecto secretos en el contenido preparado ni en el
  primer commit completo. Esta comprobacion no garantiza ausencia absoluta.
- Se clono el repositorio desde GitHub en otra carpeta y se comprobaron todos
  los hashes del manifiesto, numero de archivos y `git fsck` con resultado correcto.
- GitHub Actions desactivado; cero webhooks en el repositorio nuevo.
- Antes de cerrar se repite la comprobacion para el commit final y su etiqueta.
  El certificado externo final se guarda en
  `A:\ProjectRestorePoints\telegram-signal-copier\github-checkpoint-20260924\GITHUB-CHECKPOINT-VERIFIED.json`.

No subir `.env`, sesiones de Telegram/MT5, claves, credenciales, entornos
virtuales ni la copia congelada. Se excluyen `data/`, los datos ignorados por
Git, salidas de test generadas y los demas informes historicos. Los 233 archivos
visibles para Git excluidos constan en el manifiesto; los ignorados no se
exportan. El historico Git original no se ha subido: se conserva una instantanea
nueva para no arrastrar informacion de commits antiguos.

El respaldo completo verificado es
`A:\ProjectRestorePoints\telegram-signal-copier\pre-claude-20260924-v3`.
Contiene los archivos locales que una copia de codigo puede excluir.
Un repositorio privado no sustituye ese respaldo ni garantiza reproducir
analisis que dependan de entradas no incluidas. Tampoco protege frente a perder
el disco A:; una copia completa externa exigiria un destino y proteccion
adecuados para datos sensibles, no publicar el archivo bruto en GitHub.

## Uso y limites

Claude continua en la carpeta de trabajo original, no en el respaldo de A:
ni en el clon de comprobacion. El repositorio privado es un segundo respaldo
de codigo/contexto, no una imagen completa del proyecto ni una version validada
para operar. Una restauracion desde GitHub necesita recuperar por separado las
entradas privadas para reproducir todos los analisis. No iniciar el bot desde
el archivo ni configurar un watcher para el.

No modificar `origin`, `main` ni el watcher del proyecto actual por esta tarea.
La copia congelada permanece intacta. No se ha vuelto a comprobar el estado
operativo de la VM ni se ha desplegado codigo con este respaldo.
