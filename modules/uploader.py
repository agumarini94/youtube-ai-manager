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


# Permisos OAuth2 necesarios para subir videos y consultar el canal activo
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# Archivo donde se guarda el token de sesión OAuth2
TOKEN_FILE = "token_youtube.json"


def verificar_dependencias() -> bool:
    """Verifica que las librerías de Google estén instaladas."""
    if not GOOGLE_LIBS_OK:
        st.error("❌ Librerías de Google no instaladas. Ejecuta: pip install google-api-python-client google-auth-oauthlib")
        return False
    return True


def obtener_credenciales_guardadas() -> object | None:
    """Carga el token guardado si existe y es válido."""
    if not Path(TOKEN_FILE).exists():
        return None
    try:
        credenciales = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        # Renovar token si expiró
        if credenciales.expired and credenciales.refresh_token:
            credenciales.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(credenciales.to_json())
        if credenciales.valid:
            return credenciales
    except Exception:
        pass
    return None


def generar_url_auth() -> tuple[str, object] | None:
    """
    Genera la URL de autorización OAuth2 para mostrarla al usuario.
    Usa select_account para que Google muestre el selector de canal/cuenta.
    Retorna (url, flow) para completar el flujo después.
    """
    client_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
    if not Path(client_secrets).exists():
        return None
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secrets,
            SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"  # Modo consola: muestra el código en pantalla
        )
        # select_account + consent: fuerza mostrar el selector de cuenta Y de canal cada vez
        url_auth, _ = flow.authorization_url(prompt="select_account consent")
        return url_auth, flow
    except Exception as e:
        st.error(f"❌ Error al generar URL de autorización: {e}")
        return None


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


def obtener_canal_activo(youtube) -> tuple[str, str] | tuple[None, None]:
    """
    Retorna (nombre_canal, channel_id) del primer canal conectado.
    """
    canales = obtener_todos_los_canales(youtube)
    if canales:
        return canales[0]["nombre"], canales[0]["id"]
    return None, None


def cerrar_sesion_youtube():
    """Elimina el token guardado para forzar una nueva autenticación."""
    try:
        Path(TOKEN_FILE).unlink(missing_ok=True)
    except Exception:
        pass
    # Limpiar estado de sesión
    for key in ["youtube_autenticado", "youtube_canal_nombre", "youtube_canal_id", "youtube_canales", "oauth_flow", "oauth_url"]:
        st.session_state.pop(key, None)


def completar_auth_con_codigo(flow: object, codigo: str) -> object | None:
    """Completa el flujo OAuth2 con el código que pegó el usuario."""
    try:
        flow.fetch_token(code=codigo.strip())
        credenciales = flow.credentials
        with open(TOKEN_FILE, "w") as f:
            f.write(credenciales.to_json())
        return credenciales
    except Exception as e:
        st.error(f"❌ Código inválido o expirado: {e}")
        return None


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
1. Presioná **Generar link de autorización**
2. Abrí el link en el navegador
3. Iniciá sesión con tu cuenta de Google y aceptá los permisos
4. Google te muestra un código — copialo
5. Pegalo en el campo "Código de autorización" y presioná **Confirmar código**
6. Listo — la próxima vez ya no necesitás hacer esto

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
    existe_token = Path(TOKEN_FILE).exists()

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
            st.info("Esta cuenta no tiene un canal de YouTube directo. Probá autenticarte con `agumarini94@gmail.com` para ver todos los canales incluyendo ViralLocos.")
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
        sin_canal = False
        if canal_objetivo:
            es_canal_correcto = canal_nombre.strip().lower() == canal_objetivo.strip().lower()
            if not es_canal_correcto:
                st.warning(f"⚠️ El canal seleccionado es **{canal_nombre}**, no **{canal_objetivo}**. Si ViralLocos no aparece en la lista, autenticarte con `agumarini94@gmail.com`.")
        else:
            es_canal_correcto = True

        if not es_canal_correcto:
            with st.expander("📋 ¿Cómo conectar ViralLocos?", expanded=True):
                if sin_canal:
                    st.markdown("""
**La cuenta autenticada no tiene canal de YouTube configurado todavía.**

**Paso 1 — Activá ViralLocos en YouTube con el Gmail nuevo**
1. Abrí [youtube.com](https://youtube.com) con `viralvideosinvestigate@gmail.com`
2. Hacé clic en tu foto de perfil arriba a la derecha
3. Tiene que aparecer **ViralLocos** en la lista — hacé clic para cambiar a ese canal
4. Si no aparece: la invitación como propietario todavía no fue aceptada — revisá el correo del Gmail nuevo
5. Una vez que dice *ViralLocos* en la esquina, volvé acá

**Paso 2 — Volvé a autenticar**
1. Hacé clic en **Cambiar canal** (arriba)
2. Generá un nuevo link de autorización
3. Abrilo en el navegador donde ya está activo ViralLocos
4. Aceptá y pegá el código
                    """)
                else:
                    st.markdown("""
**El canal equivocado está conectado. Seguí estos pasos exactos:**

**Paso 1 — Cambiá el canal activo en YouTube**
1. Abrí [youtube.com](https://youtube.com) en el mismo navegador que vas a usar para autorizar
2. Hacé clic en tu foto de perfil (arriba a la derecha)
3. Elegí **"Cambiar cuenta"** → seleccioná **ViralLocos**
4. Verificá que en la esquina dice *ViralLocos* y no *agustinmarini*

**Paso 2 — Autorizá desde acá**
1. Volvé a esta app y hacé clic en **"Cambiar canal"** (arriba)
2. Generá un nuevo link de autorización
3. Abrilo en el mismo navegador donde activaste ViralLocos
4. Google va a pedir permisos para ese canal — aceptá
5. Pegá el código acá

> 💡 La clave es que el canal de YouTube en el navegador sea ViralLocos **antes** de abrir el link.
                """)
            return

        st.session_state["youtube_autenticado"] = True
    else:
        st.info("🔐 Necesitas autenticarte con Google para conectar el canal ViralLocos.")

        with st.expander("📋 Instrucciones — Leer antes de autorizar", expanded=True):
            st.markdown("""
**Para que los videos se suban a ViralLocos y no a agustinmarini, seguí estos pasos en orden:**

**Paso 1 — Activá ViralLocos en tu navegador**
1. Abrí [youtube.com](https://youtube.com) en el navegador que vas a usar
2. Hacé clic en tu foto de perfil → **"Cambiar cuenta"** → **ViralLocos**
3. Confirmá que dice *ViralLocos* arriba a la derecha

**Paso 2 — Generá el link de autorización desde acá abajo**

**Paso 3 — Abrí el link en el mismo navegador** (donde activaste ViralLocos)

**Paso 4 — Aceptá los permisos y copiá el código que te da Google**

**Paso 5 — Pegalo acá abajo y confirmá**

> ⚠️ Si saltás el Paso 1, Google va a autorizar el canal agustinmarini por defecto.
            """)

        # Paso 1 — Generar URL
        if st.button("🔗 Generar link de autorización", use_container_width=True):
            resultado = generar_url_auth()
            if resultado:
                url_auth, flow = resultado
                st.session_state["oauth_flow"] = flow
                st.session_state["oauth_url"] = url_auth

        # Mostrar URL si ya fue generada
        if "oauth_url" in st.session_state:
            st.markdown("---")
            st.markdown("**Link de autorización** — copialo y abrilo en el navegador donde activaste ViralLocos:")
            st.code(st.session_state["oauth_url"])
            st.markdown("**Código de autorización** — Google te lo muestra después de aceptar:")

            codigo = st.text_input("Pegá el código aquí", placeholder="4/0AX4XfW...")

            if st.button("✅ Confirmar código", use_container_width=True, type="primary"):
                if codigo.strip():
                    flow = st.session_state.get("oauth_flow")
                    if flow:
                        credenciales = completar_auth_con_codigo(flow, codigo)
                        if credenciales:
                            st.session_state["youtube_autenticado"] = True
                            st.session_state.pop("oauth_flow", None)
                            st.session_state.pop("oauth_url", None)
                            st.rerun()
                else:
                    st.warning("⚠️ Pegá el código antes de confirmar.")

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

    # Cargar datos del generador de contenido si existen
    titulo_previo = st.session_state.get("ultimo_titulo", "")
    descripcion_previa = st.session_state.get("ultima_descripcion", "")
    tags_previos = st.session_state.get("ultimos_tags", "")

    if titulo_previo or descripcion_previa:
        st.info("💡 Título, descripción y tags cargados automáticamente desde el Generador de Contenido.")

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

                encuesta_pendiente = st.session_state.get("encuesta_pendiente") or {}
                if encuesta_pendiente.get("pregunta") and encuesta_pendiente.get("opciones"):
                    video_id = url_video.rsplit("=", 1)[-1]
                    with st.spinner("🗳️ Posteando encuesta como comentario..."):
                        from modules.workflow import postear_comentario_encuesta
                        ok = postear_comentario_encuesta(video_id, encuesta_pendiente)
                    if ok:
                        st.success("✅ Encuesta posteada como comentario. Fijala desde YouTube Studio para máximo engagement.")
                        st.session_state.pop("encuesta_pendiente", None)
                    else:
                        st.warning("⚠️ No se pudo postear la encuesta automáticamente. Podés hacerlo a mano desde YouTube Studio.")

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
