"""
Módulo — Compilador de Videos
Ordená tus clips y unilos en un video de compilación listo para editar y subir.
"""

import streamlit as st
import subprocess
import tempfile
import os
import time
import hashlib
from pathlib import Path


RESOLUCIONES = {
    "Vertical 1080×1920 (Shorts / Reels)": (1080, 1920),
    "Horizontal 1920×1080 (YouTube clásico)": (1920, 1080),
    "Cuadrado 1080×1080 (Instagram)": (1080, 1080),
}


def run_ffmpeg(cmd: list) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return False, result.stderr[-800:]
        return True, ""
    except FileNotFoundError:
        return False, "FFmpeg no encontrado. Instalalo con: brew install ffmpeg"
    except Exception as e:
        return False, str(e)


def obtener_duracion(ruta: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", ruta],
            capture_output=True, text=True, timeout=15
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0.0


def normalizar_video(entrada: str, salida: str, ancho: int, alto: int, barra_texto: str = "") -> tuple[bool, str]:
    """Re-encoda el video al formato y resolución estándar para garantizar compatibilidad en el concat."""
    vf = (
        f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
        f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    ok, err = run_ffmpeg([
        "ffmpeg", "-i", entrada,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30",
        "-y", salida
    ])
    if not ok:
        # Fallback: sin escalar, solo re-encodar
        ok, err = run_ffmpeg([
            "ffmpeg", "-i", entrada,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30",
            "-y", salida
        ])
    return ok, err


def concatenar_clips(rutas: list[str], salida: str, ancho: int, alto: int) -> tuple[bool, str]:
    """
    Normaliza todos los clips al mismo formato y los concatena.
    Siempre re-encoda antes del concat para evitar problemas de codec mixto.
    """
    d = Path(salida).parent
    norm_dir = d / "norm"
    norm_dir.mkdir(exist_ok=True)

    videos_norm = []
    for i, ruta in enumerate(rutas):
        norm = str(norm_dir / f"clip_{i:03d}.mp4")
        ok, err = normalizar_video(ruta, norm, ancho, alto)
        if ok:
            videos_norm.append(norm)
        else:
            return False, f"Error normalizando clip {i+1}: {err}"

    if not videos_norm:
        return False, "No se pudo procesar ningún clip."

    lista = str(d / "lista_concat.txt")
    with open(lista, "w") as f:
        for ruta in videos_norm:
            f.write(f"file '{ruta}'\n")

    return run_ffmpeg([
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", lista, "-c", "copy", "-y", salida
    ])


def fmt_duracion(seg: float) -> str:
    if seg <= 0:
        return "—"
    m, s = divmod(int(seg), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def mostrar_compilador():
    st.title("🎞️ Compilador de Videos")
    st.markdown("Ordená tus clips y unilos en un solo video de compilación.")

    # ── Fuente de clips ────────────────────────────────────────────────────────
    clips_feed = st.session_state.get("compilador_clips", [])

    if clips_feed:
        st.success(f"✅ {len(clips_feed)} clips cargados desde el Feed de TikTok.")
        fuente = st.radio(
            "Clips a compilar",
            ["Usar clips del Feed de TikTok", "Subir videos manualmente"],
            horizontal=True,
            key="comp_fuente"
        )
    else:
        fuente = "Subir videos manualmente"
        st.info("Podés subir tus propios clips o ir al **Feed de TikTok** para buscar y descargar videos.")

    clips = []

    if fuente == "Usar clips del Feed de TikTok" and clips_feed:
        clips = list(clips_feed)
    else:
        archivos = st.file_uploader(
            "Subí los clips (podés seleccionar varios a la vez)",
            type=["mp4", "mov", "avi", "mkv", "webm"],
            accept_multiple_files=True,
            key="comp_upload"
        )
        if archivos:
            d = Path(tempfile.gettempdir()) / "compilador_manual"
            d.mkdir(exist_ok=True)
            for archivo in archivos:
                ruta = str(d / archivo.name)
                with open(ruta, "wb") as f:
                    f.write(archivo.getvalue())
                dur = obtener_duracion(ruta)
                clips.append({
                    "ruta": ruta,
                    "nombre": archivo.name,
                    "titulo": archivo.name.rsplit(".", 1)[0],
                    "canal": "",
                    "duracion": dur,
                })

    if not clips:
        return

    # ── Orden de los clips ─────────────────────────────────────────────────────
    # Inicializar orden si cambió la cantidad de clips
    orden_actual = st.session_state.get("comp_orden", [])
    if len(orden_actual) != len(clips):
        st.session_state["comp_orden"] = list(range(len(clips)))
    orden = st.session_state["comp_orden"]

    st.markdown("---")
    st.subheader(f"📋 {len(clips)} clips — ordenálos como quieras")
    st.caption("Usá las flechas ↑ ↓ para reordenar. El video final seguirá este orden de arriba a abajo.")

    for pos, idx in enumerate(orden):
        clip = clips[idx]
        dur = clip.get("duracion", 0)
        col_num, col_info, col_up, col_down = st.columns([0.4, 5, 0.4, 0.4])

        with col_num:
            st.markdown(f"**{pos + 1}**")
        with col_info:
            canal = f" · @{clip['canal']}" if clip.get("canal") else ""
            st.markdown(f"**{clip['titulo'][:60]}{'...' if len(clip['titulo']) > 60 else ''}**")
            st.caption(f"⏱ {fmt_duracion(dur)}{canal} · {clip['nombre']}")
        with col_up:
            if pos > 0:
                if st.button("↑", key=f"up_{pos}", use_container_width=True):
                    orden[pos], orden[pos - 1] = orden[pos - 1], orden[pos]
                    st.session_state["comp_orden"] = orden
                    st.rerun()
        with col_down:
            if pos < len(orden) - 1:
                if st.button("↓", key=f"dn_{pos}", use_container_width=True):
                    orden[pos], orden[pos + 1] = orden[pos + 1], orden[pos]
                    st.session_state["comp_orden"] = orden
                    st.rerun()

    dur_total = sum(clips[i].get("duracion", 0) for i in orden)
    st.markdown(f"**Duración total estimada: {fmt_duracion(dur_total)}**")

    # ── Configuración de salida ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⚙️ Configuración de salida")

    col_res, col_info2 = st.columns([2, 2])
    with col_res:
        res_label = st.selectbox("Resolución", list(RESOLUCIONES.keys()), key="comp_res")
    with col_info2:
        ancho, alto = RESOLUCIONES[res_label]
        st.markdown("<br>", unsafe_allow_html=True)
        st.info(f"Todos los clips se van a escalar a **{ancho}×{alto}** con barras negras si hace falta.")

    # ── Resultado previo ────────────────────────────────────────────────────────
    st.markdown("---")
    ruta_previa = st.session_state.get("comp_resultado_ruta", "")
    if ruta_previa and Path(ruta_previa).exists():
        _mostrar_resultado_compilacion(ruta_previa)
        return

    # ── Botón unir (solo si no hay compilación guardada) ───────────────────────
    unir = st.button("🎬 Unir todos los clips", type="primary", use_container_width=True, key="comp_unir")

    if not unir:
        return

    if len(clips) < 2:
        st.warning("⚠️ Necesitás al menos 2 clips para hacer una compilación.")
        return

    rutas_ordenadas = [clips[i]["ruta"] for i in orden]

    # Verificar que los archivos existen
    faltantes = [r for r in rutas_ordenadas if not Path(r).exists()]
    if faltantes:
        st.error(
            f"❌ {len(faltantes)} archivos no se encuentran en disco. "
            "Volvé al **Feed de TikTok** y descargá los videos de nuevo."
        )
        if st.button("📱 Ir al Feed de TikTok", key="btn_feed_faltantes"):
            st.session_state.pop("compilador_clips", None)
            st.session_state.pop("feed_descargados", None)
            st.session_state.modulo_activo = "Feed TikTok"
            st.rerun()
        return

    # Detectar codecs BVC2 antes de intentar compilar
    bvc2_indices = []
    for i, ruta in enumerate(rutas_ordenadas):
        try:
            probe = subprocess.run(
                ["/opt/homebrew/bin/ffprobe", "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name,codec_tag_string",
                 "-of", "default=noprint_wrappers=1", ruta],
                capture_output=True, text=True, timeout=10,
            )
            out = probe.stdout.lower()
            if "bvc2" in out or "codec_name=none" in out:
                bvc2_indices.append(i + 1)
        except Exception:
            pass

    if bvc2_indices:
        st.error(
            f"❌ Los clips {bvc2_indices} usan el codec BVC2 de TikTok que FFmpeg no puede procesar. "
            "Volvé al **Feed de TikTok**, descargá los videos de nuevo y volvé al Compilador."
        )
        if st.button("📱 Volver al Feed de TikTok", type="primary", key="btn_feed_bvc2"):
            st.session_state.pop("compilador_clips", None)
            st.session_state.pop("feed_descargados", None)
            st.session_state.modulo_activo = "Feed TikTok"
            st.rerun()
        return

    ancho, alto = RESOLUCIONES[res_label]
    carpeta_out = Path(tempfile.gettempdir()) / "compilador_out"
    carpeta_out.mkdir(exist_ok=True)
    # Nombre único por compilación para evitar que el browser cachee el archivo anterior
    ts = int(time.time())
    ruta_salida = str(carpeta_out / f"compilacion_{ts}.mp4")
    barra = st.progress(0, text="Normalizando clips...")

    norm_dir = carpeta_out / "norm"
    norm_dir.mkdir(exist_ok=True)
    videos_norm = []

    for i, ruta in enumerate(rutas_ordenadas):
        barra.progress(
            i / (len(rutas_ordenadas) + 1),
            text=f"Procesando clip {i+1}/{len(rutas_ordenadas)}..."
        )
        norm = str(norm_dir / f"clip_{i:03d}.mp4")
        ok, err = normalizar_video(ruta, norm, ancho, alto)
        if ok:
            videos_norm.append(norm)
        else:
            barra.empty()
            st.error(f"❌ Error en clip {i+1}: {err}")
            return

    barra.progress(0.95, text="Concatenando video final...")

    lista_txt = str(carpeta_out / "lista.txt")
    with open(lista_txt, "w") as f:
        for ruta in videos_norm:
            f.write(f"file '{ruta}'\n")

    ok, err = run_ffmpeg([
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", lista_txt, "-c", "copy", "-y", ruta_salida
    ])
    barra.progress(1.0, text="✅ Compilación lista")

    if ok and Path(ruta_salida).exists():
        st.session_state["comp_resultado_ruta"] = ruta_salida
        st.session_state["video_compilado_ruta"] = ruta_salida
        st.session_state["video_compilado_listo"] = True
        st.session_state["ve_ruta"] = ruta_salida
        st.session_state["ve_nombre"] = Path(ruta_salida).name
        st.session_state["video_para_subir"] = ruta_salida
        st.rerun()
    else:
        barra.empty()
        st.error(f"❌ Error al concatenar: {err}")


def _crear_short(ruta_video: str, inicio: float, duracion_s: float) -> tuple[str | None, str]:
    """
    Extrae un segmento del video y lo reencoda en 1080×1920 (formato Short/Reel).
    Retorna (ruta_short, error).
    """
    carpeta = Path(tempfile.gettempdir()) / "compilador_shorts"
    carpeta.mkdir(exist_ok=True)
    ts = int(time.time())
    salida = str(carpeta / f"short_{ts}.mp4")

    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920"
    )
    ok, err = run_ffmpeg([
        "ffmpeg",
        "-ss", str(inicio),
        "-i", ruta_video,
        "-t", str(duracion_s),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30",
        "-y", salida,
    ])
    if ok and Path(salida).exists():
        return salida, ""
    return None, err


def _mostrar_resultado_compilacion(ruta: str):
    """Muestra el resultado de la compilación de forma persistente."""
    dur_final = obtener_duracion(ruta)
    nombre_archivo = Path(ruta).name
    st.success(f"🎉 ¡Compilación lista! Duración: **{fmt_duracion(dur_final)}**")

    with open(ruta, "rb") as f:
        datos = f.read()

    # Key basada en hash del contenido real: garantiza que Streamlit
    # cree un widget nuevo con los bytes correctos aunque el nombre cambie.
    file_hash = hashlib.md5(datos).hexdigest()[:16]
    st.download_button(
        "⬇️ Descargar compilación",
        data=datos,
        file_name=nombre_archivo,
        mime="video/mp4",
        use_container_width=True,
        type="primary",
        key=f"dl_comp_{file_hash}",
    )

    # ── Crear Short ────────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("✂️ Crear Short / Reel (vertical 1080×1920)"):
        st.markdown(
            "Extraé un fragmento del video, convertilo a formato vertical y descargalo "
            "listo para subir como **YouTube Short** o **Instagram Reel**."
        )
        dur_max = min(dur_final, 60.0)
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            ini_short = st.slider(
                "Inicio del Short (seg)",
                0.0, max(0.0, dur_final - 5.0), 0.0, 0.5,
                format="%.1f s",
                key="short_ini",
            )
        with col_s2:
            dur_short = st.slider(
                "Duración del Short (seg)",
                5.0, min(60.0, max(5.0, dur_final - ini_short)), min(30.0, dur_max),
                0.5,
                format="%.1f s",
                key="short_dur",
            )
        st.caption(
            f"Fragmento: {fmt_duracion(ini_short)} → {fmt_duracion(ini_short + dur_short)} "
            f"({dur_short:.1f} s) — se convertirá a 1080×1920 px"
        )

        if st.button("✂️ Generar Short", type="primary", use_container_width=True, key="btn_gen_short"):
            with st.spinner("Recortando y convirtiendo a formato vertical..."):
                ruta_short, err_short = _crear_short(ruta, ini_short, dur_short)
            if ruta_short:
                st.session_state["short_resultado"] = ruta_short
                st.success("✅ Short listo")
            else:
                st.error(f"❌ Error al crear el Short: {err_short}")

        if "short_resultado" in st.session_state:
            rs = st.session_state["short_resultado"]
            if Path(rs).exists():
                st.video(rs)
                with open(rs, "rb") as f:
                    st.download_button(
                        "⬇️ Descargar Short",
                        data=f.read(),
                        file_name="short_youtube.mp4",
                        mime="video/mp4",
                        use_container_width=True,
                        key=f"dl_short_{hashlib.md5(rs.encode()).hexdigest()[:8]}",
                    )

    st.markdown("---")
    st.subheader("¿Qué hacés ahora?")
    col_thumb, col_sync, col_ed, col_sub, col_new = st.columns(5)

    with col_thumb:
        if st.button("🖼️ Thumbnail", use_container_width=True, key="btn_goto_thumb"):
            st.session_state.modulo_activo = "Generador de Thumbnail"
            st.rerun()

    with col_sync:
        if st.button("🎵 Narración\nsincronizada", use_container_width=True, key="btn_goto_sync"):
            st.session_state.modulo_activo = "Sincronizador de Audio"
            st.rerun()

    with col_ed:
        if st.button("🎬 Editar\nvideo", use_container_width=True, key="btn_goto_editor"):
            st.session_state.modulo_activo = "Editor de Video"
            st.rerun()

    with col_sub:
        if st.button("📋 Generar\nSEO", use_container_width=True, key="btn_goto_seo"):
            st.session_state.modulo_activo = "Generador de SEO"
            st.rerun()

    with col_new:
        if st.button("🔄 Nueva\ncompilación", use_container_width=True, key="btn_nueva_comp"):
            st.session_state.pop("comp_resultado_ruta", None)
            st.session_state.pop("short_resultado", None)
            st.rerun()
