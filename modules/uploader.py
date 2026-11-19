"""
Módulo 5 — Subida Automática a YouTube
Usa YouTube Data API v3 con OAuth2 para subir videos,
configurar metadata y programar publicación.
"""

import streamlit as st
import os
import json
import datetime
import tempfile
from pathlib import Path

# Importaciones de Google OAuth2 y YouTube API
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    GOOGLE_LIBS_OK = True
except ImportError:
    GOOGLE_LIBS_OK = False

from modules import youtube_auth


# Scopes y ruta del token viven en modules/youtube_auth.py — acá solo se reexportan
# para los que ya los importaban desde este módulo (autenticar_youtube.py, video_manager.py).
SCOPES = youtube_auth.SCOPES
TOKEN_FILE = youtube_auth.TOKEN_FILE


def verificar_dependencias() -> bool:
    """Verifica que las librerías de Google estén instaladas."""
    if not GOOGLE_LIBS_OK:
        st.error("❌ Librerías de Google no instaladas. Ejecuta: pip install google-api-python-client google-auth-oauthlib")
        return False
    return True


def obtener_credenciales_guardadas() -> object | None:
    """
    Carga el token guardado si existe y es válido, refrescándolo si hace falta.
    Devuelve None ante cualquier problema: la pantalla ofrece reautenticar.
    """
    try:
        return youtube_auth.cargar_credenciales()
    except Exception:
        return None


def autenticar_youtube_local() -> object | None:
    """
    Abre el navegador y completa el flujo OAuth2 con un servidor local
    temporal — sin copiar y pegar códigos.
    Guarda el token en TOKEN_FILE y retorna las credenciales.
    """
    client_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
    if not Path(client_secrets).exists():
        raise FileNotFoundError(f"No se encontró {client_secrets}")

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
    # select_account + consent: permite elegir la cuenta de Google y después la de marca
    credenciales = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="select_account consent",
    )
    youtube_auth.guardar_credenciales(credenciales)
    return credenciales


def obtener_todos_los_canales(youtube) -> list[dict]:
    """
    Retorna todos los canales disponibles para la cuenta autenticada,
    incluyendo Brand Accounts si la API los devuelve.
    """
    canales = []
    try:
        resp = youtube.channels().list(
            part="snippet",
            mine=True,
            maxResults=50
        ).execute()
        for item in resp.get("items", []):
            canales.append({
                "id": item["id"],
                "nombre": item["snippet"]["title"].strip(),
            })
    except Exception:
        pass
    return canales


def cerrar_sesion_youtube():
    """Elimina el token guardado para forzar una nueva autenticación."""
    try:
        youtube_auth.borrar_token()
    except Exception:
        pass
    # Limpiar estado de sesión
    for key in ["youtube_autenticado", "youtube_canal_nombre", "youtube_canal_id", "youtube_canales"]:
        st.session_state.pop(key, None)


def obtener_cliente_youtube_oauth() -> object | None:
    """Retorna el cliente de YouTube si ya hay credenciales guardadas."""
    credenciales = obtener_credenciales_guardadas()
    if not credenciales:
        return None
    try:
        return build("youtube", "v3", credentials=credenciales)
    except Exception as e:
        st.error(f"❌ Error al crear cliente de YouTube: {e}")
        return None


def subir_video_youtube(
    youtube,
    ruta_video: str,
    titulo: str,
    descripcion: str,
    tags: list,
    categoria_id: str,
    privacidad: str,
    fecha_publicacion: str | None = None,
    ruta_thumbnail: str | None = None,
) -> str | None:
    """
    Sube un video a YouTube con todos sus metadatos.
    Si ruta_thumbnail es válida, la sube como thumbnail personalizado.
    Retorna la URL del video subido o None si falla.

    fecha_publicacion: ISO 8601 (ej: "2024-12-25T18:00:00Z") o None para publicar ya.
    """
    # Configurar el estado de privacidad
    estado_privacidad = {"privacyStatus": privacidad}
    if privacidad == "private" and fecha_publicacion:
        estado_privacidad["publishAt"] = fecha_publicacion

    cuerpo_video = {
        "snippet": {
            "title": titulo,
            "description": descripcion,
            "tags": tags,
            "categoryId": categoria_id,
            "defaultLanguage": "es",
            "defaultAudioLanguage": "es",
        },
        "status": estado_privacidad,
    }

    try:
        # Crear la solicitud de subida
        request_subida = youtube.videos().insert(
            part="snippet,status",
            body=cuerpo_video,
            media_body=MediaFileUpload(
                ruta_video,
                chunksize=1024 * 1024,  # 1 MB por chunk
                resumable=True,
                mimetype="video/*"
            )
        )

        # Subir el video con progreso
        respuesta = None
        barra_progreso = st.progress(0, text="Iniciando subida...")

        while respuesta is None:
            estado, respuesta = request_subida.next_chunk()
            if estado:
                porcentaje = int(estado.progress() * 100)
                barra_progreso.progress(porcentaje, text=f"Subiendo video... {porcentaje}%")

        barra_progreso.progress(100, text="¡Subida completada!")
        video_id = respuesta["id"]

        # Subir thumbnail personalizado si se proveyó
        if ruta_thumbnail and Path(ruta_thumbnail).exists():
            st.info("🖼️ Subiendo thumbnail personalizado...")
            try:
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(ruta_thumbnail, mimetype="image/jpeg"),
                ).execute()
                st.success("✅ Thumbnail personalizado aplicado correctamente.")
            except HttpError as e_thumb:
                msg = str(e_thumb)
                if "403" in msg or "forbidden" in msg.lower() or "permission" in msg.lower():
                    st.error(
                        "❌ **El canal no tiene permiso para subir thumbnails personalizados.** "
                        "YouTube requiere verificación de teléfono para esta función.\n\n"
                        "**Cómo solucionarlo:**\n"
                        "1. Abrí [YouTube Studio](https://studio.youtube.com)\n"
                        "2. Configuración → Canal → Elegibilidad de funciones\n"
                        "3. Verificá el número de teléfono\n"
                        "4. Después de verificar, volvé a subir y el thumbnail funcionará."
                    )
                else:
                    st.error(f"❌ Error al subir thumbnail: {msg}")
            except Exception as e_thumb:
                st.error(f"❌ Error inesperado al subir thumbnail: {e_thumb}")
        elif ruta_thumbnail:
            st.warning("⚠️ El archivo de thumbnail no se encontró en disco — no se subió.")

        return f"https://www.youtube.com/watch?v={video_id}"

    except HttpError as e:
        st.error(f"❌ Error al subir video a YouTube: {e}")
        return None
    except Exception as e:
        st.error(f"❌ Error inesperado al subir: {e}")
        return None


# Categorías de YouTube más usadas para compilaciones
CATEGORIAS_YOUTUBE = {
    "Entretenimiento": "24",
    "Humor / Comedia": "23",
    "Mascotas y Animales": "15",
    "Deportes": "17",
    "Ciencia y Tecnología": "28",
    "Personas y Blogs": "22",
    "Música": "10",
    "Gaming": "20",
}


def mostrar_subidor():
    """Renderiza la interfaz del Subidor de Videos en Streamlit."""
    st.title("🚀 Subida Automática a YouTube")
    st.markdown("Sube tu video a YouTube con título, descripción, tags y horario de publicación.")

    with st.expander("❓ ¿Cómo usar este módulo?"):
        st.markdown("""
**¿Qué hace?**
Sube tu video terminado directamente a YouTube con todos los metadatos, sin entrar a YouTube Studio.

**Primera vez — Autenticación (solo una vez):**
1. Presioná **Autenticar con Google** — se abre solo el navegador
2. Elegí `agumarini94@gmail.com` y después la cuenta de marca **SunsetVideos**
3. Aceptá los permisos
4. La pestaña se cierra sola y volvés acá ya autenticado
5. Listo — la próxima vez ya no necesitás hacer esto

**Subir un video:**
1. Seleccioná tu archivo de video (MP4, MOV, hasta 2GB)
2. El título, descripción y tags se cargan **automático** si usaste el Generador de Contenido
3. Elegí la categoría correcta
4. Elegí la privacidad:
   - **Público** — se publica de inmediato
   - **No listado** — solo quién tenga el link lo ve
   - **Programar** — elegís día y hora de publicación (recomendado)
5. Presioná **Subir Video a YouTube** y esperá la barra de progreso

**Tip de horario:** Los mejores días para publicar son jueves y viernes entre las 17:00 y 20:00 (hora de tu audiencia).

**Importante:** No cierres la app mientras sube el video.
        """)

    if not verificar_dependencias():
        return

    # ── Autenticación OAuth2 ──────────────────────────────────
    st.subheader("1. Autenticación con YouTube")

    archivo_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
    existe_secrets = Path(archivo_secrets).exists()

    if not existe_secrets:
        st.error(f"❌ No se encontró `{archivo_secrets}`. Descárgalo desde Google Cloud Console.")
        return

    # Si ya hay token válido, mostrar estado y canales disponibles
    credenciales_guardadas = obtener_credenciales_guardadas()
    if credenciales_guardadas:
        # Obtener todos los canales disponibles (solo una vez por sesión)
        if "youtube_canales" not in st.session_state:
            youtube_tmp = obtener_cliente_youtube_oauth()
            if youtube_tmp:
                canales = obtener_todos_los_canales(youtube_tmp)
                st.session_state["youtube_canales"] = canales

        canales = st.session_state.get("youtube_canales", [])
        canal_objetivo = os.getenv("YOUTUBE_TARGET_CHANNEL", "").strip()

        col_auth1, col_auth2 = st.columns([3, 1])
        with col_auth2:
            if st.button("🔄 Cambiar cuenta", use_container_width=True):
                cerrar_sesion_youtube()
                st.session_state.pop("youtube_canales", None)
                st.rerun()

        if not canales:
            with col_auth1:
                st.error("⚠️ No se encontraron canales de YouTube en esta cuenta.")
            st.info("Esta cuenta no tiene un canal de YouTube directo. Probá autenticarte con `agumarini94@gmail.com` y elegí la cuenta de marca SunsetVideos para ver el canal viralesDelFutbol.")
            return

        # Si hay más de un canal, mostrar selector
        nombres = [f"{c['nombre']} ({c['id']})" for c in canales]
        if len(canales) > 1:
            with col_auth1:
                st.info(f"✅ Sesión activa — Se encontraron **{len(canales)} canales**")
            seleccion = st.selectbox(
                "Elegí el canal de destino para la subida:",
                options=range(len(canales)),
                format_func=lambda i: nombres[i],
                key="canal_seleccionado_idx"
            )
            canal_elegido = canales[seleccion]
        else:
            canal_elegido = canales[0]
            with col_auth1:
                st.success(f"✅ Sesión activa — Canal: **{canal_elegido['nombre']}**")

        st.session_state["youtube_canal_nombre"] = canal_elegido["nombre"]
        st.session_state["youtube_canal_id"] = canal_elegido["id"]

        # Verificar si es el canal correcto
        canal_nombre = canal_elegido["nombre"]
        if canal_objetivo:
            es_canal_correcto = canal_nombre.strip().lower() == canal_objetivo.strip().lower()
            if not es_canal_correcto:
                st.warning(f"⚠️ El canal seleccionado es **{canal_nombre}**, no **{canal_objetivo}**. Si viralesDelFutbol no aparece en la lista, volvé a autenticarte con `agumarini94@gmail.com` eligiendo la cuenta de marca SunsetVideos.")
        else:
            es_canal_correcto = True

        if not es_canal_correcto:
            with st.expander("📋 ¿Cómo conectar viralesDelFutbol?", expanded=True):
                st.markdown("""
**Está conectado el canal equivocado. Seguí estos pasos:**

**Paso 1 — Cerrá la sesión actual**
Hacé clic en **🔄 Cambiar cuenta** (arriba). Eso borra el token guardado.

**Paso 2 — Autenticá de nuevo**
1. Hacé clic en **🔐 Autenticar con Google** — se abre solo el navegador
2. Elegí `agumarini94@gmail.com` en el selector de cuentas de Google
3. En la pantalla siguiente elegí la cuenta de marca **SunsetVideos** —
   ahí adentro está el canal **viralesDelFutbol**, que es el destino de las subidas
4. Aceptá los permisos. La pestaña se cierra sola y volvés acá autenticado

> 💡 El paso que más se equivoca es el 3: si elegís la cuenta personal en vez de la
> de marca, Google conecta el canal personal y los videos se suben al lugar equivocado.
                """)
            return

        st.session_state["youtube_autenticado"] = True
    else:
        st.info("🔐 Necesitas autenticarte con Google para conectar el canal viralesDelFutbol.")

        with st.expander("📋 Instrucciones — Leer antes de autorizar", expanded=True):
            st.markdown("""
**Para que los videos se suban al canal correcto, seguí estos pasos en orden:**

**Paso 1 — Presioná "Autenticar con Google"** acá abajo. Se abre solo el navegador.

**Paso 2 — Elegí `agumarini94@gmail.com`** en el selector de cuentas de Google.

**Paso 3 — Elegí la cuenta de marca SunsetVideos** en la pantalla siguiente.
Dentro de esa cuenta de marca está el canal **viralesDelFutbol**, que es el destino de las subidas.

**Paso 4 — Aceptá los permisos.** La pestaña se cierra sola y volvés acá autenticado.

> ⚠️ Si en el Paso 3 elegís la cuenta personal en vez de la de marca, los videos van a subirse al canal equivocado.
            """)

        # Un solo paso — Google abre en el navegador y vuelve solo
        if st.button("🔐 Autenticar con Google", use_container_width=True, type="primary"):
            with st.spinner("Esperando que completes la autorización en el navegador..."):
                try:
                    autenticar_youtube_local()
                except Exception as e:
                    st.error(f"❌ Falló la autenticación: {e}")
                else:
                    st.session_state["youtube_autenticado"] = True
                    st.rerun()

        # Si no está autenticado, no mostrar el resto
        if not st.session_state.get("youtube_autenticado"):
            return

    st.markdown("---")

    # ── Seleccionar video ────────────────────────────────────
    st.subheader("2. Video a subir")

    ruta_preexistente = st.session_state.get("video_para_subir", "")
    archivo_video = None

    if ruta_preexistente and Path(ruta_preexistente).exists():
        st.success(f"✅ Video disponible desde el Compilador — {Path(ruta_preexistente).name}")
        st.video(ruta_preexistente)
        if st.button("📁 Elegir un video diferente", key="btn_cambiar_video"):
            st.session_state.pop("video_para_subir", None)
            st.rerun()
    else:
        archivo_video = st.file_uploader(
            "Seleccioná tu archivo de video",
            type=["mp4", "mov", "avi", "mkv", "webm"],
            help="Tamaño máximo: 128 GB. Formatos: MP4, MOV, AVI, MKV, WebM"
        )
        if archivo_video:
            st.video(archivo_video)

    # ── Thumbnail ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader("3. Thumbnail personalizado")

    ruta_thumb_auto = st.session_state.get("thumb_resultado", "")
    ruta_thumbnail_final: str | None = None

    if ruta_thumb_auto and Path(ruta_thumb_auto).exists():
        st.success("✅ Thumbnail generado disponible — se subirá automáticamente con el video.")
        st.image(ruta_thumb_auto, width=400)
        col_th1, col_th2 = st.columns(2)
        with col_th1:
            if st.button("✅ Usar este thumbnail", key="btn_usar_thumb", use_container_width=True):
                st.session_state["thumb_para_subir"] = ruta_thumb_auto
        with col_th2:
            if st.button("🚫 No usar thumbnail", key="btn_skip_thumb", use_container_width=True):
                st.session_state.pop("thumb_para_subir", None)
                st.session_state.pop("thumb_resultado", None)
                st.rerun()
        ruta_thumbnail_final = st.session_state.get("thumb_para_subir", ruta_thumb_auto)
    else:
        thumb_upload = st.file_uploader(
            "Subí un thumbnail manualmente (JPG/PNG, recomendado 1280×720)",
            type=["jpg", "jpeg", "png"],
            key="thumb_upload_manual",
        )
        if thumb_upload:
            d = Path(tempfile.gettempdir()) / "thumb_manual"
            d.mkdir(exist_ok=True)
            ruta_thumbnail_final = str(d / thumb_upload.name)
            with open(ruta_thumbnail_final, "wb") as f:
                f.write(thumb_upload.getvalue())
            st.image(thumb_upload, width=400, caption="Thumbnail a subir")
        else:
            st.caption("Sin thumbnail → YouTube elige un frame automáticamente. Podés generarlo en **🖼️ Generador de Thumbnail**.")

    # ── Metadatos del video ───────────────────────────────────
    st.markdown("---")
    st.subheader("4. Metadatos del video")

    # ── SEO automático, con el mismo criterio que el autopilot ────────────────
    # Hasta ahora estos tres campos solo se llenaban si pasabas a mano por el
    # Generador de SEO y apretabas "Usar en Subida a YouTube". Viniendo del
    # Compilador llegabas acá con todo vacío — y sin encuesta, así que tampoco se
    # posteaba el comentario.
    ruta_para_seo = st.session_state.get("video_para_subir", "")
    if not (ruta_para_seo and Path(ruta_para_seo).exists()):
        ruta_para_seo = ""

    ya_hay_seo = bool(
        st.session_state.get("ultimo_titulo") or st.session_state.get("ultima_descripcion")
    )

    col_seo_txt, col_seo_btn = st.columns([3, 1])
    with col_seo_txt:
        if ya_hay_seo:
            st.caption("✅ Título, descripción y tags cargados. Editalos abajo o regeneralos.")
        else:
            st.caption(
                "Todavía no hay SEO para este video. El botón usa el mismo criterio que el "
                "autopilot: mira 4 frames del video, pide 3 candidatos de título — sin "
                "muertes, lesiones ni noticias sin confirmar — y se queda con el que menos "
                "se parece a lo que ya subiste. Tarda ~15 segundos."
            )
    with col_seo_btn:
        generar_seo_btn = st.button(
            "🔄 Regenerar SEO" if ya_hay_seo else "✨ Generar SEO",
            use_container_width=True,
            type="secondary" if ya_hay_seo else "primary",
            disabled=not (ruta_para_seo or archivo_video),
            key="btn_seo_auto",
        )

    if not (ruta_para_seo or archivo_video):
        st.caption(
            "⚠️ Elegí primero el video (arriba) o llegá desde el **🎞️ Compilador** — "
            "el SEO automático necesita ver el video para analizarlo."
        )

    if generar_seo_btn and not ruta_para_seo and archivo_video:
        # Video subido a mano: Claude necesita el archivo en disco para sacarle
        # los frames. Se escribe una sola vez; la subida después lo reusa.
        d_seo = Path(tempfile.gettempdir()) / "uploader_seo"
        d_seo.mkdir(exist_ok=True)
        ruta_para_seo = str(d_seo / archivo_video.name)
        with open(ruta_para_seo, "wb") as f:
            f.write(archivo_video.getvalue())

    if generar_seo_btn:
        clips_feed = st.session_state.get("compilador_clips", [])
        orden_clips = st.session_state.get("comp_orden", list(range(len(clips_feed))))
        clips_seo = [clips_feed[i] for i in orden_clips if i < len(clips_feed)] or None

        with st.spinner("Claude está viendo el video y generando título, descripción y tags..."):
            from modules.seo_gen import generar_seo_automatico
            seo_auto = generar_seo_automatico(
                ruta_para_seo,
                clips=clips_seo,
                jugador=st.session_state.get("seo_jugador", ""),
            )

        if "error" in seo_auto:
            st.error(seo_auto["error"])
            cruda = seo_auto.get("respuesta_cruda", "").strip()
            if cruda:
                with st.expander("🔍 Qué contestó Claude (primeros 200 caracteres)"):
                    st.code(cruda, language=None)
            st.caption("Podés escribir el título y la descripción a mano acá abajo y subir igual.")
        else:
            st.session_state["ultimo_titulo"] = seo_auto.get("titulo", "")
            st.session_state["ultima_descripcion"] = seo_auto.get("descripcion", "")
            st.session_state["ultimos_tags"] = ", ".join(seo_auto.get("tags", []))

            encuesta_auto = seo_auto.get("encuesta")
            if (isinstance(encuesta_auto, dict)
                    and encuesta_auto.get("pregunta") and encuesta_auto.get("opciones")):
                st.session_state["encuesta_pendiente"] = {
                    "pregunta": encuesta_auto["pregunta"],
                    "opciones": encuesta_auto["opciones"],
                }
            else:
                st.session_state.pop("encuesta_pendiente", None)
            st.rerun()

    titulo_previo = st.session_state.get("ultimo_titulo", "")
    descripcion_previa = st.session_state.get("ultima_descripcion", "")
    tags_previos = st.session_state.get("ultimos_tags", "")

    titulo = st.text_input(
        "Título del video *",
        value=titulo_previo,
        max_chars=100,
        placeholder="Ej: Los 10 Fails Más Épicos de 2024 🤣",
    )
    st.caption(f"{len(titulo)}/100 caracteres")

    descripcion = st.text_area(
        "Descripción *",
        value=descripcion_previa,
        height=150,
        placeholder="Descripción de tu video con keywords para SEO...",
    )

    col_meta1, col_meta2 = st.columns(2)

    with col_meta1:
        categoria_nombre = st.selectbox("Categoría", options=list(CATEGORIAS_YOUTUBE.keys()))
        categoria_id = CATEGORIAS_YOUTUBE[categoria_nombre]

    with col_meta2:
        tags_input = st.text_input(
            "Tags (separados por coma)",
            value=tags_previos,
            placeholder="viral, compilacion, fails, gracioso, 2024",
        )

    tags_lista = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []
    if tags_lista:
        st.caption(f"Tags: {' · '.join([f'`{t}`' for t in tags_lista])}")

    # ── Configuración de privacidad y horario ─────────────────
    st.markdown("---")
    st.subheader("5. Privacidad y horario de publicación")

    col_priv1, col_priv2 = st.columns(2)

    with col_priv1:
        privacidad = st.selectbox(
            "Estado de privacidad",
            options=["public", "unlisted", "private"],
            format_func=lambda x: {"public": "🌍 Público", "unlisted": "🔗 No listado", "private": "🔒 Privado"}[x],
        )

    with col_priv2:
        programar = st.checkbox("Programar publicación futura")

    fecha_publicacion_iso = None
    if programar:
        from zoneinfo import ZoneInfo

        # Hora actual en cada zona
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        tz_arg = ZoneInfo("America/Argentina/Buenos_Aires")
        tz_isr = ZoneInfo("Asia/Jerusalem")
        tz_us  = ZoneInfo("America/New_York")

        arg_now = utc_now.astimezone(tz_arg)
        isr_now = utc_now.astimezone(tz_isr)
        us_now  = utc_now.astimezone(tz_us)

        st.markdown("**🕐 Hora actual por zona horaria**")
        col_z1, col_z2, col_z3 = st.columns(3)
        col_z1.metric("🇦🇷 Argentina", arg_now.strftime("%H:%M"), arg_now.strftime("%a %d/%m"))
        col_z2.metric("🇮🇱 Israel",    isr_now.strftime("%H:%M"), isr_now.strftime("%a %d/%m"))
        col_z3.metric("🇺🇸 EE.UU. (ET)", us_now.strftime("%H:%M"), us_now.strftime("%a %d/%m"))

        # Recomendación de horario
        # Buenos días: mar(1), mié(2), jue(3), vie(4) — misma lógica que el bot
        _BUENOS_DIAS_PUB = {1, 2, 3, 4}
        _DIAS_ES_PUB = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        hora_arg = arg_now.hour
        dia_semana = arg_now.weekday()

        if dia_semana in _BUENOS_DIAS_PUB and hora_arg < 21:
            # Hoy es buen día con ventana disponible
            if hora_arg < 18:
                recom_hora_arg = arg_now.replace(hour=18, minute=0, second=0, microsecond=0)
                recom_label = f"⭐ Hoy ({_DIAS_ES_PUB[dia_semana]}) a las 18:00 AR"
            else:
                # Ya estamos en el horario pico → próxima hora entera
                recom_hora_arg = arg_now.replace(hour=hora_arg + 1, minute=0, second=0, microsecond=0)
                recom_label = f"⭐ Hoy a las {hora_arg + 1:02d}:00 AR (horario pico)"
        else:
            # Mal día o ya pasó la ventana → próximo día bueno
            recom_hora_arg = arg_now  # fallback
            for delta in range(1, 8):
                prox = arg_now + datetime.timedelta(days=delta)
                if prox.weekday() in _BUENOS_DIAS_PUB:
                    recom_hora_arg = prox.replace(hour=18, minute=0, second=0, microsecond=0)
                    recom_label = f"📅 {_DIAS_ES_PUB[prox.weekday()].capitalize()} a las 18:00 AR"
                    break

        recom_utc = recom_hora_arg.astimezone(datetime.timezone.utc)

        st.markdown("**💡 Recomendación de horario**")
        st.caption("Mar/Mié/Jue/Vie 18-21h AR son los horarios pico de YouTube para audiencia hispanohablante.")

        col_rec, col_man = st.columns([2, 1])
        with col_rec:
            usar_recomendacion = st.button(
                f"{recom_label} — {recom_hora_arg.strftime('%A %d/%m a las %H:%M')} AR "
                f"({recom_utc.strftime('%H:%M')} UTC)",
                use_container_width=True,
                key="btn_usar_recom"
            )
            if usar_recomendacion:
                st.session_state["prog_fecha"] = recom_utc.date()
                st.session_state["prog_hora"] = recom_utc.time().replace(second=0, microsecond=0)
                st.rerun()
        with col_man:
            st.caption("o elegí manualmente ↓")

        st.markdown("**📅 Fecha y hora de publicación (UTC)**")
        col_fecha, col_hora_input = st.columns(2)

        fecha_default = st.session_state.get("prog_fecha", datetime.date.today() + datetime.timedelta(days=1))
        hora_default  = st.session_state.get("prog_hora",  utc_now.time().replace(second=0, microsecond=0))

        with col_fecha:
            fecha = st.date_input(
                "Fecha",
                value=fecha_default,
                min_value=datetime.date.today(),
                key="date_prog",
            )
        with col_hora_input:
            hora = st.time_input("Hora (UTC)", value=hora_default, key="time_prog")

        # Preview en hora argentina e israel
        pub_utc = datetime.datetime.combine(fecha, hora, tzinfo=datetime.timezone.utc)
        pub_arg = pub_utc.astimezone(tz_arg)
        pub_isr = pub_utc.astimezone(tz_isr)
        pub_us  = pub_utc.astimezone(tz_us)
        st.info(
            f"⏰ Se publicará el **{pub_arg.strftime('%d/%m/%Y a las %H:%M')} AR** · "
            f"{pub_isr.strftime('%H:%M')} Israel · "
            f"{pub_us.strftime('%H:%M')} EE.UU. (ET)"
        )

        fecha_publicacion_iso = pub_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Botón de subida ───────────────────────────────────────
    st.markdown("---")
    subir = st.button("🚀 Subir Video a YouTube", type="primary", use_container_width=True)

    if subir:
        # Validaciones previas
        ruta_preexistente = st.session_state.get("video_para_subir", "")
        tiene_video = (ruta_preexistente and Path(ruta_preexistente).exists()) or archivo_video
        if not tiene_video:
            st.warning("⚠️ Seleccioná un archivo de video primero.")
            return
        if not titulo.strip():
            st.warning("⚠️ El título es obligatorio.")
            return
        if not descripcion.strip():
            st.warning("⚠️ La descripción es obligatoria.")
            return

        # Autenticar con YouTube
        with st.spinner("Conectando con YouTube..."):
            youtube = obtener_cliente_youtube_oauth()

        if not youtube:
            return

        # Determinar la ruta del video
        borrar_temporal = False
        if ruta_preexistente and Path(ruta_preexistente).exists():
            ruta_temporal = ruta_preexistente
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{archivo_video.name.split('.')[-1]}") as tmp:
                tmp.write(archivo_video.read())
                ruta_temporal = tmp.name
            borrar_temporal = True

        try:
            st.info(f"📤 Iniciando subida: **{titulo}**")

            from modules.historial import bloque_video_anterior
            descripcion_final = descripcion + bloque_video_anterior()

            url_video = subir_video_youtube(
                youtube=youtube,
                ruta_video=ruta_temporal,
                titulo=titulo,
                descripcion=descripcion_final,
                tags=tags_lista,
                categoria_id=categoria_id,
                privacidad="private" if programar else privacidad,
                fecha_publicacion=fecha_publicacion_iso,
                ruta_thumbnail=ruta_thumbnail_final,
            )

            if url_video:
                st.success("🎉 ¡Video subido exitosamente!")
                st.markdown(f"### [Ver tu video en YouTube]({url_video})")
                st.balloons()

                try:
                    from modules.historial import guardar_video_youtube
                    guardar_video_youtube(url_video, titulo)
                except Exception:
                    pass

                # Comentario con la encuesta, igual que el autopilot
                # (workflow.ejecutar_autopilot_pipeline): misma función y mismo parseo
                # del video_id. Nada de lo que pase acá puede cortar la subida: el
                # video ya está arriba y eso es lo que importa.
                encuesta_pendiente = st.session_state.get("encuesta_pendiente") or {}
                if encuesta_pendiente.get("pregunta") and encuesta_pendiente.get("opciones"):
                    ok = False
                    try:
                        video_id = url_video.split("v=")[-1].split("&")[0]
                        with st.spinner("🗳️ Posteando encuesta como comentario..."):
                            from modules.workflow import postear_comentario_encuesta
                            ok = postear_comentario_encuesta(video_id, encuesta_pendiente)
                    except Exception as e_com:
                        st.warning(f"⚠️ No se pudo postear la encuesta: {e_com}")
                    if ok:
                        st.success("✅ Encuesta posteada como comentario. Fijala desde YouTube Studio para máximo engagement.")
                        st.session_state.pop("encuesta_pendiente", None)
                    else:
                        st.warning("⚠️ No se pudo postear la encuesta automáticamente. El video quedó subido igual — podés comentarla a mano desde YouTube Studio.")

                # Mostrar resumen
                st.markdown("**Resumen de la publicación:**")
                resumen_cols = st.columns(3)
                resumen_cols[0].metric("Título", titulo[:30] + "..." if len(titulo) > 30 else titulo)
                resumen_cols[1].metric("Categoría", categoria_nombre)
                resumen_cols[2].metric(
                    "Publicación",
                    fecha_publicacion_iso[:10] if programar and fecha_publicacion_iso else "Inmediata"
                )

        finally:
            if borrar_temporal:
                try:
                    Path(ruta_temporal).unlink()
                except Exception:
                    pass

    # ── Sección de ayuda ──────────────────────────────────────
    st.markdown("---")
    with st.expander("❓ ¿Cómo obtener el archivo client_secret.json?"):
        st.markdown("""
1. Ve a [Google Cloud Console](https://console.cloud.google.com)
2. Crea un proyecto o selecciona uno existente
3. Activa la **YouTube Data API v3**
4. Ve a **Credenciales > Crear credenciales > ID de cliente OAuth 2.0**
5. Tipo: **Aplicación de escritorio**
6. Descarga el archivo JSON y renómbralo a `client_secret.json`
7. Colócalo en la raíz del proyecto (mismo nivel que `app.py`)
8. En **Pantalla de consentimiento OAuth**, agrega tu email como usuario de prueba
        """)
