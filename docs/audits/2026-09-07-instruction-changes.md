# Correccion de instrucciones - 2026-09-07

Alcance autorizado: aplicar la auditoria de habilidades y AGENTS.md. No cambiar
codigo de trading, configuracion activa, datos, VM, permisos ni despliegues.

## Plan y estado

1. Completado: respaldo verificado de los 28 SKILL.md personales y de los tres
   documentos principales que se van a reorganizar. Guardado en
   `C:/Users/josea/.codex/backups/instructions-20260907-100217/`.
2. Completado: corregir habilidades personales, preservando recursos auxiliares
   utiles; no editar habilidades de sistema ni caches de plugins.
3. Completado: separar el EA historico y organizar las instrucciones del bot
   mediante referencias de alcance definido.
4. Completado: validar estructura, enlaces, preservacion del trabajo previo y
   escenarios de decisiones sin efectos externos.

## Criterios de aceptacion

- Una consulta o errata no dispara un proceso de diseno y revision completo.
- Un defecto financiero conserva su caso de regresion, conciliacion y pruebas
  de integracion pertinentes; no se relajan controles del codigo.
- Retomar trabajo no borra implementaciones ni invalida evidencia sin cambio.
- Las simulaciones reutilizan los motores del proyecto y distinguen ejecucion
  observada, decisiones y ejecuciones hipoteticas.
- Ninguna receta muestra secretos ni autoriza por si sola borrar o desplegar.
- Codigo local, push y version operativa se informan por separado.

## Resultado

- Corregidos 24 SKILL.md personales. Conservados sin cambios los de composicion
  React, buenas practicas React, React Native y revision de diseno web, que ya
  tenian un alcance razonablemente especifico. No se han desinstalado habilidades.
- Los 24 archivos principales corregidos pasan de 214.418 a 29.442 bytes: un
  86,3% menos de texto de entrada. No es una medicion ni una promesa de ahorro
  equivalente en tokens. Tres recetarios utiles se conservan como referencias
  opcionales y los originales completos estan respaldados.
- AGENTS.md del bot pasa de 213 a 63 lineas. Los contratos extensos se conservan
  en docs/development/runtime-and-replay.md, research-contracts.md y
  shadow-evidence.md. No se han relajado ni cambiado gates del codigo.
- La raiz ahora identifica los proyectos por separado. CLAUDE.md remite a esa
  fuente comun; el contexto del EA queda en docs/legacy/xauusd-momentum-ea.md.
- Las recetas ya no imprimen secretos ni mandan borrar implementaciones por
  haberlas escrito antes de una prueba. Se distinguen reconstruccion contable,
  decisiones de estrategia y ejecuciones hipoteticas.
- No se han editado copias Git historicas, habilidades de sistema, configuracion
  del modelo, plugins ni archivos del bot fuera de sus instrucciones/documentos.

## Comprobaciones

- Los 28 SKILL.md personales pasan el validador oficial quick_validate.py.
- Validacion ejecutada con Python en UTF-8; el primer intento encontro la
  codificacion predeterminada cp1252 de Windows, no un error de las habilidades.
- PyYAML 6.0.3 se instalo solo en validation-deps dentro de la copia de seguridad
  para ejecutar ese validador. No se modificaron dependencias del proyecto.
- Comprobados los siete enlaces Markdown locales de los 34 archivos principales
  y referencias modificados/agregados. Se corrigieron tres enlaces relativos al
  trasladar recetarios. No quedan destinos inexistentes en esa comprobacion.
- Verificados por SHA-256 los 16 archivos previos de codigo/pruebas/documentacion
  del bot distintos de AGENTS.md: todos intactos. Tambien los 60 archivos
  encontrados bajo habilidades de sistema permanecen intactos.
- Una revision independiente de siete escenarios no encontro defectos pendientes
  en las decisiones descritas: consulta simple, fallo de dinero con reintentos,
  reanudacion, simulacion con otras entradas, token no seguro, push con posible
  reinicio y ajuste pequeno de interfaz. Fue una revision de decisiones sin
  efectos externos, no una prueba del bot ni una medicion de ahorro de tokens.

## Publicacion y alcance pendiente

Sin commit, sin push, sin despliegue ni conexion a la VM en esta tarea.
Los cambios anteriores del bot siguen pendientes y deben retomarse como un
trabajo separado, contrastando sus gates con los contratos ahora aclarados.
Los metadatos ya cargados al inicio de una conversacion pueden seguir reflejando
las descripciones antiguas; estos archivos actualizados son la fuente para su
proxima carga. No se ha forzado ningun reinicio de Codex ni del bot.

Una validacion documental no certifica la rentabilidad, simulaciones ni operativa.
