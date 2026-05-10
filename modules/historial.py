"""
Módulo — Historial
Guarda y muestra los videos de YouTube subidos y las descargas de TikTok (últimos 2 días).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

DATA_DIR = Path("data")
YOUTUBE_FILE = DATA_DIR / "youtube_videos.json"
TIKTOK_FILE = DATA_DIR / "tiktok_history.json"


def _ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)


def _leer_json(ruta: Path) -> list:
    if ruta.exists():
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _escribir_json(ruta: Path, data: list):
    ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def guardar_video_youtube(url: str, titulo: str = ""):
    """Registra un video subido a YouTube."""
    _ensure_data_dir()
    videos = _leer_json(YOUTUBE_FILE)
    videos.append({
        "url": url,
        "titulo": titulo,
        "fecha": datetime.now(timezone.utc).isoformat(),
    })
    _escribir_json(YOUTUBE_FILE, videos)


def obtener_ultimo_video_youtube() -> dict | None:
    """Retorna el último video subido a YouTube o None si no hay historial."""
    videos = _leer_json(YOUTUBE_FILE)
    return videos[-1] if videos else None


def bloque_video_anterior() -> str:
    """
    Genera el bloque de texto con el link al video anterior para inyectar
    al final de la descripción de YouTube.
    """
    ultimo = obtener_ultimo_video_youtube()
    if not ultimo:
        return ""
    canal = os.getenv("YOUTUBE_TARGET_CHANNEL", "").strip()
    handle = f"@{canal.replace(' ', '')}" if canal else ""
    titulo = ultimo.get("titulo") or "nuestro video anterior"
    lineas = [
        "",
        "─────────────────────────────",
        "🔔 ¿Te hizo reír? ¡No te pierdas el anterior!",
        f"▶ {titulo}",
        ultimo["url"],
    ]
    if handle:
        lineas.append(f"📺 Más videos: https://www.youtube.com/{handle}")
    return "\n".join(lineas)


def guardar_descarga_tiktok(url: str, titulo: str = "", canal: str = ""):
    """Registra una descarga de TikTok. Descarta entradas con más de 2 días de antigüedad."""
    _ensure_data_dir()
    historial = _leer_json(TIKTOK_FILE)
    hace_dos_dias = datetime.now(timezone.utc) - timedelta(days=2)

    # Limpiar viejos
    historial = [
        h for h in historial
        if datetime.fromisoformat(h["fecha"]) > hace_dos_dias
    ]

    # Evitar duplicados
    if url not in {h["url"] for h in historial}:
        historial.append({
            "url": url,
            "titulo": titulo,
            "canal": canal,
            "fecha": datetime.now(timezone.utc).isoformat(),
        })

    _escribir_json(TIKTOK_FILE, historial)


def mostrar_historial():
    st.title("📋 Historial")

    # ── Videos de YouTube subidos ─────────────────────────────────────────────
    st.subheader("🎬 Videos subidos a YouTube")

    videos_yt = _leer_json(YOUTUBE_FILE)

    if not videos_yt:
        st.info("Todavía no subiste ningún video. Aparecerán acá después de subirlos desde el módulo **Subida a YouTube**.")
    else:
        for v in reversed(videos_yt):
            fecha_str = datetime.fromisoformat(v["fecha"]).strftime("%d/%m/%Y %H:%M")
            titulo = v.get("titulo") or v["url"]
            st.markdown(f"- [{titulo}]({v['url']}) — {fecha_str}")

        st.markdown("")
        if st.button("🗑️ Borrar historial de YouTube", key="btn_borrar_yt"):
            YOUTUBE_FILE.unlink(missing_ok=True)
            st.rerun()

    st.markdown("---")

    # ── Descargas de TikTok (últimos 2 días) ─────────────────────────────────
    st.subheader("🎵 Descargas de TikTok — últimos 2 días")

    hace_dos_dias = datetime.now(timezone.utc) - timedelta(days=2)
    historial_tk = _leer_json(TIKTOK_FILE)
    recientes = [
        h for h in historial_tk
        if datetime.fromisoformat(h["fecha"]) > hace_dos_dias
    ]

    if not recientes:
        st.info("No hay descargas de TikTok en los últimos 2 días. Aparecerán acá después de descargar videos desde el **Feed de TikTok**.")
    else:
        st.caption(f"{len(recientes)} descarga{'s' if len(recientes) != 1 else ''} en los últimos 2 días")
        for h in reversed(recientes):
            fecha_str = datetime.fromisoformat(h["fecha"]).strftime("%d/%m/%Y %H:%M")
            canal = f" · @{h['canal']}" if h.get("canal") else ""
            titulo = h.get("titulo") or h["url"]
            label = f"{titulo[:65]}{'...' if len(titulo) > 65 else ''}"
            st.markdown(f"- [{label}]({h['url']}){canal} — {fecha_str}")
