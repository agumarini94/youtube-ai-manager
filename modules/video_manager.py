import streamlit as st
from modules.uploader import obtener_cliente_youtube_oauth, TOKEN_FILE
from pathlib import Path
import re


# ── Helpers de idioma ─────────────────────────────────────────────────────────

PALABRAS_INGLES = {
    "the", "and", "of", "in", "is", "are", "you", "your", "that", "this",
    "with", "for", "not", "have", "it", "he", "she", "they", "we", "do",
    "at", "be", "from", "or", "an", "will", "my", "one", "all", "would",
    "there", "their", "what", "so", "up", "out", "if", "about", "who",
    "get", "which", "go", "me", "when", "make", "can", "like", "time",
    "no", "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then", "now",
    "look", "only", "come", "its", "over", "think", "also", "back", "after",
    "use", "two", "how", "our", "work", "first", "well", "way", "even",
    "want", "because", "these", "most", "us", "try", "fails", "funny",
    "best", "top", "compilation", "moments", "ever", "world", "amazing",
    "incredible", "unbelievable", "watch", "must", "new", "old", "last",
    "great", "little", "man", "big", "too", "does", "more", "long", "down",
    "day", "did", "get", "has", "her", "his", "how", "may", "own", "say",
    "she", "was", "way", "who", "been", "call", "each", "find",
}


def detectar_idioma(titulo: str) -> str:
    """Devuelve 'en' si el título parece inglés, 'es' si parece español."""
    tiene_tildes = bool(re.search(r'[áéíóúüñ¡¿]', titulo, re.IGNORECASE))
    if tiene_tildes:
        return "es"

    palabras = set(re.sub(r'[^a-zA-Z\s]', '', titulo).lower().split())
    coincidencias = palabras & PALABRAS_INGLES
    if len(coincidencias) >= 2 or (palabras and len(coincidencias) / max(len(palabras), 1) > 0.3):
        return "en"

    return "es"


# ── API de YouTube ────────────────────────────────────────────────────────────

def obtener_videos_canal(youtube, max_resultados: int = 200) -> list[dict]:
    """Trae todos los videos del canal con título, vistas y privacidad."""
    # Primero obtenemos el ID de la playlist de uploads del canal
    canal_resp = youtube.channels().list(
        part="contentDetails,statistics",
        mine=True,
    ).execute()

    if not canal_resp.get("items"):
        return []

    canal = canal_resp["items"][0]
    uploads_id = canal["contentDetails"]["relatedPlaylists"]["uploads"]

    # Recorremos la playlist de uploads paginando
    videos = []
    page_token = None

    while len(videos) < max_resultados:
        resp = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        ids = [item["snippet"]["resourceId"]["videoId"] for item in resp.get("items", [])]
        if not ids:
            break

        # Traemos estadísticas y estado de privacidad en un solo request
        stats_resp = youtube.videos().list(
            part="statistics,status,snippet",
            id=",".join(ids),
        ).execute()

        for item in stats_resp.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            status = item.get("status", {})
            titulo = snippet.get("title", "")
            videos.append({
                "id": item["id"],
                "titulo": titulo,
                "vistas": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "privacidad": status.get("privacyStatus", "public"),
                "idioma": detectar_idioma(titulo),
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                "url": f"https://www.youtube.com/watch?v={item['id']}",
            })

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return videos


def cambiar_privacidad(youtube, video_id: str, nuevo_estado: str) -> bool:
    """Cambia la privacidad de un video. nuevo_estado: 'private', 'unlisted', 'public'"""
    try:
        youtube.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": {"privacyStatus": nuevo_estado},
            },
        ).execute()
        return True
    except Exception as e:
        st.error(f"Error al cambiar {video_id}: {e}")
        return False


def eliminar_video(youtube, video_id: str) -> bool:
    """Elimina un video permanentemente."""
    try:
        youtube.videos().delete(id=video_id).execute()
        return True
    except Exception as e:
        st.error(f"Error al eliminar {video_id}: {e}")
        return False


# ── Interfaz Streamlit ────────────────────────────────────────────────────────

def mostrar_video_manager():
    st.title("🎛️ Gestión de Videos")
    st.markdown("Ocultá o eliminá videos sin entrar a YouTube Studio.")

    # Verificar autenticación
    if not Path(TOKEN_FILE).exists():
        st.warning("⚠️ Necesitás autenticarte primero. Andá a **Subida a YouTube** y completá el login de Google.")
        return

    youtube = obtener_cliente_youtube_oauth()
    if not youtube:
        st.error("No se pudo conectar con YouTube. Intentá re-autenticarte en el módulo de Subida.")
        return

    # Cargar videos (con cache en session_state para no rellamar la API)
    if "vm_videos" not in st.session_state:
        with st.spinner("Cargando videos del canal..."):
            st.session_state["vm_videos"] = obtener_videos_canal(youtube)

    videos = st.session_state["vm_videos"]

    if not videos:
        st.info("No se encontraron videos en el canal.")
        return

    # ── Filtros ───────────────────────────────────────────────────────────────
    st.markdown("---")
    col_f1, col_f2, col_f3 = st.columns(3)

    with col_f1:
        filtro_idioma = st.selectbox(
            "Idioma",
            ["Todos", "Solo inglés 🇬🇧", "Solo español 🇪🇸"],
        )
    with col_f2:
        filtro_privacidad = st.selectbox(
            "Privacidad",
            ["Todos", "Públicos", "Privados", "No listados"],
        )
    with col_f3:
        orden = st.selectbox("Ordenar por", ["Menos vistas primero", "Más vistas primero"])

    if st.button("🔄 Recargar lista desde YouTube"):
        del st.session_state["vm_videos"]
        st.rerun()

    # Aplicar filtros
    filtrados = videos

    if filtro_idioma == "Solo inglés 🇬🇧":
        filtrados = [v for v in filtrados if v["idioma"] == "en"]
    elif filtro_idioma == "Solo español 🇪🇸":
        filtrados = [v for v in filtrados if v["idioma"] == "es"]

    if filtro_privacidad == "Públicos":
        filtrados = [v for v in filtrados if v["privacidad"] == "public"]
    elif filtro_privacidad == "Privados":
        filtrados = [v for v in filtrados if v["privacidad"] == "private"]
    elif filtro_privacidad == "No listados":
        filtrados = [v for v in filtrados if v["privacidad"] == "unlisted"]

    filtrados = sorted(
        filtrados,
        key=lambda v: v["vistas"],
        reverse=(orden == "Más vistas primero"),
    )

    # ── Info rápida ───────────────────────────────────────────────────────────
    en_ingles = sum(1 for v in videos if v["idioma"] == "en")
    publicos = sum(1 for v in videos if v["privacidad"] == "public")

    col_i1, col_i2, col_i3 = st.columns(3)
    col_i1.metric("Total videos", len(videos))
    col_i2.metric("En inglés (detectados)", en_ingles)
    col_i3.metric("Públicos", publicos)

    st.markdown("---")
    st.markdown(f"### {len(filtrados)} videos")

    if not filtrados:
        st.info("Ningún video coincide con los filtros.")
        return

    # ── Lista de videos con checkboxes ────────────────────────────────────────
    seleccionados = []

    col_sel_all, _ = st.columns([1, 4])
    with col_sel_all:
        seleccionar_todos = st.checkbox("Seleccionar todos", key="vm_all")

    for v in filtrados:
        idioma_badge = "🇬🇧 EN" if v["idioma"] == "en" else "🇪🇸 ES"
        privacidad_badge = {"public": "🟢 Público", "private": "🔴 Privado", "unlisted": "🟡 No listado"}.get(v["privacidad"], v["privacidad"])

        col_cb, col_thumb, col_info = st.columns([0.5, 1, 5])

        with col_cb:
            checked = st.checkbox("", key=f"vm_{v['id']}", value=seleccionar_todos)

        with col_thumb:
            if v["thumbnail"]:
                st.image(v["thumbnail"], width=80)

        with col_info:
            st.markdown(f"**[{v['titulo']}]({v['url']})**")
            st.caption(f"{idioma_badge} · {privacidad_badge} · 👁 {v['vistas']:,} vistas · 👍 {v['likes']:,}")

        if checked:
            seleccionados.append(v)

    # ── Acciones en lote ──────────────────────────────────────────────────────
    if not seleccionados:
        st.info("Seleccioná videos para ver las acciones disponibles.")
        return

    st.markdown("---")
    st.markdown(f"### Acciones para {len(seleccionados)} video(s) seleccionado(s)")

    col_a1, col_a2, col_a3 = st.columns(3)

    with col_a1:
        if st.button("🔴 Hacer privados", use_container_width=True):
            st.session_state["vm_accion"] = ("private", seleccionados)
            st.rerun()

    with col_a2:
        if st.button("🟡 Hacer no listados", use_container_width=True):
            st.session_state["vm_accion"] = ("unlisted", seleccionados)
            st.rerun()

    with col_a3:
        if st.button("🗑️ Eliminar permanentemente", use_container_width=True, type="primary"):
            st.session_state["vm_accion"] = ("delete", seleccionados)
            st.rerun()

    # ── Confirmación ──────────────────────────────────────────────────────────
    if "vm_accion" in st.session_state:
        accion, videos_accion = st.session_state["vm_accion"]

        etiquetas = {
            "private": ("hacer PRIVADOS", "🔴"),
            "unlisted": ("hacer NO LISTADOS", "🟡"),
            "delete": ("ELIMINAR PERMANENTEMENTE", "🗑️"),
        }
        etiqueta, icono = etiquetas[accion]

        st.warning(f"{icono} ¿Confirmás que querés **{etiqueta}** {len(videos_accion)} video(s)?")

        if accion == "delete":
            st.error("⚠️ Esta acción es irreversible. Los videos se borran para siempre.")

        col_c1, col_c2 = st.columns(2)

        with col_c1:
            if st.button("✅ Sí, confirmar", type="primary", use_container_width=True):
                ok = 0
                fail = 0
                with st.spinner(f"Aplicando cambios a {len(videos_accion)} video(s)..."):
                    for v in videos_accion:
                        if accion == "delete":
                            exito = eliminar_video(youtube, v["id"])
                        else:
                            exito = cambiar_privacidad(youtube, v["id"], accion)
                        if exito:
                            ok += 1
                        else:
                            fail += 1

                del st.session_state["vm_accion"]
                del st.session_state["vm_videos"]  # Forzar recarga

                if ok:
                    st.success(f"✅ {ok} video(s) actualizados correctamente.")
                if fail:
                    st.error(f"❌ {fail} video(s) fallaron.")
                st.rerun()

        with col_c2:
            if st.button("❌ Cancelar", use_container_width=True):
                del st.session_state["vm_accion"]
                st.rerun()
