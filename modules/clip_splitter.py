import subprocess
import os
from pathlib import Path
import streamlit as st
from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector


TEMP_DIR = Path("ytbot_clips")


# ── Lógica ────────────────────────────────────────────────────────────────────

def descargar_video(url: str) -> Path:
    TEMP_DIR.mkdir(exist_ok=True)
    salida = TEMP_DIR / "video_original.mp4"
    if salida.exists():
        salida.unlink()

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(salida),
        "--no-playlist",
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Error al descargar:\n{r.stderr}")
    return salida


def detectar_escenas(video_path: Path, umbral: float = 27.0) -> list[dict]:
    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=umbral))
    manager.detect_scenes(video, show_progress=False)

    escenas = []
    for i, (inicio, fin) in enumerate(manager.get_scene_list()):
        inicio_s = inicio.get_seconds()
        fin_s = fin.get_seconds()
        escenas.append({
            "numero": i + 1,
            "inicio_seg": round(inicio_s, 2),
            "fin_seg": round(fin_s, 2),
            "inicio_fmt": _seg_a_hms(inicio_s),
            "fin_fmt": _seg_a_hms(fin_s),
            "duracion": round(fin_s - inicio_s, 1),
        })
    return escenas


def cortar_clips(video_path: Path, segmentos: list[dict]) -> list[Path]:
    TEMP_DIR.mkdir(exist_ok=True)
    clips = []
    for seg in segmentos:
        salida = TEMP_DIR / f"clip_{seg['numero']:03d}.mp4"
        duracion = seg["fin_seg"] - seg["inicio_seg"]
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(seg["inicio_seg"]),
            "-i", str(video_path),
            "-t", str(duracion),
            "-c:v", "libx264", "-c:a", "aac",
            "-avoid_negative_ts", "make_zero",
            str(salida),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            clips.append(salida)
    return clips


def cortar_manual(video_path: Path, timestamps: list[tuple]) -> list[Path]:
    """timestamps: lista de ("HH:MM:SS", "HH:MM:SS")"""
    segmentos = [
        {"numero": i + 1, "inicio_seg": _hms_a_seg(ini), "fin_seg": _hms_a_seg(fin)}
        for i, (ini, fin) in enumerate(timestamps)
    ]
    return cortar_clips(video_path, segmentos)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seg_a_hms(segundos: float) -> str:
    h = int(segundos // 3600)
    m = int((segundos % 3600) // 60)
    s = int(segundos % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _hms_a_seg(hms: str) -> float:
    partes = hms.strip().split(":")
    if len(partes) == 3:
        return int(partes[0]) * 3600 + int(partes[1]) * 60 + float(partes[2])
    if len(partes) == 2:
        return int(partes[0]) * 60 + float(partes[1])
    return float(partes[0])


# ── Interfaz Streamlit ────────────────────────────────────────────────────────

def mostrar_clip_splitter():
    st.title("✂️ Cortador de Streams")
    st.markdown("Pegá una URL de YouTube o Twitch y dividí el video en clips cortos para Reels.")

    # ── Paso 1: URL ──────────────────────────────────────────────────────────
    st.markdown("### 1. URL del video")
    url = st.text_input("URL de YouTube o Twitch", placeholder="https://www.youtube.com/watch?v=...")

    if url and st.button("⬇️ Descargar video", type="primary"):
        with st.spinner("Descargando... puede tardar unos minutos según el largo del video."):
            try:
                video_path = descargar_video(url)
                st.session_state["clip_video_path"] = str(video_path)
                st.success(f"Video descargado: `{video_path.name}` ({video_path.stat().st_size // 1_000_000} MB)")
            except Exception as e:
                st.error(str(e))

    if "clip_video_path" not in st.session_state:
        return

    video_path = Path(st.session_state["clip_video_path"])
    if not video_path.exists():
        st.warning("El video ya no está en disco. Volvé a descargarlo.")
        del st.session_state["clip_video_path"]
        return

    # ── Paso 2: Modo de corte ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 2. ¿Cómo querés cortar?")
    modo = st.radio("Modo", ["✂️ Manual (especificás los tiempos)", "🔍 Auto (detecta escenas)"], horizontal=True)

    # ── Modo Manual ──────────────────────────────────────────────────────────
    if modo.startswith("✂️"):
        st.markdown("Escribí un segmento por línea en formato `HH:MM:SS - HH:MM:SS`")
        st.caption("Ejemplo: `00:23:10 - 00:23:55`")

        texto = st.text_area(
            "Segmentos",
            placeholder="00:23:10 - 00:23:55\n00:45:00 - 00:45:40\n01:12:30 - 01:13:10",
            height=150,
        )

        if st.button("✂️ Cortar clips", type="primary"):
            lineas = [l.strip() for l in texto.strip().splitlines() if l.strip()]
            if not lineas:
                st.warning("Escribí al menos un segmento.")
                return

            timestamps = []
            error = False
            for linea in lineas:
                partes = [p.strip() for p in linea.split("-", 1)]
                if len(partes) != 2:
                    st.error(f"Formato incorrecto: `{linea}` — usá `HH:MM:SS - HH:MM:SS`")
                    error = True
                    break
                timestamps.append((partes[0], partes[1]))

            if not error:
                with st.spinner(f"Cortando {len(timestamps)} clip(s)..."):
                    clips = cortar_manual(video_path, timestamps)
                _mostrar_clips(clips)

    # ── Modo Auto ────────────────────────────────────────────────────────────
    else:
        col1, col2 = st.columns(2)
        with col1:
            umbral = st.slider(
                "Sensibilidad de detección",
                min_value=10, max_value=60, value=27,
                help="Menor = detecta más cortes (más clips). Mayor = solo cortes muy evidentes.",
            )
        with col2:
            dur_min = st.number_input("Duración mínima del clip (seg)", min_value=5, max_value=60, value=10)
            dur_max = st.number_input("Duración máxima del clip (seg)", min_value=15, max_value=120, value=60)

        if st.button("🔍 Detectar escenas", type="primary"):
            with st.spinner("Analizando el video..."):
                escenas = detectar_escenas(video_path, umbral=umbral)

            # Filtrar por duración
            escenas_filtradas = [e for e in escenas if dur_min <= e["duracion"] <= dur_max]
            st.session_state["escenas_detectadas"] = escenas_filtradas
            st.info(f"Se detectaron **{len(escenas_filtradas)}** escenas dentro del rango {dur_min}–{dur_max} seg.")

        if "escenas_detectadas" in st.session_state and st.session_state["escenas_detectadas"]:
            escenas = st.session_state["escenas_detectadas"]

            st.markdown("**Previsualizá cada escena y seleccioná las que querés exportar:**")

            seleccionadas = []
            for e in escenas:
                col_check, col_info, col_btn = st.columns([0.5, 4, 1.5])

                with col_check:
                    checked = st.checkbox("", key=f"escena_{e['numero']}", value=False)

                with col_info:
                    st.markdown(f"**Escena {e['numero']}** — {e['inicio_fmt']} → {e['fin_fmt']} ({e['duracion']}s)")

                with col_btn:
                    if st.button("👁 Preview", key=f"prev_{e['numero']}"):
                        with st.spinner("Cortando preview..."):
                            clips_prev = cortar_clips(video_path, [e])
                        if clips_prev:
                            st.session_state[f"preview_{e['numero']}"] = str(clips_prev[0])

                # Mostrar el reproductor si ya se generó el preview de esta escena
                if f"preview_{e['numero']}" in st.session_state:
                    st.video(st.session_state[f"preview_{e['numero']}"])

                if checked:
                    seleccionadas.append(e)

            st.markdown("---")
            total = len(seleccionadas)
            if total:
                if st.button(f"✂️ Exportar {total} clip(s) seleccionado(s)", type="primary"):
                    with st.spinner(f"Exportando {total} clip(s)..."):
                        clips = cortar_clips(video_path, seleccionadas)
                    _mostrar_clips(clips)
            else:
                st.info("Previsualizá las escenas y marcá el checkbox de las que querés exportar.")


def _mostrar_clips(clips: list[Path]):
    if not clips:
        st.error("No se generó ningún clip. Revisá los tiempos.")
        return

    st.success(f"✅ Se generaron {len(clips)} clip(s)")
    for clip in clips:
        st.markdown(f"**{clip.name}**")
        st.video(str(clip))
        with open(clip, "rb") as f:
            st.download_button(
                label=f"⬇️ Descargar {clip.name}",
                data=f,
                file_name=clip.name,
                mime="video/mp4",
                key=f"dl_{clip.name}",
            )
        st.markdown("---")
