"""
Módulo 6 — Descargador de Videos
Descarga videos de YouTube y TikTok directamente desde la app usando yt-dlp.
TikTok: descarga sin marca de agua usando la API interna de TikTok.
"""

import streamlit as st
import yt_dlp
import os
import tempfile
from pathlib import Path


CALIDADES_YOUTUBE = {
    "1080p MP4": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/bestvideo[height<=1080]/best",
    "720p MP4":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/bestvideo[height<=720]/best",
    "480p MP4":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/bestvideo[height<=480]/best",
    "Solo Audio MP3": "bestaudio[ext=m4a]/bestaudio",
}

CALIDADES_TIKTOK = {
    "Sin marca de agua (MP4)": "download_addr_h264/download_addr/play_addr_h264/play_addr",
    "Con marca de agua (MP4)": "play_addr_h264/play_addr/bestvideo+bestaudio/best",
    "Solo Audio MP3": "bestaudio[ext=m4a]/bestaudio",
}

OPCIONES_BASE = {
    "quiet": True,
    "no_warnings": True,
    "cookiesfrombrowser": ("chrome",),
    "remote_components": "ejs:github",
    "ffmpeg_location": "/opt/homebrew/bin",
}

HEADERS_TIKTOK = {
    "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; en_US; Pixel 4; Build/NJH47F;tt-ok/3.12.13.1)",
    "Referer": "https://www.tiktok.com/",
}


def detectar_plataforma(url: str) -> str:
    """Detecta la plataforma del video según la URL."""
    url_lower = url.lower()
    if "tiktok.com" in url_lower or "vm.tiktok.com" in url_lower:
        return "tiktok"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    return "desconocida"


def obtener_opciones_plataforma(url: str, formato: str | None = None) -> dict:
    """Retorna las opciones de yt-dlp correctas según la plataforma."""
    plataforma = detectar_plataforma(url)
    opciones = {**OPCIONES_BASE}

    if plataforma == "tiktok":
        opciones["http_headers"] = HEADERS_TIKTOK
        calidades = CALIDADES_TIKTOK
    else:
        calidades = CALIDADES_YOUTUBE

    if formato:
        opciones["format"] = calidades.get(formato, list(calidades.values())[0])
    else:
        opciones["skip_download"] = True

    return opciones


def obtener_info_video(url: str) -> dict | None:
    """Obtiene metadata del video sin descargarlo."""
    opciones = obtener_opciones_plataforma(url)
    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "titulo": info.get("title", "Sin título"),
                "canal": info.get("uploader", "Desconocido"),
                "duracion": info.get("duration", 0),
                "vistas": info.get("view_count", 0),
                "thumbnail": info.get("thumbnail", ""),
                "descripcion": info.get("description", "")[:300],
            }
    except Exception as e:
        st.error(f"❌ No se pudo obtener información del video: {e}")
        return None


def formatear_duracion(segundos: int) -> str:
    """Convierte segundos a formato legible."""
    if not segundos:
        return "Desconocida"
    m, s = divmod(segundos, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def descargar_video(url: str, formato: str, carpeta_destino: str) -> str | None:
    """
    Descarga el video en la carpeta destino.
    Retorna la ruta del archivo descargado o None si falla.
    """
    es_audio = "MP3" in formato
    extension = "mp3" if es_audio else "mp4"

    opciones = obtener_opciones_plataforma(url, formato)
    opciones["outtmpl"] = os.path.join(carpeta_destino, "%(title)s.%(ext)s")
    opciones["merge_output_format"] = extension

    if es_audio:
        opciones["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            ydl.download([url])
            for archivo in Path(carpeta_destino).iterdir():
                if archivo.suffix.lower() in [".mp4", ".mp3", ".webm", ".m4a"]:
                    return str(archivo)
    except Exception as e:
        st.error(f"❌ Error al descargar: {e}")
    return None


def mostrar_descargador():
    """Renderiza la interfaz del Descargador de Videos en Streamlit."""
    st.title("⬇️ Descargador de Videos")
    st.markdown("Pegá la URL de un video de **YouTube** o **TikTok**. Los videos de TikTok se descargan sin marca de agua.")

    with st.expander("❓ ¿Cómo usar este módulo?"):
        st.markdown("""
**¿Qué hace?**
Descarga videos de YouTube y TikTok directamente desde la app.
Los videos de TikTok se descargan **sin marca de agua** usando la API interna de TikTok.

**Plataformas soportadas:**
- 🎬 **YouTube** — `youtube.com/watch?v=...` o `youtu.be/...`
- 🎵 **TikTok** — `tiktok.com/@usuario/video/...` o links cortos `vm.tiktok.com/...`

**Pasos:**
1. Copiá la URL del video
2. Pegala en el campo — la app detecta la plataforma automáticamente
3. Presioná **Ver información del video** para confirmar que es el correcto
4. Elegí la calidad y presioná **Descargar video**

**Tip TikTok:** Los videos virales de TikTok son perfectos para re-editar y subir a YouTube.
La descarga sin marca de agua te da el video limpio para usar en compilaciones.
        """)

    # ── Input de URL ──────────────────────────────────────────
    url = st.text_input(
        "URL del video",
        placeholder="https://www.youtube.com/watch?v=... o https://www.tiktok.com/@usuario/video/...",
    )

    # Badge de plataforma detectada en tiempo real
    plataforma = None
    calidades = CALIDADES_YOUTUBE

    if url.strip():
        plataforma = detectar_plataforma(url.strip())
        if plataforma == "youtube":
            st.success("🎬 YouTube detectado")
            calidades = CALIDADES_YOUTUBE
        elif plataforma == "tiktok":
            st.success("🎵 TikTok detectado — se descargará sin marca de agua")
            calidades = CALIDADES_TIKTOK
        else:
            st.warning("⚠️ Plataforma no reconocida. Solo se soporta YouTube y TikTok.")

    col1, col2 = st.columns([2, 2])
    with col1:
        calidad = st.selectbox("Calidad / Formato", options=list(calidades.keys()))
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        ver_info = st.button("🔍 Ver información del video", use_container_width=True)

    # ── Mostrar info del video ────────────────────────────────
    if ver_info:
        if not url.strip():
            st.warning("⚠️ Ingresa una URL primero.")
        elif plataforma == "desconocida":
            st.error("❌ URL no reconocida. Usá un link de YouTube o TikTok.")
        else:
            with st.spinner("Obteniendo información..."):
                info = obtener_info_video(url)
            if info:
                st.session_state["info_video_descarga"] = info
                st.session_state["url_descarga"] = url
                st.session_state["plataforma_descarga"] = plataforma

    if "info_video_descarga" in st.session_state:
        info = st.session_state["info_video_descarga"]
        st.markdown("---")

        col_thumb, col_datos = st.columns([1, 2])
        with col_thumb:
            if info["thumbnail"]:
                st.image(info["thumbnail"], use_container_width=True)
        with col_datos:
            st.markdown(f"### {info['titulo']}")
            st.caption(f"Canal: {info['canal']}")
            col_d1, col_d2 = st.columns(2)
            col_d1.metric("Duración", formatear_duracion(info["duracion"]))
            col_d2.metric("Vistas", f"{info['vistas']:,}" if info["vistas"] else "N/A")

        st.markdown("---")

        # ── Botón de descarga ─────────────────────────────────
        if st.button("⬇️ Descargar video", type="primary", use_container_width=True):
            carpeta_temp = tempfile.mkdtemp()
            plat = st.session_state.get("plataforma_descarga", "youtube")
            spinner_msg = "Descargando sin marca de agua..." if plat == "tiktok" else f"Descargando en {calidad}..."

            with st.spinner(spinner_msg):
                ruta_archivo = descargar_video(
                    st.session_state["url_descarga"],
                    calidad,
                    carpeta_temp,
                )

            if ruta_archivo and Path(ruta_archivo).exists():
                st.success("✅ ¡Descarga completada!")

                with open(ruta_archivo, "rb") as f:
                    datos = f.read()

                nombre_archivo = Path(ruta_archivo).name
                extension = Path(ruta_archivo).suffix.lower()
                mime = "audio/mpeg" if extension == ".mp3" else "video/mp4"

                st.download_button(
                    label=f"💾 Guardar '{nombre_archivo}'",
                    data=datos,
                    file_name=nombre_archivo,
                    mime=mime,
                    use_container_width=True,
                )

                try:
                    Path(ruta_archivo).unlink()
                except Exception:
                    pass
            else:
                st.error("❌ No se pudo completar la descarga. Verifica que la URL sea válida y que tengas cookies de TikTok en Chrome.")
