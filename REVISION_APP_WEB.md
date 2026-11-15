# Revisión — App web (Streamlit) vs Bot de Telegram

Comparación de `app.py` + sus módulos contra `telegram_bot.py` + `workflow.py` + `run_bot.py`.
Fecha: 2026-11-15. **No se modificó ningún archivo**: esto es solo el diagnóstico.

Los 17 módulos de la web importan sin errores. Lo que está desactualizado no es que
se rompa al abrirlo, sino que **la web quedó en la versión vieja del pipeline**: todo lo
que se agregó en los últimos meses (ACRCloud, gancho, recorte por tramo, títulos con
historial, efemérides) vive solo en `workflow.py`, que es el camino del bot.

---

## 1. Módulo por módulo

| Módulo de la web | Entrada | ¿Existe en el bot? | Estado |
|---|---|---|---|
| 📱 Feed de TikTok | `tiktok_feed.mostrar_tiktok_feed` | Parcial — `/trabajar` y `/urls` | **Desactualizado.** Comparte `_buscar_keywords` con el bot, pero ignora `keywords_disponibles()` (no avisa si Cloudflare bloqueó la búsqueda) y tiene su propio downloader y su propio compilador en paralelo. |
| 🎞️ Compilador | `compilador.mostrar_compilador` | Sí — `workflow.compilar_y_generar_seo` | **Desactualizado.** Comparte `concatenar_clips`, `agregar_overlay_suscripcion` y `marca_agua` con el bot, pero le faltan ACRCloud, recorte por tramo y gancho. Orden manual con flechas + `sugerir_orden_claude`, que el bot no tiene. |
| 🖼️ Generador de Thumbnail | `thumbnail_gen.mostrar_thumbnail_gen` | Sí — `generar_thumbnail_automatico` | **Al día.** Misma función en ambos lados; la web agrega control manual del texto. |
| 🎵 Narración Sincronizada | `sync_audio.mostrar_sync_audio` | No | Solo web. Funciona. |
| 🎬 Editor de Video | `video_editor.mostrar_video_editor` | No | Solo web. Funciona. |
| 📋 Generador de SEO | `seo_gen.mostrar_seo_gen` | Sí — `generar_seo` | **Desactualizado.** Llama a `generar_seo()` sin `candidatos_titulo`, así que no pide los 3 candidatos, no compara contra el historial y **no aplica la prohibición de muertes/lesiones/salud/noticias**. Sigue mostrando las 4 `variaciones` viejas de ≤45 chars con emoji + MAYÚSCULAS. |
| 🚀 Subida a YouTube | `uploader.mostrar_subidor` | Sí — `workflow.subir_a_youtube` | **Desactualizado en la doc, funcional en el código.** Ver §2 (auth y nombres de canal). |
| ✂️ Cortador de Streams | `clip_splitter.mostrar_clip_splitter` | No | Solo web. Funciona. |
| 🎛️ Gestión de Videos | `video_manager.mostrar_video_manager` | Parcial — `/stats` | La web lista los videos con vistas/likes y permite cambiar privacidad; `/stats` solo muestra los últimos 10 en modo lectura. Los dos funcionan. |
| 📅 Calendario Viral | `calendario.mostrar_calendario` | No, pero **se pisa con efemérides** | Funciona, aunque hoy hay dos calendarios de fechas de fútbol sin relación entre sí. Ver §4. |
| 📋 Historial | `historial.mostrar_historial` | Indirecto | **Al día y ahora compartido.** Desde el cambio de ayer el bot también escribe en `youtube_videos.json`. |
| 📊 Analizador de Canal | `analyzer.mostrar_analizador` | No | Solo web. Funciona. |
| 🔥 Tendencias Virales | `trends.mostrar_tendencias` | No | Solo web. Funciona. |
| ✍️ Generador de Contenido | `content_gen.mostrar_generador_contenido` | No | Solo web. Funciona. |
| 🎙️ Generador de Voz | `voice_gen.mostrar_generador_voz` | Parcial — `/narracion`, `/uso` | La web es ElevenLabs manual; el bot arma el reel completo con `narracion_reel.py`. Los dos funcionan. |
| ⬇️ Descargador de Videos | `downloader.mostrar_descargador` | No | Solo web (yt-dlp, YouTube + TikTok). Funciona. |
| 🎯 Proceso Guiado | `proceso.mostrar_proceso` | No | **Roto en el paso 5.** Ver §2. |

---

## 2. Los ejes que cambiamos hace poco

### Autenticación de YouTube — **divergente, y un camino roto**

Hay **cinco copias** de la carga de credenciales, cada una escrita a mano:

| Dónde | Ruta del token | Scopes |
|---|---|---|
| `uploader.py:51` (web) | `"token_youtube.json"` — **relativa al cwd** | 3 (`SCOPES`) |
| `workflow.py:544` (`subir_a_youtube`) | absoluta vía `__file__` | 3 |
| `workflow.py:640` (`postear_comentario_encuesta`) | absoluta | 3 |
| `telegram_bot.py:974` | absoluta | 3 |
| `telegram_bot.py:1077` (`/stats`) | absoluta | **2 — le falta `force-ssl`** |

La de `uploader.py` es la única con ruta relativa: si algún día lanzás Streamlit desde
otra carpeta, la web no encuentra el token mientras el bot sí.

Lo más importante: **`proceso.py` (Proceso Guiado) usa el flujo OOB, que Google dio de
baja.** `generar_url_auth()` (`uploader.py:64`) pide `redirect_uri="urn:ietf:wg:oauth:2.0:oob"`,
y `proceso.py:578` y `:590` son los únicos que lo llaman. Ese "generá un link, pegá el
código" ya no funciona para autenticar de cero: el paso 5 solo anda si el token ya existe
porque lo creaste desde el Subidor o desde `autenticar_youtube.py`.

El Subidor, en cambio, está bien: el botón de `uploader.py:452` llama a
`autenticar_youtube_local()`, que abre el navegador y vuelve solo. Mismo camino que
`autenticar_youtube.py`.

### Copyright con ACRCloud — **solo en el bot**

`copyright_check.revisar_y_limpiar_clips` se importa en un único lugar de todo el repo:
`workflow.py:21`. Ni el Compilador ni el Feed de TikTok lo llaman.

En su lugar la web tiene `tiktok_feed._procesar_anticopyright` (`:239`), que es otra
cosa: no identifica la canción, sino que aplica mirror + zoom 1.1x + speed 1.07x y pisa
el audio. **No se reemplazan entre sí** — ACRCloud detecta y silencia el tramo con música
registrada; el otro deforma el video entero. El bot hace los dos; la web solo el segundo.

### Música de `Songs/` — **al día**

`music_manager` es compartido de verdad. El Compilador (`compilador.py:676`) y el Feed
(`tiktok_feed.py:750`) usan `listar_tracks` y `mezclar_musica`, igual que el bot. La
diferencia es de criterio, no de código: el autopilot elige al azar y el reel de las 19:00
busca una pista tranquila con `_PISTAS_TRANQUILAS` cuando la efeméride es respetuosa.
En la web elegís vos a mano.

### Gancho en pantalla — **solo en el bot**

`_generar_texto_gancho` y `_quemar_gancho` viven en `workflow.py` y los usa únicamente
`_compilar_reel_autopilot`. No hay equivalente en la web. Tampoco está el fallback a
`ffmpeg-full` para `drawtext`.

### Recorte de clips a su mejor tramo — **solo en el bot**

`_recortar_clips_autopilot` y `_elegir_tramo_clip` (`workflow.py:715` y `:800`) son del
autopilot. El Compilador web corta por duración fija y nada más. `_ordenar_clips_autopilot`
(orden por impacto con Claude) tampoco está; la web tiene `sugerir_orden_claude`, que es
otra función con otro prompt y decide sobre los nombres de archivo, no sobre el contenido.

### Títulos con historial — **solo en el autopilot**

Lo de ayer quedó detrás de `candidatos_titulo=True`, que solo pasa `ejecutar_autopilot_pipeline`.
`mostrar_seo_gen()` no lo activa, así que en la web seguís viendo el título viejo y las 4
variaciones. Esto incluye la regla de seguridad: **el SEO de la web puede generar títulos
sobre muertes o lesiones, el del autopilot no.**

⚠️ **Efecto colateral del cambio de ayer que conviene saber:** `bloque_video_anterior()`
(`historial.py`) se agrega a la descripción tanto en `uploader.py:718` como en
`workflow.py:559`, y solo devuelve algo si el último video subido tiene menos de 7 días.
Hasta ayer el historial terminaba en julio, así que el bot nunca agregaba ese bloque.
Ahora que el camino headless registra los títulos, **a partir del segundo reel el bot va a
empezar a linkear el reel anterior en la descripción**. Probablemente es lo que querés,
pero es un cambio de comportamiento que no estaba pedido explícitamente.

### Efemérides — **solo en el bot, sin pisarse**

`efemerides.py` lo usan `workflow.ejecutar_reel_del_dia_pipeline` y `telegram_bot`. La web
no lo toca. Está limpio.

### Nombres del canal — **una referencia obsoleta**

`uploader.py:398` todavía dice:

> Abrí [youtube.com](https://youtube.com) con `viralvideosinvestigate@gmail.com`

Ese correo ya no es el que usás, y **el mismo archivo se contradice**: las líneas 301, 441
y 443 dicen `agumarini94@gmail.com` → cuenta de marca **SunsetVideos** → canal
**viralesDelFutbol**, que es el setup correcto.

Además, las instrucciones de `uploader.py:403-427` ("Generá un nuevo link de autorización…
Pegá el código acá") describen el flujo OOB que el botón ya no usa. El resto de las
menciones al canal (`calendario.py:366`, `trends.py:238`, `seo_gen.py:350`) están bien.

---

## 3. Solo en la app web

| Función | Qué hace | ¿Anda? |
|---|---|---|
| `analyzer.mostrar_analizador` | Estadísticas del canal vía YouTube API + análisis de Claude | Sí |
| `trends.mostrar_tendencias` | Busca virales en YouTube y TikTok y los rankea con Claude | Sí |
| `content_gen.mostrar_generador_contenido` | Guión + título + descripción a partir de un video de referencia | Sí |
| `voice_gen.mostrar_generador_voz` | Texto a MP3 con ElevenLabs, con preview de voces | Sí |
| `downloader.mostrar_descargador` | Descarga de YouTube/TikTok con yt-dlp y elección de calidad | Sí |
| `sync_audio.mostrar_sync_audio` | Detecta cortes de escena y escribe narración proporcional a cada clip | Sí |
| `video_editor.mostrar_video_editor` | Pipeline de edición (recorte, velocidad, filtros, texto) en una pasada | Sí |
| `clip_splitter.mostrar_clip_splitter` | Parte un stream largo en clips | Sí |
| `video_manager.mostrar_video_manager` | Lista los videos del canal y cambia privacidad en lote | Sí |
| `calendario.mostrar_calendario` | Calendario de eventos virales de fútbol sugeridos por Claude | Sí |
| `compilador.sugerir_orden_claude` | Propone el orden de los clips por los nombres de archivo | Sí |
| `tiktok_feed._mostrar_versus` | Versus de 2 lados: busca dos términos y los alterna A/B | Sí, pero ver abajo |
| `proceso.mostrar_proceso` | Asistente de 5 pasos que encadena Tendencias → Contenido → Descarga → Voz → Subida | **Paso 5 roto** (OOB) |

El Versus de la web y el del bot no son el mismo: la web solo hace 2 lados, ordena por
vistas y no pasa por Claude ni por el filtro de seguidores; el bot soporta N lados, usa
`_claude_seleccionar` + `filtrar_por_seguidores` y mete un flash rojo de transición entre
bloques (`compilador.generar_clip_transicion_vs`). El de la web además no aplica marca de
agua ni campanita de suscripción.

---

## 4. Código muerto y duplicado

### Muerto — cero llamadores

| Qué | Dónde | Nota |
|---|---|---|
| `generate_thumbnail()` + `_THUMB_W/_THUMB_H/_FUENTES_IMPACT/_fuente_impact` | `thumbnail_gen.py:293-370` | Segunda implementación de thumbnail, 1280×720 con fuente Impact. Nadie la llama; la viva es `generar_thumbnail_automatico` → `aplicar_texto`. |
| `buscar_y_seleccionar()` | `selector_ia.py:1191` | Reemplazada por `buscar_hashtags` + `seleccionar_con_claude` por separado. |
| `obtener_canal_activo()` | `uploader.py:131` | Reemplazada por `obtener_todos_los_canales` + el selector del Subidor. |
| `ejecutar_workflow_automatico()` | `telegram_bot.py:5389` | El scheduler que la usaba está comentado en `run_bot.py:152-162` desde el 2026-09-27. |
| `HORA_AUTO` / `MINUTO_AUTO` | `run_bot.py:53-54` | Solo las lee el job comentado. Siguen documentadas en `.env.example`. |

### Duplicado — dos implementaciones con lógica distinta

| Qué | Web | Bot | Diferencia real |
|---|---|---|---|
| Anti-copyright por transformación | `tiktok_feed._procesar_anticopyright:239` | `workflow._aplicar_anticopyright:214` | Mismo filtro `hflip,scale,crop,setpts`. El bot usa 192k de audio y sabe quedarse en silencio si no hay música; la web usa 128k y **exige** una pista. |
| Carga de credenciales OAuth | `uploader.py:51` | `workflow.py:544`, `:640`, `telegram_bot.py:974`, `:1077` | 5 copias. Ruta relativa vs absoluta, y `/stats` pide 2 scopes en vez de 3. |
| Normalizar + concatenar con ffmpeg | `tiktok_feed._run_ffmpeg:192`, `_normalizar_clip:202`, `_concat_lista:226` | `compilador.run_ffmpeg:26`, `normalizar_video:50`, `concatenar_clips:101` | La versión de `tiktok_feed` no hace padding a la resolución destino ni soporta `puntos_transicion`. |
| Metadata y descarga de TikTok | `tiktok_feed.obtener_info_tiktok:43`, `descargar_tiktok:126`, `_codec_compatible:86` | `selector_ia._info_por_url:718`, `_descargar_video:956`, `_codec_ok:908` | Mismo endpoint de tikwm. `selector_ia` tiene el fallback a `wmplay` y el caché de seguidores; `tiktok_feed` reporta errores con `st.warning`, lo que lo ata a Streamlit. |
| Orden de clips con Claude | `compilador.sugerir_orden_claude:164` | `workflow._ordenar_clips_autopilot:861` | La web decide por nombre de archivo; el bot por contenido del clip. |
| Calendario de fechas de fútbol | `calendario.py` (Claude genera eventos) | `data/efemerides.json` (dataset fijo) | Dos fuentes de verdad para lo mismo. La del bot es la confiable; la de la web inventa fechas. |

### Inconsistencias chicas de rutas

- `historial.py:13` → `DATA_DIR = Path("data")` es **relativa**. Anda porque `start_bot.sh`
  hace `cd` al directorio del script, pero el resto del repo usa `Path(__file__).parent.parent / "data"`.
- `eleccion_reel.py:23` guarda `eleccion_historial.json` en la **raíz del proyecto**, no en
  `data/`, a diferencia de todos los demás historiales.

### Transversal, no es web vs bot

Los modelos de Claude están parejos entre web y bot, pero son de la generación anterior:
`claude-opus-4-6` (8 módulos), `claude-sonnet-4-6` (5) y `claude-haiku-4-5` (4). Hoy
existen Opus 5 y Sonnet 5. No está roto, pero si vas a tocar prompts conviene revisarlo
de una vez.

---

## 5. Recomendación

### Arreglar ahora (son errores, no mejoras)

1. **`uploader.py:398`** — cambiar `viralvideosinvestigate@gmail.com` por
   `agumarini94@gmail.com` + SunsetVideos. Hoy el archivo se contradice solo y te manda a
   una cuenta que no existe más.
2. **`uploader.py:403-427`** — reescribir las instrucciones de "pegá el código": describen
   un flujo que el botón ya no usa.
3. **Proceso Guiado, paso 5** — reemplazar `generar_url_auth` + `completar_auth_con_codigo`
   por `autenticar_youtube_local()`, que es lo que ya usa el Subidor. Son ~15 líneas en
   `proceso.py:573-594`. Con eso las dos funciones OOB de `uploader.py` quedan muertas y se
   pueden borrar.
4. **`/stats`** (`telegram_bot.py:1073`) — agregarle `force-ssl` para que use los mismos 3
   scopes que el resto. Hoy no molesta porque solo lee, pero es una bomba de tiempo si
   alguien le agrega una escritura.

### Unificar (una función, un lugar)

5. **Centralizar la carga de credenciales.** Un solo `obtener_credenciales()` sin Streamlit
   —en `uploader.py` o en un módulo nuevo— con la ruta absoluta y los 3 scopes, y que las 5
   copias lo llamen. Es el duplicado más riesgoso: cualquier cambio de scopes hoy hay que
   acordarse de hacerlo en cinco lados.
6. **Hacer que `TOKEN_FILE` sea absoluto** en `uploader.py:35`. Cae de maduro con el punto 5.
7. **Llevar ACRCloud al Compilador web.** `revisar_y_limpiar_clips` ya recibe la lista de
   descargados y un `notify`; es una línea en el flujo del Compilador. Es lo que más
   protege de un strike y hoy la web no lo tiene.
8. **Borrar el pipeline de ffmpeg de `tiktok_feed`** (`_run_ffmpeg`, `_normalizar_clip`,
   `_concat_lista`, `_compilar_video_largo`) y usar `compilador.concatenar_clips`. De paso
   el Versus web gana padding correcto, transiciones, marca de agua y campanita.
9. **Unificar el anti-copyright**: dejar `workflow._aplicar_anticopyright` (es la más
   completa) e importarla desde `tiktok_feed`.

### Borrar

10. `thumbnail_gen.generate_thumbnail` y sus cuatro constantes (`:293-370`).
11. `selector_ia.buscar_y_seleccionar` (`:1191`).
12. `uploader.obtener_canal_activo` (`:131`).
13. `telegram_bot.ejecutar_workflow_automatico` (`:5389`) + el bloque comentado de
    `run_bot.py:152-162` + `HORA_AUTO`/`MINUTO_AUTO` y sus entradas en `.env.example`.

### Decisión tuya, no la tomo yo

14. **Proceso Guiado** — una vez arreglado el paso 5, sigue siendo un wrapper de cinco
    módulos que ya están en el menú y que apunta al flujo viejo (video de referencia + voz
    en off), no al que usás hoy. Arreglarlo cuesta poco; mantenerlo tiene sentido solo si
    lo seguís abriendo. Si no, borrar `proceso.py` saca 722 líneas y la mitad del problema
    de auth.
15. **Calendario vs efemérides** — tener Claude inventando fechas de fútbol en la web
    mientras el bot usa un dataset curado es pedir que se contradigan. O el Calendario pasa
    a leer `data/efemerides.json`, o se convierte en la pantalla para editar ese dataset
    (que hoy no existe y lo editás a mano), o se borra.
16. **El SEO de la web** — si querés que la web genere los mismos títulos que el autopilot,
    es pasarle `candidatos_titulo=True` a `generar_seo` desde `mostrar_seo_gen` y mostrar
    los 3 candidatos con su score. Lo dejé sin tocar porque dijiste explícitamente que el
    cambio era solo para el autopilot, pero la prohibición de muertes/lesiones es
    discutible que quieras tenerla solo de un lado.
