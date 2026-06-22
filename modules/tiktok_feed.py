"""
Módulo — Feed de TikTok
Pegá URLs de videos de TikTok, previsualizalos en un grid y descargalos sin marca de agua.
Usa tikwm.com API para metadata y descarga directa (no requiere sesión ni cookies).
"""

import streamlit as st
import requests
import os
import subprocess
import tempfile
import time
import hashlib
from pathlib import Path
from modules.selector_ia import SUBCATEGORIAS, SUB_SUBCATEGORIAS, sub_tag, _buscar_keywords


TIKWM_API = "https://www.tikwm.com/api/"

LINKS_BUSQUEDA = {
    # ── Fútbol (nicho principal) ──────────────────────────────────────────────
    "🔥 Tendencias fútbol":       "https://www.tiktok.com/explore",
    "⚽ Goles épicos":            "https://www.tiktok.com/tag/goles",
    "🏆 Mundial 2026":           "https://www.tiktok.com/tag/mundial2026",
    "🌟 Jugadas de crack":        "https://www.tiktok.com/tag/cracks",
    "😂 Fails de fútbol":         "https://www.tiktok.com/tag/futbolfails",
    "🥅 Atajadas increíbles":     "https://www.tiktok.com/tag/atajadas",
    "🇦🇷 Fútbol argentino":      "https://www.tiktok.com/tag/futbolargentino",
    "🔥 Highlights":              "https://www.tiktok.com/tag/footballhighlights",
    "⚡ Fútbol callejero":        "https://www.tiktok.com/tag/freestylesoccer",
    "💥 Momentos épicos":         "https://www.tiktok.com/tag/futbolmoments",
    "🤣 Reacciones de hinchas":   "https://www.tiktok.com/tag/fansreactions",
    "🏅 Champions League":        "https://www.tiktok.com/tag/championsleague",
    "👦 Jóvenes talentos":        "https://www.tiktok.com/tag/youngtalents",
    "🎯 Penales":                 "https://www.tiktok.com/tag/penales",
    "🇧🇷 Fútbol brasileño":      "https://www.tiktok.com/tag/futbolbrasil",
    "🏟️ Ambiente de estadio":    "https://www.tiktok.com/tag/estadio",
    "🌍 Selecciones del mundo":   "https://www.tiktok.com/tag/selecciones",
    "💪 Entrenamiento":           "https://www.tiktok.com/tag/futboltraining",
}


def obtener_info_tiktok(url: str) -> dict | None:
    """Obtiene metadata de un video de TikTok via tikwm API (no requiere login)."""
    try:
        resp = requests.post(
            TIKWM_API,
            data={"url": url.strip(), "hd": "1"},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0 and data.get("data"):
            d = data["data"]
            autor = d.get("author") or {}
            canal = autor.get("unique_id") or autor.get("nickname") or ""
            return {
                "id": str(d.get("id") or url.split("/")[-1]),
                "titulo": d.get("title") or "Sin título",
                "canal": canal,
                "vistas": int(d.get("play_count") or 0),
                "duracion": int(d.get("duration") or 0),
                "thumbnail": d.get("cover") or d.get("origin_cover") or "",
                "url": url.strip(),
                # hdplay = sin marca de agua (puede ser bvc2); wmplay = con marca de agua pero H.264
                "download_url": d.get("hdplay") or d.get("play") or "",
                "download_url_wm": d.get("wmplay") or "",
            }
        else:
            msg = data.get("msg") or "respuesta vacía"
            st.warning(f"⚠️ `{url[:55]}...` — {msg}")
    except Exception as e:
        st.warning(f"⚠️ Error obteniendo `{url[:55]}...`: {e}")
    return None


_FFPROBE = "/opt/homebrew/bin/ffprobe"
_DESCARGA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.tiktok.com/",
}


_FFMPEG = "/opt/homebrew/bin/ffmpeg"


def _codec_compatible(ruta: str) -> bool:
    """Intenta decodificar 0.5s del video. Retorna False si FFmpeg no tiene decoder."""
    import subprocess
    try:
        # Primero chequear el codec tag (más fiable que codec_name para codecs desconocidos)
        probe = subprocess.run(
            [_FFPROBE, "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,codec_tag_string",
             "-of", "default=noprint_wrappers=1", ruta],
            capture_output=True, text=True, timeout=10,
        )
        output = probe.stdout.lower()
        if "bvc2" in output or "codec_name=none" in output:
            return False

        # Intento real de decodificación para confirmar
        decode = subprocess.run(
            [_FFMPEG, "-v", "error", "-i", ruta, "-t", "0.5", "-f", "null", "-"],
            capture_output=True, text=True, timeout=20,
        )
        bad_keywords = ("no decoder found", "decoder found for: none", "decoding requested")
        return not any(kw in decode.stderr.lower() for kw in bad_keywords)
    except Exception:
        return True  # si no podemos verificar, asumimos compatible


def _http_download(url: str, ruta: str) -> bool:
    """Descarga un archivo HTTP a disco. Retorna True si el archivo es válido."""
    try:
        resp = requests.get(url, stream=True, headers=_DESCARGA_HEADERS, timeout=90)
        resp.raise_for_status()
        with open(ruta, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        return Path(ruta).stat().st_size > 10_000
    except Exception:
        return False


def descargar_tiktok(video: dict, carpeta: str) -> str | None:
    """
    Descarga el video sin marca de agua en H.264 compatible con FFmpeg.
    Orden: hdplay primero (sin watermark), re-encode si es BVC2,
    wmplay solo como último recurso si hdplay no está disponible.
    """
    video_id = video.get("id", "video")

    # 1. Intentar hdplay/play (sin marca de agua)
    url_hd = video.get("download_url", "")
    if url_hd:
        ruta_hd = os.path.join(carpeta, f"{video_id}.mp4")
        if _http_download(url_hd, ruta_hd):
            if _codec_compatible(ruta_hd):
                return ruta_hd
            # BVC2 u otro codec incompatible — re-encodear a H.264
            ruta_conv = os.path.join(carpeta, f"{video_id}_conv.mp4")
            import subprocess as _sp
            res = _sp.run(
                [_FFMPEG, "-i", ruta_hd,
                 "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k", "-y", ruta_conv],
                capture_output=True, timeout=120,
            )
            Path(ruta_hd).unlink(missing_ok=True)
            if res.returncode == 0 and Path(ruta_conv).stat().st_size > 10_000:
                return ruta_conv
            Path(ruta_conv).unlink(missing_ok=True)

    # 2. Fallback: wmplay (H.264 garantizado pero tiene watermark de TikTok)
    url_wm = video.get("download_url_wm", "")
    if url_wm:
        ruta_wm = os.path.join(carpeta, f"{video_id}_wm.mp4")
        if _http_download(url_wm, ruta_wm):
            if _codec_compatible(ruta_wm):
                return ruta_wm
            Path(ruta_wm).unlink(missing_ok=True)

    return None


def fmt_duracion(seg: int) -> str:
    if seg <= 0:
        return "—"
    m, s = divmod(seg, 60)
    return f"{m}:{s:02d}"


def fmt_vistas(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n) if n > 0 else "N/D"


RESOLUCIONES_LARGO = {
    "Horizontal 1920×1080 (YouTube clásico)": (1920, 1080),
    "Vertical 1080×1920 (Shorts / Reels)": (1080, 1920),
    "Cuadrado 1080×1080 (Instagram)": (1080, 1080),
}

_FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg"
_FFPROBE_BIN = "/opt/homebrew/bin/ffprobe"


def _run_ffmpeg(cmd: list) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result.returncode == 0, result.stderr[-800:] if result.returncode != 0 else ""
    except FileNotFoundError:
        return False, "FFmpeg no encontrado. Instalalo con: brew install ffmpeg"
    except Exception as e:
        return False, str(e)


def _normalizar_clip(entrada: str, salida: str, ancho: int, alto: int) -> tuple[bool, str]:
    vf = (
        f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
        f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    ok, err = _run_ffmpeg([
        _FFMPEG_BIN, "-i", entrada,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30",
        "-y", salida,
    ])
    if not ok:
        ok, err = _run_ffmpeg([
            _FFMPEG_BIN, "-i", entrada,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30",
            "-y", salida,
        ])
    return ok, err


def _concat_lista(rutas: list[str], salida: str) -> tuple[bool, str]:
    lista_txt = salida + ".txt"
    with open(lista_txt, "w") as f:
        for r in rutas:
            f.write(f"file '{r}'\n")
    ok, err = _run_ffmpeg([
        _FFMPEG_BIN, "-f", "concat", "-safe", "0",
        "-i", lista_txt, "-c", "copy", "-y", salida,
    ])
    Path(lista_txt).unlink(missing_ok=True)
    return ok, err


def _compilar_video_largo(clips: list[dict], ancho: int, alto: int, barra) -> str | None:
    """
    Procesa clips en grupos de 4, genera un intermedio por grupo y luego los une.
    Retorna la ruta del video final o None si falla.
    """
    ts = int(time.time())
    base_dir = Path(tempfile.gettempdir()) / f"video_largo_{ts}"
    base_dir.mkdir(exist_ok=True)
    norm_dir = base_dir / "norm"
    norm_dir.mkdir(exist_ok=True)

    rutas = [c["ruta"] for c in clips]
    total = len(rutas)

    # Paso 1: normalizar todos los clips
    norm_rutas = []
    for i, ruta in enumerate(rutas):
        barra.progress(
            i / (total * 2),
            text=f"Normalizando clip {i+1}/{total}...",
        )
        norm = str(norm_dir / f"clip_{i:03d}.mp4")
        ok, err = _normalizar_clip(ruta, norm, ancho, alto)
        if not ok:
            st.error(f"❌ Error normalizando clip {i+1}: {err}")
            return None
        norm_rutas.append(norm)

    # Paso 2: concatenar en grupos de 4
    grupos = [norm_rutas[i:i+4] for i in range(0, len(norm_rutas), 4)]
    intermedios = []
    for g_idx, grupo in enumerate(grupos):
        barra.progress(
            (total + g_idx) / (total + len(grupos) + 1),
            text=f"Uniendo grupo {g_idx+1}/{len(grupos)} ({len(grupo)} clips)...",
        )
        salida_grupo = str(base_dir / f"grupo_{g_idx:03d}.mp4")
        ok, err = _concat_lista(grupo, salida_grupo)
        if not ok:
            st.error(f"❌ Error uniendo grupo {g_idx+1}: {err}")
            return None
        intermedios.append(salida_grupo)

    # Paso 3: unir todos los intermedios en el video final
    barra.progress(0.97, text="Generando video final...")
    salida_final = str(base_dir / f"video_largo_{ts}.mp4")
    if len(intermedios) == 1:
        import shutil
        shutil.copy2(intermedios[0], salida_final)
    else:
        ok, err = _concat_lista(intermedios, salida_final)
        if not ok:
            st.error(f"❌ Error al unir los grupos: {err}")
            return None

    barra.progress(1.0, text="✅ Video largo listo")
    return salida_final if Path(salida_final).exists() else None


_BROADCASTERS = {
    "espn", "fox", "beinsports", "dazn", "fifacom", "goal", "marca",
    "mundodeportivo", "sport", "as_", "tycsports", "tntsports", "skysports",
    "bbcsport", "canalplus", "eurosport", "telemundo", "univision",
    "infobae", "clarin", "lanacion", "olé", "ole_com",
}


def _mostrar_versus():
    st.markdown(
        "Buscá contenido de **dos temas distintos** y el bot los mezcla alternados: "
        "A, B, A, B... listo para compilar. Ideal para comparativas (2026 vs 2022, "
        "Argentina vs Brasil, goles vs fails, etc.)."
    )
    st.markdown("---")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("### 🔵 Lado A")
        term_a = st.text_input("Búsqueda Lado A", value="mundial 2026 goles", key="vs_term_a",
                               placeholder="ej: mundial 2026 goles")
    with col_b:
        st.markdown("### 🔴 Lado B")
        term_b = st.text_input("Búsqueda Lado B", value="mundial 2022 goles", key="vs_term_b",
                               placeholder="ej: mundial 2022 goles")

    col_n, col_btn = st.columns([2, 1])
    with col_n:
        n_por_lado = st.slider("Videos por lado", 2, 8, 4, key="vs_n_por_lado",
                               help="Total de videos = Lado A + Lado B")
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        buscar_vs = st.button("🔍 Buscar y mezclar", type="primary",
                              use_container_width=True, key="vs_buscar")

    if buscar_vs:
        if not term_a.strip() or not term_b.strip():
            st.warning("⚠️ Completá los dos términos de búsqueda.")
        else:
            col_prog_a, col_prog_b = st.columns(2)
            with col_prog_a:
                with st.spinner(f"Buscando '{term_a}'..."):
                    crudos_a = _buscar_keywords(term_a.strip(), cantidad=n_por_lado * 4)
            with col_prog_b:
                with st.spinner(f"Buscando '{term_b}'..."):
                    crudos_b = _buscar_keywords(term_b.strip(), cantidad=n_por_lado * 4)

            # Tomar los mejores de cada lado (por vistas) y alternar A, B, A, B...
            top_a = sorted(crudos_a, key=lambda x: x.get("vistas", 0), reverse=True)[:n_por_lado]
            top_b = sorted(crudos_b, key=lambda x: x.get("vistas", 0), reverse=True)[:n_por_lado]
            mezclados = [v for par in zip(top_a, top_b) for v in par]
            # Si un lado tiene más que el otro, agregar los sobrantes al final
            n_min = min(len(top_a), len(top_b))
            mezclados += top_a[n_min:] + top_b[n_min:]

            if mezclados:
                st.session_state["vs_videos"] = mezclados
                st.session_state["vs_term_a_label"] = term_a.strip()
                st.session_state["vs_term_b_label"] = term_b.strip()
                st.rerun()
            else:
                st.error("❌ No se encontraron videos para uno o ambos términos. Probá con otras palabras.")

    vs_videos = st.session_state.get("vs_videos", [])
    if not vs_videos:
        return

    term_a_lbl = st.session_state.get("vs_term_a_label", "Lado A")
    term_b_lbl = st.session_state.get("vs_term_b_label", "Lado B")

    st.markdown("---")
    st.success(f"✅ {len(vs_videos)} videos mezclados — alternando **{term_a_lbl}** y **{term_b_lbl}**")

    COLS = 3
    for i in range(0, len(vs_videos), COLS):
        fila = vs_videos[i:i + COLS]
        cols = st.columns(COLS)
        for j, video in enumerate(fila):
            with cols[j]:
                pos_global = i + j
                lado = "🔵 A" if pos_global % 2 == 0 else "🔴 B"
                st.markdown(f"**{lado}**")
                if video["thumbnail"]:
                    st.image(video["thumbnail"], use_container_width=True)
                titulo = video["titulo"]
                st.markdown(f"**{titulo[:55]}{'...' if len(titulo) > 55 else ''}**")
                st.caption(
                    f"@{video['canal']} · 👁 {fmt_vistas(video['vistas'])} · ⏱ {fmt_duracion(video['duracion'])}"
                )
        st.markdown("")

    st.markdown("---")
    col_dl_vs, col_new_vs = st.columns(2)

    with col_dl_vs:
        if st.button("⬇️ Descargar todos y armar compilación",
                     type="primary", use_container_width=True, key="vs_descargar"):
            if "feed_carpeta" not in st.session_state:
                st.session_state["feed_carpeta"] = tempfile.mkdtemp()
            carpeta = st.session_state["feed_carpeta"]

            descargados = []
            barra = st.progress(0, text="Descargando videos versus...")
            for i, video in enumerate(vs_videos):
                barra.progress(i / len(vs_videos), text=f"Descargando {i+1}/{len(vs_videos)}...")
                ruta = descargar_tiktok(video, carpeta)
                if ruta:
                    descargados.append({
                        "ruta": ruta,
                        "nombre": Path(ruta).name,
                        "titulo": video["titulo"],
                        "canal": video["canal"],
                        "duracion": float(video["duracion"]),
                    })
            barra.progress(1.0, text="✅ Listo")

            if descargados:
                st.session_state["feed_descargados"] = descargados
                st.session_state["compilador_clips"] = descargados
                st.success(f"✅ {len(descargados)} videos listos. Ir al **Compilador** para unirlos.")
                st.rerun()
            else:
                st.error("❌ No se pudo descargar ningún video.")

    with col_new_vs:
        if st.button("🔄 Nueva búsqueda versus", use_container_width=True, key="vs_limpiar"):
            st.session_state.pop("vs_videos", None)
            st.session_state.pop("vs_term_a_label", None)
            st.session_state.pop("vs_term_b_label", None)
            st.rerun()

    if st.session_state.get("feed_descargados"):
        if st.button("🎞️ Ir al Compilador →", use_container_width=True,
                     type="primary", key="vs_goto_comp"):
            st.session_state["compilador_clips"] = st.session_state["feed_descargados"]
            st.session_state.modulo_activo = "Compilador"
            st.rerun()


def mostrar_tiktok_feed():
    st.title("📱 Feed de TikTok")

    modo = st.radio(
        "Modo",
        ["📱 Feed Normal", "⚔️ Versus"],
        horizontal=True,
        key="feed_modo",
        label_visibility="collapsed",
    )

    if modo == "⚔️ Versus":
        _mostrar_versus()
        return

    st.markdown("Explorá TikTok, copiá las URLs de los videos que te gusten y descargalos **sin marca de agua** — sin necesidad de login ni cookies.")

    # ── Atajos para abrir TikTok ──────────────────────────────────────────────
    st.markdown("### 1. Encontrá los videos en TikTok")
    st.caption("Abrí TikTok en el navegador, buscá por el nicho que quieras y copiá las URLs de los videos que más te gusten.")

    nicho_label = st.selectbox("Categoría", list(LINKS_BUSQUEDA.keys()), key="feed_nicho")

    if nicho_label in SUBCATEGORIAS:
        sub_opciones = list(SUBCATEGORIAS[nicho_label].keys())
        sub_label = st.selectbox("Subcategoría", sub_opciones, key="feed_subcat")

        # Tercer nivel: sub-subcategorías (ej: Partidos recientes → Goles)
        sub_sub_label = None
        sub_sub_map = SUB_SUBCATEGORIAS.get(nicho_label, {}).get(sub_label)
        if sub_sub_map:
            sub_sub_label = st.selectbox("Filtro", list(sub_sub_map.keys()), key="feed_subsubcat")
            sval = sub_sub_map[sub_sub_label]
        else:
            sval = SUBCATEGORIAS[nicho_label][sub_label]

        tag_str = sub_tag(sval)
        if sub_label == "Todos" and not sub_sub_label:
            tiktok_url = LINKS_BUSQUEDA[nicho_label]
        else:
            tiktok_url = f"https://www.tiktok.com/search?q={tag_str.replace(' ', '+')}"
    else:
        tiktok_url = LINKS_BUSQUEDA[nicho_label]
        sub_label = None
        sub_sub_label = None

    partes_link = [f"**{nicho_label}**"]
    if sub_label and sub_label != "Todos":
        partes_link.append(sub_label)
    if sub_sub_label and sub_sub_label != "Todos":
        partes_link.append(sub_sub_label)
    link_texto = " › ".join(partes_link)
    st.markdown(f"[🔗 Abrir {link_texto} en TikTok →]({tiktok_url})")

    st.markdown("---")

    # ── Input de URLs ─────────────────────────────────────────────────────────
    st.markdown("### 2. Pegá las URLs de los videos")
    st.caption("Copiá el enlace de cada video desde TikTok (Compartir → Copiar enlace) y pegá uno por línea.")

    urls_texto = st.text_area(
        "URLs de TikTok (una por línea)",
        placeholder=(
            "https://www.tiktok.com/@usuario/video/7123456789012345678\n"
            "https://vm.tiktok.com/ZMxxxxxxxx/\n"
            "https://www.tiktok.com/@otro/video/7987654321098765432"
        ),
        height=160,
        key="feed_urls_input",
    )

    cargar = st.button("🔍 Cargar videos", type="primary", use_container_width=True)

    if cargar:
        urls = [u.strip() for u in urls_texto.strip().splitlines() if u.strip()]
        if not urls:
            st.warning("⚠️ Pegá al menos una URL de TikTok.")
        else:
            barra = st.progress(0, text="Obteniendo información...")
            videos = []
            for i, url in enumerate(urls):
                barra.progress(i / len(urls), text=f"Cargando video {i+1}/{len(urls)}...")
                info = obtener_info_tiktok(url)
                if info:
                    videos.append(info)
            barra.progress(1.0, text="✅ Listo")

            if videos:
                st.session_state["feed_videos"] = videos
                for v in videos:
                    st.session_state.pop(f"chk_{v['id']}", None)
                st.session_state.pop("feed_descargados", None)
                st.rerun()
            else:
                st.error("❌ No se pudo obtener información de ningún video. Verificá que las URLs sean correctas y que tengas conexión a internet.")

    if "feed_videos" not in st.session_state:
        return

    videos = st.session_state["feed_videos"]

    # ── Filtro copyright ──────────────────────────────────────────────────────
    with st.expander("🔒 Filtro anti-copyright (opcional)"):
        st.caption(
            "Elimina videos de medios oficiales y canales de TV para reducir el riesgo de claims. "
            "No es 100% efectivo — el contenido de fútbol puede recibir claims igual."
        )
        aplicar_filtro_cr = st.button(
            "🔒 Aplicar filtro ahora",
            key="btn_filtro_cr",
            use_container_width=True,
        )
        if aplicar_filtro_cr:
            antes = len(videos)
            videos_filtrados = [
                v for v in videos
                if v.get("duracion", 999) <= 60
                and not any(p in v.get("canal", "").lower() for p in _BROADCASTERS)
                and not any(
                    p in v.get("titulo", "").lower()
                    for p in ("official", "broadcast", "tv", "highlights oficial")
                )
            ]
            st.session_state["feed_videos"] = videos_filtrados
            eliminados = antes - len(videos_filtrados)
            if eliminados:
                st.info(f"🔒 Filtro aplicado: {eliminados} videos eliminados → {len(videos_filtrados)} restantes.")
            else:
                st.success("✅ Ningún video fue detectado como oficial. El filtro no eliminó nada.")
            st.rerun()

    # ── Barra de selección rápida ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 3. Seleccioná y descargá")

    col_info, col_all, col_none, col_clear = st.columns([3, 1, 1, 1])
    with col_info:
        n_sel = sum(1 for v in videos if st.session_state.get(f"chk_{v['id']}", False))
        st.markdown(f"**{len(videos)} videos cargados** — {n_sel} seleccionados")
    with col_all:
        if st.button("Seleccionar todos", use_container_width=True):
            for v in videos:
                st.session_state[f"chk_{v['id']}"] = True
            st.rerun()
    with col_none:
        if st.button("Deseleccionar", use_container_width=True):
            for v in videos:
                st.session_state[f"chk_{v['id']}"] = False
            st.rerun()
    with col_clear:
        if st.button("🗑️ Limpiar todo", use_container_width=True, help="Resetea el proceso completo para empezar un nuevo video"):
            for v in videos:
                st.session_state.pop(f"chk_{v['id']}", None)
            # Feed
            for key in ("feed_videos", "feed_descargados", "compilador_clips", "feed_carpeta"):
                st.session_state.pop(key, None)
            # Compilador
            for key in ("comp_resultado_ruta", "video_compilado_ruta", "video_compilado_listo",
                        "ve_ruta", "ve_nombre", "comp_orden"):
                st.session_state.pop(key, None)
            # SEO
            for key in ("seo_resultado", "seo_fuente_video", "seo_titulo_edit",
                        "seo_desc_edit", "seo_tags_edit"):
                st.session_state.pop(key, None)
            # Limpiar cache de frames SEO (claves dinámicas seo_frames_*)
            frame_keys = [k for k in st.session_state if k.startswith("seo_frames_")]
            for key in frame_keys:
                st.session_state.pop(key, None)
            # Uploader
            for key in ("video_para_subir", "ultimo_titulo", "ultima_descripcion", "ultimos_tags"):
                st.session_state.pop(key, None)
            st.rerun()

    # ── Grid de videos (3 columnas) ───────────────────────────────────────────
    COLS = 3
    for i in range(0, len(videos), COLS):
        fila = videos[i:i + COLS]
        cols = st.columns(COLS)
        for j, video in enumerate(fila):
            with cols[j]:
                st.checkbox(f"Seleccionar #{i+j+1}", key=f"chk_{video['id']}")
                if video["thumbnail"]:
                    st.image(video["thumbnail"], use_container_width=True)
                else:
                    st.markdown(
                        "<div style='background:#111;height:180px;border-radius:8px;"
                        "display:flex;align-items:center;justify-content:center;"
                        "color:#888;font-size:32px'>🎵</div>",
                        unsafe_allow_html=True,
                    )
                titulo = video["titulo"]
                st.markdown(f"**{titulo[:55]}{'...' if len(titulo) > 55 else ''}**")
                st.caption(
                    f"@{video['canal']} · 👁 {fmt_vistas(video['vistas'])} · ⏱ {fmt_duracion(video['duracion'])}"
                )
                st.markdown(f"[Ver en TikTok ↗]({video['url']})")
        st.markdown("")

    # ── Panel de descarga ─────────────────────────────────────────────────────
    st.markdown("---")

    ids_sel = [v["id"] for v in videos if st.session_state.get(f"chk_{v['id']}", False)]
    n_sel = len(ids_sel)

    if n_sel == 0:
        st.info("Seleccioná al menos un video con el checkbox para descargarlo.")
        return

    col_dl, col_go = st.columns(2)

    with col_dl:
        descargar_btn = st.button(
            f"⬇️ Descargar {n_sel} video{'s' if n_sel > 1 else ''} sin marca de agua",
            type="primary",
            use_container_width=True,
        )

    with col_go:
        if st.session_state.get("feed_descargados"):
            if st.button("🎞️ Ir al Compilador →", use_container_width=True, type="primary"):
                st.session_state["compilador_clips"] = st.session_state["feed_descargados"]
                st.session_state.modulo_activo = "Compilador"
                st.rerun()

    if descargar_btn:
        videos_sel = [v for v in videos if v["id"] in ids_sel]

        if "feed_carpeta" not in st.session_state:
            st.session_state["feed_carpeta"] = tempfile.mkdtemp()
        carpeta = st.session_state["feed_carpeta"]

        descargados = []
        errores = []
        barra = st.progress(0, text="Iniciando descargas...")

        for i, video in enumerate(videos_sel):
            barra.progress(
                i / len(videos_sel),
                text=f"Descargando {i+1}/{len(videos_sel)}: @{video['canal']}...",
            )
            ruta = descargar_tiktok(video, carpeta)
            if ruta:
                descargados.append({
                    "ruta": ruta,
                    "nombre": Path(ruta).name,
                    "titulo": video["titulo"],
                    "canal": video["canal"],
                    "duracion": float(video["duracion"]),
                })
                try:
                    from modules.historial import guardar_descarga_tiktok
                    guardar_descarga_tiktok(video["url"], video["titulo"], video["canal"])
                except Exception:
                    pass
            else:
                errores.append(video["titulo"][:40])

        barra.progress(1.0, text="✅ Descargas completadas")

        if errores:
            st.warning(f"⚠️ No se pudieron descargar {len(errores)} videos: {', '.join(errores)}")

        if descargados:
            st.session_state["feed_descargados"] = descargados
            st.session_state["compilador_clips"] = descargados
            st.success(f"✅ {len(descargados)} videos descargados. Ahora podés ir al **Compilador** para unirlos.")
            st.rerun()
        else:
            st.error("❌ No se pudo descargar ningún video. Verificá tu conexión a internet.")

    # Resumen de descargados
    if st.session_state.get("feed_descargados") and not descargar_btn:
        descargados = st.session_state["feed_descargados"]
        st.success(f"✅ {len(descargados)} videos listos para compilar.")
        with st.expander("Ver videos descargados"):
            for d in descargados:
                st.markdown(f"- **{d['titulo'][:60]}** · ⏱ {fmt_duracion(int(d.get('duracion', 0)))}")

        # ── Compilar video largo (grupos de 4) ───────────────────────────────
        st.markdown("---")
        st.subheader("🎬 Compilar video largo")
        st.caption(
            f"Une los {len(descargados)} videos de a 4 por vez para generar un solo video largo. "
            "Ideal para YouTube en formato horizontal."
        )

        grupos_preview = [descargados[i:i+4] for i in range(0, len(descargados), 4)]
        st.info(
            f"**{len(descargados)} videos → {len(grupos_preview)} grupo{'s' if len(grupos_preview) > 1 else ''}** "
            + " + ".join(f"{len(g)}" for g in grupos_preview)
            + " clips cada uno → 1 video final"
        )

        res_label = st.selectbox(
            "Resolución del video largo",
            list(RESOLUCIONES_LARGO.keys()),
            key="feed_largo_res",
        )
        ancho_largo, alto_largo = RESOLUCIONES_LARGO[res_label]

        col_largo, col_result = st.columns(2)

        with col_largo:
            compilar_largo_btn = st.button(
                f"🎬 Compilar video largo ({len(descargados)} clips)",
                type="primary",
                use_container_width=True,
                key="btn_compilar_largo",
            )

        with col_result:
            if st.session_state.get("feed_video_largo_ruta"):
                ruta_largo = st.session_state["feed_video_largo_ruta"]
                if Path(ruta_largo).exists():
                    if st.button("🎬 Editar / Subir video largo →", use_container_width=True, key="btn_goto_editor_largo"):
                        st.session_state["ve_ruta"] = ruta_largo
                        st.session_state["ve_nombre"] = Path(ruta_largo).name
                        st.session_state["video_para_subir"] = ruta_largo
                        st.session_state.modulo_activo = "Editor de Video"
                        st.rerun()

        if compilar_largo_btn:
            barra_largo = st.progress(0, text="Iniciando compilación en grupos...")
            resultado = _compilar_video_largo(descargados, ancho_largo, alto_largo, barra_largo)
            if resultado:
                st.session_state["feed_video_largo_ruta"] = resultado
                st.session_state["ve_ruta"] = resultado
                st.session_state["ve_nombre"] = Path(resultado).name
                st.session_state["video_para_subir"] = resultado
                st.rerun()

        if st.session_state.get("feed_video_largo_ruta") and not compilar_largo_btn:
            ruta_largo = st.session_state["feed_video_largo_ruta"]
            if Path(ruta_largo).exists():
                st.success("🎉 ¡Video largo listo!")
                with open(ruta_largo, "rb") as f:
                    datos_largo = f.read()
                file_hash = hashlib.md5(datos_largo).hexdigest()[:16]
                st.download_button(
                    "⬇️ Descargar video largo",
                    data=datos_largo,
                    file_name=Path(ruta_largo).name,
                    mime="video/mp4",
                    use_container_width=True,
                    key=f"dl_largo_{file_hash}",
                )
