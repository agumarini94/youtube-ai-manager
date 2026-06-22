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

FFMPEG  = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

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
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", ruta],
            capture_output=True, text=True, timeout=15
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0.0


def normalizar_video(entrada: str, salida: str, ancho: int, alto: int,
                     barra_texto: str = "", max_seg: int | None = None) -> tuple[bool, str]:
    """Re-encoda el video al formato y resolución estándar para garantizar compatibilidad en el concat."""
    vf = (
        f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
        f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    trim = ["-t", str(max_seg)] if max_seg else []
    ok, err = run_ffmpeg([
        FFMPEG, "-i", entrada,
        *trim,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30",
        "-y", salida
    ])
    if not ok:
        ok, err = run_ffmpeg([
            FFMPEG, "-i", entrada,
            *trim,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30",
            "-y", salida
        ])
    return ok, err


def concatenar_clips(rutas: list[str], salida: str, ancho: int, alto: int,
                     max_seg_por_clip: int | None = None) -> tuple[bool, str]:
    """
    Normaliza todos los clips al mismo formato y los concatena.
    max_seg_por_clip: si se indica, recorta cada clip a esa duración (útil para reels).
    Siempre re-encoda antes del concat para evitar problemas de codec mixto.
    """
    d = Path(salida).parent
    norm_dir = d / "norm"
    norm_dir.mkdir(exist_ok=True)

    videos_norm = []
    for i, ruta in enumerate(rutas):
        norm = str(norm_dir / f"clip_{i:03d}.mp4")
        ok, err = normalizar_video(ruta, norm, ancho, alto, max_seg=max_seg_por_clip)
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
        FFMPEG, "-f", "concat", "-safe", "0",
        "-i", lista, "-c", "copy", "-y", salida
    ])


def agregar_overlay_suscripcion(entrada: str, salida: str, segundos: float = 4.0) -> tuple[bool, str]:
    """Superpone un botón rojo 'SUSCRIBITE AL CANAL' en los últimos N segundos del video."""
    dur = obtener_duracion(entrada)
    if dur <= 0:
        return False, "No se pudo leer la duración del video."

    t_ini = max(0.0, dur - segundos)
    enable = f"between(t,{t_ini:.2f},{dur:.2f})"

    vf = (
        f"drawbox=x=(iw-400)/2:y=ih-135:w=400:h=80:color=0xFF0000@0.88:t=fill:enable='{enable}',"
        f"drawtext=text='SUSCRIBITE AL CANAL':fontsize=34:fontcolor=white"
        f":x=(w-text_w)/2:y=h-115:enable='{enable}'"
    )
    return run_ffmpeg([FFMPEG, "-i", entrada, "-vf", vf, "-codec:a", "copy", "-y", salida])


def sugerir_orden_claude(clips: list[dict]) -> list[int] | None:
    """Llama a Claude para sugerir el orden óptimo de clips según retención."""
    import os, json, anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    info_clips = []
    for i, c in enumerate(clips):
        dur = c.get("duracion", 0)
        nombre = c.get("titulo") or c.get("nombre", f"Clip {i+1}")
        m, s = divmod(int(dur), 60)
        info_clips.append(f"{i}: \"{nombre[:80]}\" ({m}:{s:02d})")

    prompt = f"""Tenés {len(clips)} clips de video para una compilación de YouTube. Sugerí el orden óptimo para maximizar la retención.

CLIPS (índice: nombre — duración):
{chr(10).join(info_clips)}

CRITERIOS:
- Posición 1: el clip más impactante o gracioso (hook — engancha en los primeros 3 segundos)
- Posiciones 2-3: clips de alta intensidad para mantener el engagement inicial
- Posiciones intermedias: clips de intensidad media
- Último clip: cierre fuerte, memorable (NO el más débil)

Inferí la intensidad desde el nombre del clip. Si los nombres no dan información, priorizá clips más cortos al inicio.

Respondé SOLO con un JSON array de índices del 0 al {len(clips)-1}, en el orden óptimo.
Ejemplo con 4 clips: [2, 0, 3, 1]
No incluyas explicaciones ni texto adicional."""

    try:
        cliente = anthropic.Anthropic(api_key=api_key)
        respuesta = cliente.messages.create(
            model="claude-opus-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        texto = respuesta.content[0].text.strip()
        if texto.startswith("```"):
            texto = texto.split("```")[1]
            if texto.startswith("json"):
                texto = texto[4:]
        if texto.endswith("```"):
            texto = texto[:-3]
        orden = json.loads(texto.strip())
        if (
            isinstance(orden, list)
            and len(orden) == len(clips)
            and set(orden) == set(range(len(clips)))
        ):
            return orden
        return None
    except Exception:
        return None


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

    # ── Orden sugerido por Claude (opcional) ───────────────────────────────────
    sugerir = st.checkbox(
        "📊 Sugerir orden óptimo de clips con IA",
        key="comp_sugerir_orden",
        help="Claude analiza los nombres de los clips y sugiere el orden para maximizar la retención."
    )

    if sugerir:
        if st.button("🤖 Obtener sugerencia de Claude", key="comp_btn_sugerir", use_container_width=True):
            with st.spinner("Claude está analizando los clips..."):
                orden_sugerido = sugerir_orden_claude(clips)
            if orden_sugerido is not None:
                st.session_state["comp_orden_sugerido"] = orden_sugerido
            else:
                st.error("❌ No se pudo obtener una sugerencia. Verificá que ANTHROPIC_API_KEY esté configurada.")

        orden_sugerido = st.session_state.get("comp_orden_sugerido")
        if orden_sugerido and len(orden_sugerido) == len(clips):
            st.markdown("**Orden sugerido:**")
            descripciones_rol = {0: "🪝 Hook", 1: "🔥 Alta", 2: "🔥 Alta"}
            for pos, idx in enumerate(orden_sugerido):
                clip = clips[idx]
                if pos == 0:
                    rol = "🪝 Hook"
                elif pos in (1, 2):
                    rol = "🔥 Alta intensidad"
                elif pos == len(orden_sugerido) - 1:
                    rol = "🏁 Cierre"
                else:
                    rol = "▪️ Media intensidad"
                st.caption(f"**{pos+1}.** {clip['titulo'][:55]} — {rol}")
            if st.button("✅ Aplicar orden sugerido", key="comp_aplicar_sugerido", type="primary", use_container_width=True):
                st.session_state["comp_orden"] = orden_sugerido
                st.session_state.pop("comp_orden_sugerido", None)
                st.rerun()

    # ── Configuración de salida ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⚙️ Configuración de salida")

    _AMBOS = "📺+🎬 Ambos: Short vertical (1080×1920) + Video horizontal (1920×1080)"
    opciones_res = [_AMBOS] + list(RESOLUCIONES.keys())

    col_res, col_info2 = st.columns([2, 2])
    with col_res:
        res_label = st.selectbox("Formato de salida", opciones_res, key="comp_res")
    with col_info2:
        st.markdown("<br>", unsafe_allow_html=True)
        if res_label == _AMBOS:
            st.info("Se compilan **dos versiones** con los mismos clips: Short vertical (1080×1920) + Video horizontal (1920×1080).")
        else:
            ancho, alto = RESOLUCIONES[res_label]
            st.info(f"Todos los clips se van a escalar a **{ancho}×{alto}** con barras negras si hace falta.")

    # ── Resultado previo ────────────────────────────────────────────────────────
    st.markdown("---")
    ruta_previa = st.session_state.get("comp_resultado_ruta", "")
    if ruta_previa and Path(ruta_previa).exists():
        ruta_horiz_previa = st.session_state.get("comp_resultado_horizontal")
        _mostrar_resultado_compilacion(ruta_previa, ruta_horiz_previa)
        return

    # ── Un solo clip en formato único: aplicar overlay y saltar compilación ──
    _rutas = [clips[i]["ruta"] for i in orden]
    _dual_check = (res_label == _AMBOS)
    if len(_rutas) == 1 and not _dual_check and Path(_rutas[0]).exists():
        ruta_unica = _rutas[0]
        carpeta_single = Path(tempfile.gettempdir()) / "compilador_out"
        carpeta_single.mkdir(exist_ok=True)
        ts_single = int(time.time())
        ruta_single_ov = str(carpeta_single / f"compilacion_single_{ts_single}_ov.mp4")
        ok_ov, _ = agregar_overlay_suscripcion(ruta_unica, ruta_single_ov)
        ruta_final_single = ruta_single_ov if ok_ov else ruta_unica
        st.session_state["comp_resultado_ruta"] = ruta_final_single
        st.session_state["comp_resultado_horizontal"] = None
        st.session_state["video_compilado_ruta"] = ruta_final_single
        st.session_state["video_compilado_listo"] = True
        st.session_state["ve_ruta"] = ruta_final_single
        st.session_state["ve_nombre"] = Path(ruta_final_single).name
        st.session_state["video_para_subir"] = ruta_final_single
        st.rerun()

    # ── Botón unir (solo si no hay compilación guardada) ───────────────────────
    unir = st.button("🎬 Unir todos los clips", type="primary", use_container_width=True, key="comp_unir")

    if not unir:
        return

    if len(clips) < 1:
        st.warning("⚠️ No hay clips para compilar.")
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

    dual = (res_label == _AMBOS)
    if dual:
        formatos_a_compilar = [("vertical", 1080, 1920), ("horizontal", 1920, 1080)]
    else:
        ancho, alto = RESOLUCIONES[res_label]
        formatos_a_compilar = [("salida", ancho, alto)]

    carpeta_out = Path(tempfile.gettempdir()) / "compilador_out"
    carpeta_out.mkdir(exist_ok=True)
    ts = int(time.time())

    total_pasos = len(rutas_ordenadas) * len(formatos_a_compilar) + len(formatos_a_compilar)
    paso_actual = 0
    barra = st.progress(0, text="Iniciando...")
    resultados = {}

    _LABELS = {"vertical": "Short vertical", "horizontal": "Video horizontal", "salida": "Video"}

    for sufijo, ancho, alto in formatos_a_compilar:
        label = _LABELS[sufijo]
        norm_dir = carpeta_out / f"norm_{sufijo}"
        norm_dir.mkdir(exist_ok=True)
        videos_norm = []

        for i, ruta in enumerate(rutas_ordenadas):
            barra.progress(paso_actual / total_pasos, text=f"{label}: clip {i+1}/{len(rutas_ordenadas)}...")
            norm = str(norm_dir / f"clip_{i:03d}.mp4")
            ok, err = normalizar_video(ruta, norm, ancho, alto)
            if ok:
                videos_norm.append(norm)
                paso_actual += 1
            else:
                barra.empty()
                st.error(f"❌ Error en clip {i+1} ({label}): {err}")
                return

        barra.progress(paso_actual / total_pasos, text=f"Concatenando {label}...")
        ruta_salida = str(carpeta_out / f"compilacion_{sufijo}_{ts}.mp4")
        lista_txt = str(carpeta_out / f"lista_{sufijo}.txt")
        with open(lista_txt, "w") as f:
            for r in videos_norm:
                f.write(f"file '{r}'\n")
        ok, err = run_ffmpeg([
            FFMPEG, "-f", "concat", "-safe", "0",
            "-i", lista_txt, "-c", "copy", "-y", ruta_salida
        ])
        paso_actual += 1

        if not ok:
            barra.empty()
            st.error(f"❌ Error al concatenar {label}: {err}")
            return

        barra.progress(paso_actual / total_pasos, text=f"Agregando campanita de suscripción ({label})...")
        ruta_overlay = str(carpeta_out / f"compilacion_{sufijo}_{ts}_ov.mp4")
        ok_ov, _ = agregar_overlay_suscripcion(ruta_salida, ruta_overlay)
        if ok_ov:
            ruta_salida = ruta_overlay

        resultados[sufijo] = ruta_salida

    barra.progress(1.0, text="✅ ¡Listo!")

    ruta_principal = resultados.get("vertical") or resultados.get("salida")
    ruta_horiz = resultados.get("horizontal")

    if ruta_principal and Path(ruta_principal).exists():
        st.session_state["comp_resultado_ruta"] = ruta_principal
        st.session_state["comp_resultado_horizontal"] = ruta_horiz
        st.session_state["video_compilado_ruta"] = ruta_principal
        st.session_state["video_compilado_listo"] = True
        st.session_state["ve_ruta"] = ruta_principal
        st.session_state["ve_nombre"] = Path(ruta_principal).name
        st.session_state["video_para_subir"] = ruta_principal
        st.rerun()
    else:
        barra.empty()
        st.error("❌ Error al generar el video.")


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
        FFMPEG,
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


def _mostrar_resultado_compilacion(ruta: str, ruta_horizontal: str | None = None):
    """Muestra el resultado de la compilación de forma persistente."""
    dur_final = obtener_duracion(ruta)
    nombre_archivo = Path(ruta).name
    tiene_dual = ruta_horizontal and Path(ruta_horizontal).exists()

    if tiene_dual:
        st.success("🎉 ¡Ambas compilaciones listas!")
        col_v, col_h = st.columns(2)
        with col_v:
            st.markdown("**🎬 Short / Reel vertical (1080×1920)**")
            st.caption(f"Duración: {fmt_duracion(dur_final)}")
            with open(ruta, "rb") as f:
                datos_v = f.read()
            st.download_button(
                "⬇️ Descargar Short",
                data=datos_v,
                file_name=f"short_{nombre_archivo}",
                mime="video/mp4",
                use_container_width=True,
                type="primary",
                key=f"dl_short_{hashlib.md5(datos_v).hexdigest()[:16]}",
            )
        with col_h:
            dur_h = obtener_duracion(ruta_horizontal)
            nombre_h = Path(ruta_horizontal).name
            st.markdown("**📺 Video horizontal (1920×1080)**")
            st.caption(f"Duración: {fmt_duracion(dur_h)}")
            with open(ruta_horizontal, "rb") as f:
                datos_h = f.read()
            st.download_button(
                "⬇️ Descargar Video",
                data=datos_h,
                file_name=f"video_{nombre_h}",
                mime="video/mp4",
                use_container_width=True,
                type="primary",
                key=f"dl_video_{hashlib.md5(datos_h).hexdigest()[:16]}",
            )
    else:
        st.success(f"🎉 ¡Compilación lista! Duración: **{fmt_duracion(dur_final)}**")
        with open(ruta, "rb") as f:
            datos = f.read()
        st.download_button(
            "⬇️ Descargar compilación",
            data=datos,
            file_name=nombre_archivo,
            mime="video/mp4",
            use_container_width=True,
            type="primary",
            key=f"dl_comp_{hashlib.md5(datos).hexdigest()[:16]}",
        )

    # ── Crear Short (solo si no se compilaron ambos ya) ───────────────────────
    if not tiene_dual:
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

    # ── Música de fondo ────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("🎵 Agregar música de fondo"):
        from modules.music_manager import listar_tracks, buscar_y_descargar, mezclar_musica as _mezclar
        import time as _time

        tracks = listar_tracks()

        col_si, col_btn_si = st.columns([3, 1])
        with col_si:
            nombre_cancion = st.text_input(
                "Buscar canción (se descarga de YouTube)",
                placeholder="Ej: Flowers Miley Cyrus / phonk drift 2025",
                key="comp_music_search",
            )
        with col_btn_si:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔍 Buscar", key="comp_music_btn_buscar", use_container_width=True):
                if nombre_cancion:
                    with st.spinner(f"Buscando {nombre_cancion}..."):
                        track_dl = buscar_y_descargar(nombre_cancion)
                    if track_dl:
                        st.session_state["comp_music_track"] = track_dl["ruta"]
                        st.success(f"✅ {track_dl['nombre']}")
                        st.rerun()
                    else:
                        st.error("❌ No encontré esa canción.")

        if tracks:
            opciones = ["— Elegir de biblioteca —"] + [t["nombre"] for t in tracks]
            sel = st.selectbox("O elegí un track guardado", opciones, key="comp_music_sel")
            if sel != opciones[0]:
                st.session_state["comp_music_track"] = next(t["ruta"] for t in tracks if t["nombre"] == sel)

        track_ruta = st.session_state.get("comp_music_track")
        if track_ruta and Path(track_ruta).exists():
            st.info(f"🎵 Seleccionada: **{Path(track_ruta).stem}**")
            volumen = st.slider("Volumen de la música", 5, 50, 18, 1, format="%d%%", key="comp_music_vol")
            if st.button("🎵 Aplicar al video", type="primary", use_container_width=True, key="comp_music_apply"):
                with st.spinner("Mezclando música de fondo..."):
                    carpeta_m = Path(tempfile.gettempdir()) / "compilador_out"
                    carpeta_m.mkdir(exist_ok=True)
                    salida_m = str(carpeta_m / f"compilacion_musica_{int(_time.time())}.mp4")
                    ok_m = _mezclar(ruta, track_ruta, salida_m, volumen / 100)
                if ok_m:
                    st.session_state["comp_resultado_ruta"] = salida_m
                    st.session_state["video_compilado_ruta"] = salida_m
                    st.session_state["video_para_subir"] = salida_m
                    st.session_state["ve_ruta"] = salida_m
                    st.session_state["ve_nombre"] = Path(salida_m).name
                    st.session_state.pop("comp_music_track", None)
                    st.rerun()
                else:
                    st.error("❌ Error al mezclar la música.")

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
