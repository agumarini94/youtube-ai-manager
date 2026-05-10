"""
Proceso Guiado — Flujo completo paso a paso
Guía al usuario desde buscar una tendencia hasta subir el video a YouTube.
"""

import streamlit as st
import os
import tempfile
import datetime
from pathlib import Path
from modules.trends import buscar_en_youtube, buscar_en_tiktok, rankear_con_ia, NICHOS_YOUTUBE, NICHOS_TIKTOK, formatear_duracion, MINIMO_VISTAS, FECHAS, ORDENAR_POR, ordenar_videos
from modules.content_gen import obtener_info_video, generar_contenido_completo, analizar_video_local, calcular_palabras_por_duracion
from modules.voice_gen import obtener_api_key, obtener_voces_disponibles, generar_audio
from modules.downloader import obtener_info_video as obtener_info_descarga, descargar_video, detectar_plataforma, CALIDADES_YOUTUBE, CALIDADES_TIKTOK
from modules.uploader import (
    obtener_credenciales_guardadas, generar_url_auth,
    completar_auth_con_codigo, obtener_cliente_youtube_oauth,
    subir_video_youtube, CATEGORIAS_YOUTUBE, TOKEN_FILE
)


PASOS = [
    {"num": 1, "icono": "🔥", "nombre": "Buscar tendencia viral"},
    {"num": 2, "icono": "✍️", "nombre": "Generar contenido con IA"},
    {"num": 3, "icono": "⬇️", "nombre": "Descargar video de referencia"},
    {"num": 4, "icono": "🎙️", "nombre": "Generar voz en off"},
    {"num": 5, "icono": "🚀", "nombre": "Subir a YouTube"},
]


def inicializar_estado():
    """Inicializa el estado del proceso si no existe."""
    if "proceso_paso" not in st.session_state:
        st.session_state.proceso_paso = 1
    if "proceso_completados" not in st.session_state:
        st.session_state.proceso_completados = set()


def mostrar_progreso():
    """Muestra la barra de progreso con los pasos numerados."""
    paso_actual = st.session_state.proceso_paso
    completados = st.session_state.proceso_completados
    total = len(PASOS)
    progreso = len(completados) / total

    st.progress(progreso, text=f"Progreso: {len(completados)}/{total} pasos completados")
    st.markdown("")

    # Chips de pasos
    cols = st.columns(total)
    for i, paso in enumerate(PASOS):
        with cols[i]:
            num = paso["num"]
            if num in completados:
                st.markdown(f"<div style='text-align:center; color:#00c853; font-size:11px'>✅<br><b>Paso {num}</b><br>{paso['nombre']}</div>", unsafe_allow_html=True)
            elif num == paso_actual:
                st.markdown(f"<div style='text-align:center; color:#ff6d00; font-size:11px'>{paso['icono']}<br><b>Paso {num}</b><br>{paso['nombre']}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='text-align:center; color:#888; font-size:11px'>○<br>Paso {num}<br>{paso['nombre']}</div>", unsafe_allow_html=True)

    st.markdown("---")


def boton_siguiente(paso_actual: int, label: str = "Siguiente paso →"):
    """Botón para marcar el paso como completado y avanzar."""
    if st.button(label, type="primary", use_container_width=True):
        st.session_state.proceso_completados.add(paso_actual)
        if paso_actual < len(PASOS):
            st.session_state.proceso_paso = paso_actual + 1
        st.rerun()


def boton_ir_paso(num: int):
    """Botón pequeño para volver a un paso anterior."""
    if st.button(f"← Volver al paso {num}", key=f"volver_{num}"):
        st.session_state.proceso_paso = num
        st.rerun()


# ─────────────────────────────────────────────────────────────
# PASO 1 — Buscar tendencia viral
# ─────────────────────────────────────────────────────────────
def mostrar_paso1():
    st.subheader("🔥 Paso 1 — Elegir video de referencia")
    st.markdown("Buscá una tendencia viral o usá un video/link que ya tenés.")

    # ── Modo de entrada ───────────────────────────────────────
    modo = st.radio(
        "¿Cómo querés empezar?",
        ["🔍 Buscar tendencias", "🔗 Ya tengo un link", "📁 Ya tengo un video en mi compu"],
        horizontal=True,
        key="p1_modo"
    )

    # ── MODO: Ya tengo un link ────────────────────────────────
    if modo == "🔗 Ya tengo un link":
        st.markdown("Pegá la URL del video con el que querés trabajar.")
        url_directa = st.text_input("URL del video", placeholder="https://www.youtube.com/watch?v=...", key="p1_url_directa")

        if url_directa and st.button("✅ Usar este video", type="primary", use_container_width=True, key="p1_usar_url"):
            from modules.content_gen import obtener_info_video
            with st.spinner("Obteniendo información del video..."):
                info = obtener_info_video(url_directa.strip())
            if info:
                st.session_state["ref_url"] = url_directa.strip()
                st.session_state["ref_titulo"] = info["titulo"]
                st.session_state["ref_canal"] = info["canal"]
                st.session_state["p1_videos"] = [{
                    "titulo": info["titulo"],
                    "canal": info["canal"],
                    "vistas": info["vistas"],
                    "duracion": 0,
                    "fecha": "—",
                    "fecha_raw": "",
                    "url": url_directa.strip(),
                    "plataforma": "YouTube",
                }]
                st.success(f"✅ **{info['titulo']}**")
                st.caption(f"Canal: {info['canal']} · {info['vistas']:,} vistas")
            else:
                # Si la API falla, guardamos la URL igual
                st.session_state["ref_url"] = url_directa.strip()
                st.session_state["ref_titulo"] = url_directa.strip()
                st.session_state["ref_canal"] = ""

        if st.session_state.get("ref_url"):
            st.markdown("---")
            boton_siguiente(1, "✅ Continuar con este video → Paso 2")
        return

    # ── MODO: Ya tengo un video en mi compu ──────────────────
    if modo == "📁 Ya tengo un video en mi compu":
        st.markdown("Subí el video desde tu computadora o celular.")
        archivo = st.file_uploader("Subí tu video", type=["mp4", "mov", "avi", "mkv", "webm"], key="p1_archivo_local")
        descripcion = st.text_input("Descripción breve del video (opcional)", placeholder="Ej: Mi perro haciendo algo muy gracioso", key="p1_desc_local")

        if archivo:
            st.video(archivo)
            if st.button("✅ Usar este video", type="primary", use_container_width=True, key="p1_usar_local"):
                # Guardar referencia del video local para el paso 2
                st.session_state["ref_url"] = ""
                st.session_state["ref_titulo"] = archivo.name
                st.session_state["ref_canal"] = "Video propio"
                st.session_state["p1_video_local_nombre"] = archivo.name
                st.session_state["p1_video_local_desc"] = descripcion
                # Guardamos los bytes para que el paso 2 pueda analizarlo
                st.session_state["p1_video_local_bytes"] = archivo.read()
                st.session_state["p1_video_local_ext"] = archivo.name.split(".")[-1]
                st.success(f"✅ Video cargado: **{archivo.name}**")

        if st.session_state.get("p1_video_local_bytes"):
            st.markdown("---")
            boton_siguiente(1, "✅ Continuar con este video → Paso 2")
        return

    # ── MODO: Buscar tendencias (original) ───────────────────
    plataforma = st.radio("Plataforma", ["YouTube", "TikTok"], horizontal=True, key="p1_plataforma")
    nichos = NICHOS_YOUTUBE if plataforma == "YouTube" else NICHOS_TIKTOK

    col1, col2 = st.columns(2)
    with col1:
        nicho = st.selectbox("Nicho", options=list(nichos.keys()), key="p1_nicho")
    with col2:
        termino_custom = st.text_input("O ingresá tu propio término", placeholder="Ej: gatos graciosos 2024", key="p1_termino")

    # ── Filtros avanzados ─────────────────────────────────────
    with st.expander("⚙️ Filtros avanzados"):
        col_f1, col_f2, col_f3 = st.columns(3)

        with col_f1:
            st.markdown("**⏱ Duración**")
            dur_min = st.number_input("Mínimo (min)", min_value=0, max_value=60, value=0, key="p1_dur_min")
            dur_max = st.number_input("Máximo (min)", min_value=0, max_value=120, value=0, help="0 = sin límite", key="p1_dur_max")

        with col_f2:
            st.markdown("**👁 Vistas y fecha**")
            min_vistas_label = st.selectbox("Mínimo de vistas", list(MINIMO_VISTAS.keys()), index=2, key="p1_vistas")
            antiguedad_label = st.selectbox("Antigüedad máxima", list(FECHAS.keys()), index=2, key="p1_fecha")

        with col_f3:
            st.markdown("**⚙️ Extra**")
            solo_cc = st.checkbox("Solo Creative Commons", key="p1_cc", help="Videos reutilizables sin copyright")
            ordenar_label = st.selectbox("Ordenar por", list(ORDENAR_POR.keys()), key="p1_orden")
            cantidad = st.slider("Resultados", 5, 25, 10, key="p1_cantidad")

    st.markdown("")
    buscar = st.button("🔍 Buscar tendencias", type="primary", use_container_width=True, key="p1_buscar")

    if buscar:
        termino = termino_custom.strip() if termino_custom.strip() else nichos[nicho]
        termino_busqueda = f"{termino} creative commons" if solo_cc and plataforma == "YouTube" else termino

        filtros = {
            "min_dur_seg": int(dur_min * 60),
            "max_dur_seg": int(dur_max * 60) if dur_max > 0 else 0,
            "min_vistas": MINIMO_VISTAS[min_vistas_label],
            "dias": FECHAS[antiguedad_label],
            "solo_cc": solo_cc,
        }

        # Mostrar filtros activos
        activos = []
        if dur_min > 0 or dur_max > 0:
            activos.append(f"Duración: {dur_min}–{dur_max}min" if dur_max else f"Mín {dur_min}min")
        if filtros["min_vistas"] > 0:
            activos.append(min_vistas_label)
        if filtros["dias"]:
            activos.append(antiguedad_label)
        if solo_cc:
            activos.append("Creative Commons")
        if activos:
            st.info(f"Filtros activos: {' · '.join(activos)}")

        with st.spinner(f"Buscando en {plataforma}..."):
            videos = buscar_en_youtube(termino_busqueda, cantidad, filtros) if plataforma == "YouTube" else buscar_en_tiktok(termino, cantidad, filtros)

        if videos:
            videos = ordenar_videos(videos, ORDENAR_POR[ordenar_label])
            st.session_state["p1_videos"] = videos
            st.session_state["p1_termino_usado"] = termino
        else:
            st.warning("No se encontraron videos con esos filtros. Probá ampliar la duración o bajar el mínimo de vistas.")

    # Mostrar resultados si existen
    if "p1_videos" in st.session_state:
        videos = st.session_state["p1_videos"]
        st.success(f"✅ {len(videos)} videos encontrados")

        for i, v in enumerate(videos, 1):
            col_n, col_i, col_v = st.columns([0.4, 4, 1.5])
            with col_n:
                st.markdown(f"**#{i}**")
            with col_i:
                st.markdown(f"**[{v['titulo']}]({v['url']})**")
                st.caption(f"@{v['canal']} · {formatear_duracion(v['duracion'])} · {v.get('fecha', '—')}")
            with col_v:
                vistas = v["vistas"]
                vistas_str = f"{vistas/1_000_000:.1f}M" if vistas >= 1_000_000 else f"{vistas/1_000:.0f}K" if vistas >= 1_000 else str(vistas) if vistas else "N/D"
                st.metric("Vistas", vistas_str)
            st.divider()

        # Seleccionar video de referencia
        st.markdown("#### Seleccioná el video de referencia")
        titulos = [f"#{i+1} — {v['titulo'][:60]}" for i, v in enumerate(videos)]
        seleccion = st.selectbox("Video de referencia", titulos, key="p1_seleccion")
        idx = titulos.index(seleccion)
        video_ref = videos[idx]

        st.info(f"📌 **{video_ref['titulo']}**\n\n🔗 {video_ref['url']}")

        st.session_state["ref_url"] = video_ref["url"]
        st.session_state["ref_titulo"] = video_ref["titulo"]
        st.session_state["ref_canal"] = video_ref["canal"]

        st.markdown("---")
        boton_siguiente(1, "✅ Usar este video → Ir al Paso 2")


# ─────────────────────────────────────────────────────────────
# PASO 2 — Generar contenido con IA
# ─────────────────────────────────────────────────────────────
def mostrar_paso2():
    st.subheader("✍️ Paso 2 — Generar contenido con IA")
    st.markdown("Claude genera el guión, título, descripción, tags y thumbnail para tu video.")

    # Mostrar video de referencia si viene del paso 1
    ref_url = st.session_state.get("ref_url", "")
    ref_titulo = st.session_state.get("ref_titulo", "")

    # Detectar si viene un video local del Paso 1
    video_local_bytes = st.session_state.get("p1_video_local_bytes")
    video_local_desc = st.session_state.get("p1_video_local_desc", "")

    if video_local_bytes:
        # Viene del paso 1 con video local — analizarlo automáticamente
        st.info(f"📁 Video local cargado desde el Paso 1: **{st.session_state.get('p1_video_local_nombre', '')}**")
        if "p2_tema_generado" not in st.session_state:
            if st.button("🎬 Analizar video con Claude", type="primary", use_container_width=True, key="p2_analizar_auto"):
                ext = st.session_state.get("p1_video_local_ext", "mp4")
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
                    tmp.write(video_local_bytes)
                    ruta_tmp = tmp.name
                resultado = analizar_video_local(ruta_tmp, video_local_desc)
                try:
                    Path(ruta_tmp).unlink()
                except Exception:
                    pass
                if resultado:
                    analisis = resultado["analisis"]
                    frames = resultado["frames_extraidos"]
                    st.success(f"✅ Video analizado ({frames} frames)")
                    tema_local = f"Video propio del usuario. Análisis de Claude: {analisis}"
                    if video_local_desc:
                        tema_local += f" Descripción del usuario: {video_local_desc}"
                    st.session_state["p2_tema_generado"] = tema_local
        else:
            st.success("✅ Video ya analizado")
            with st.expander("Ver análisis"):
                st.markdown(st.session_state.get("p2_tema_generado", ""))
        tema = st.session_state.get("p2_tema_generado", "")

    else:
        # Flujo normal — elegir modo de input
        modo = st.radio(
            "¿Cómo definís el tema?",
            ["URL de referencia (desde Paso 1)", "Escribir tema manualmente", "Subir video desde mi compu"],
            horizontal=True, key="p2_modo"
        )
        tema = ""

        if modo == "URL de referencia (desde Paso 1)":
            if ref_titulo:
                st.success(f"📌 Video de referencia: **{ref_titulo}**")
            url_input = st.text_input("URL del video", value=ref_url, key="p2_url")
            if url_input and st.button("🔗 Analizar URL", key="p2_analizar_url"):
                with st.spinner("Analizando video..."):
                    info = obtener_info_video(url_input.strip())
                if info:
                    tema = f"Video inspirado en: '{info['titulo']}' (canal: {info['canal']}, {info['vistas']:,} vistas)"
                    st.session_state["p2_tema_generado"] = tema
                    st.success(f"✅ {info['titulo']}")
            tema = st.session_state.get("p2_tema_generado", "")

        elif modo == "Escribir tema manualmente":
            tema = st.text_area("Describí el tema", placeholder="Ej: Compilación de los 10 gatos más graciosos de 2024", height=70, key="p2_tema_manual")

        else:
            st.info("Claude analiza frames de tu video y genera el guión basándose en lo que ve.")
            archivo_local = st.file_uploader("Subí tu video (MP4, MOV, AVI)", type=["mp4", "mov", "avi", "mkv", "webm"], key="p2_video_local")
            desc_extra = st.text_input("Descripción opcional", placeholder="Ej: Mi perro jugando en el parque", key="p2_desc_extra")

            if archivo_local and st.button("🎬 Analizar mi video", key="p2_analizar_local"):
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{archivo_local.name.split('.')[-1]}") as tmp:
                    tmp.write(archivo_local.read())
                    ruta_tmp = tmp.name
                resultado = analizar_video_local(ruta_tmp, desc_extra)
                try:
                    Path(ruta_tmp).unlink()
                except Exception:
                    pass
                if resultado:
                    analisis = resultado["analisis"]
                    frames = resultado["frames_extraidos"]
                    st.success(f"✅ Video analizado ({frames} frames)")
                    with st.expander("Ver análisis"):
                        st.markdown(analisis)
                    tema_local = f"Video propio del usuario. Análisis: {analisis}"
                    if desc_extra:
                        tema_local += f" Descripción: {desc_extra}"
                    st.session_state["p2_tema_generado"] = tema_local
            tema = st.session_state.get("p2_tema_generado", "")

    # Configuración
    col1, col2, col3 = st.columns(3)
    with col1:
        tipo = st.selectbox("Tipo", ["Compilación viral", "Top 10 / Ranking", "Fails y momentos graciosos", "Momentos épicos", "Animales y mascotas"], key="p2_tipo")
    with col2:
        idioma = st.selectbox("Idioma", ["Español latino", "Español de España", "Inglés americano"], key="p2_idioma")
    with col3:
        st.markdown("**Duración**")
        modo_dur = st.radio("", ["Aproximada", "Exacta"], horizontal=True, key="p2_modo_dur", label_visibility="collapsed")

    if modo_dur == "Aproximada":
        duracion = st.select_slider("Duración aproximada", options=["1-2 minutos", "2-3 minutos", "5-7 minutos", "8-10 minutos", "12-15 minutos"], value="5-7 minutos", key="p2_dur_aprox")
        p2_min, p2_seg = 0, 0
    else:
        col_m, col_s, col_i = st.columns([1, 1, 2])
        with col_m:
            p2_min = st.number_input("Minutos", min_value=0, max_value=60, value=1, key="p2_min")
        with col_s:
            p2_seg = st.number_input("Segundos", min_value=0, max_value=59, value=30, key="p2_seg")
        with col_i:
            if p2_min > 0 or p2_seg > 0:
                palabras = calcular_palabras_por_duracion(p2_min, p2_seg, idioma)
                st.markdown("<br>", unsafe_allow_html=True)
                st.info(f"📝 ~**{palabras} palabras** para {p2_min}:{p2_seg:02d} min")
        duracion = f"{p2_min}:{p2_seg:02d} minutos"

    st.markdown("---")
    generar = st.button("✨ Generar contenido con IA", type="primary", use_container_width=True, key="p2_generar")

    if generar:
        if not tema.strip():
            st.warning("⚠️ Primero definí el tema del video (analizá la URL, escribí el tema o subí tu video).")
        else:
            with st.spinner("Claude está generando tu contenido..."):
                contenido = generar_contenido_completo(
                    tema, tipo, duracion, idioma,
                    minutos_exactos=p2_min if modo_dur == "Exacta" else 0,
                    segundos_exactos=p2_seg if modo_dur == "Exacta" else 0
                )

            if "error" not in contenido:
                st.session_state["ultimo_titulo"] = contenido.get("titulo", "")
                st.session_state["ultima_descripcion"] = contenido.get("descripcion", "")
                st.session_state["ultimos_tags"] = ", ".join(contenido.get("tags", []))
                st.session_state["ultimo_guion"] = contenido.get("guion", "")
                st.session_state["p2_contenido"] = contenido
            else:
                st.error(contenido["error"])

    # Mostrar resultado
    if "p2_contenido" in st.session_state:
        c = st.session_state["p2_contenido"]
        st.success("✅ Contenido generado")

        st.markdown(f"**🎯 Título:** {c.get('titulo', '')}")
        with st.expander("📝 Ver guión completo"):
            st.text_area("Guión", value=c.get("guion", ""), height=250, key="p2_guion_display")
        with st.expander("📄 Ver descripción y tags"):
            st.text_area("Descripción", value=c.get("descripcion", ""), height=150, key="p2_desc_display")
            st.markdown("**Tags:** " + ", ".join([f"`{t}`" for t in c.get("tags", [])]))

        # Descarga
        paquete = f"TÍTULO:\n{c.get('titulo','')}\n\nGUIÓN:\n{c.get('guion','')}\n\nDESCRIPCIÓN:\n{c.get('descripcion','')}\n\nTAGS:\n{', '.join(c.get('tags',[]))}"
        st.download_button("📥 Descargar paquete completo", data=paquete, file_name="contenido_youtube.txt", mime="text/plain")

        st.markdown("---")
        boton_ir_paso(1)
        boton_siguiente(2, "✅ Contenido listo → Ir al Paso 3")


# ─────────────────────────────────────────────────────────────
# PASO 3 — Descargar video de referencia
# ─────────────────────────────────────────────────────────────
def mostrar_paso3():
    st.subheader("⬇️ Paso 3 — Descargar video de referencia")
    st.markdown("Descargá el video viral para usarlo como base en tu edición. Soporta YouTube y TikTok.")

    ref_url = st.session_state.get("ref_url", "")
    url = st.text_input("URL del video a descargar", value=ref_url, key="p3_url")

    # Detectar plataforma en tiempo real y mostrar calidades correspondientes
    plataforma = detectar_plataforma(url.strip()) if url.strip() else "youtube"
    if url.strip():
        if plataforma == "tiktok":
            st.success("🎵 TikTok detectado — se descargará sin marca de agua")
            calidades = CALIDADES_TIKTOK
        elif plataforma == "youtube":
            st.success("🎬 YouTube detectado")
            calidades = CALIDADES_YOUTUBE
        else:
            st.warning("⚠️ Pegá una URL de YouTube o TikTok.")
            calidades = CALIDADES_YOUTUBE
    else:
        calidades = CALIDADES_YOUTUBE

    calidad_label = st.selectbox("Calidad", list(calidades.keys()), key="p3_calidad")

    col_a, col_b = st.columns(2)
    with col_a:
        ver_info = st.button("🔍 Ver info del video", use_container_width=True, key="p3_info")
    with col_b:
        descargar_btn = st.button("⬇️ Descargar", type="primary", use_container_width=True, key="p3_descargar")

    if ver_info and url:
        with st.spinner("Obteniendo información..."):
            info = obtener_info_descarga(url)
        if info:
            if info.get("thumbnail"):
                st.image(info["thumbnail"], width=300)
            st.markdown(f"**{info['titulo']}** — @{info['canal']}")
            if info.get("vistas"):
                st.caption(f"Duración: {formatear_duracion(info['duracion'])} · {info['vistas']:,} vistas")

    if descargar_btn and url:
        carpeta_temp = tempfile.mkdtemp()
        with st.spinner("Descargando video..."):
            ruta = descargar_video(url.strip(), calidad_label, carpeta_temp)

        if ruta and Path(ruta).exists():
            with open(ruta, "rb") as f:
                datos = f.read()
            nombre = Path(ruta).name
            st.success("✅ ¡Descarga completada!")
            st.download_button("💾 Guardar video", data=datos, file_name=nombre, mime="video/mp4", use_container_width=True)
            try:
                Path(ruta).unlink()
            except Exception:
                pass
        else:
            st.error("❌ No se pudo descargar el video. Verificá la URL y que tengas cookies del sitio en Chrome.")

    st.markdown("---")
    boton_ir_paso(2)
    if st.button("✅ Video descargado → Ir al Paso 4", use_container_width=True, key="p3_siguiente"):
        st.session_state.proceso_completados.add(3)
        st.session_state.proceso_paso = 4
        st.rerun()


# ─────────────────────────────────────────────────────────────
# PASO 4 — Generar voz en off
# ─────────────────────────────────────────────────────────────
def mostrar_paso4():
    st.subheader("🎙️ Paso 4 — Generar voz en off")
    st.markdown("Convertí el guión a audio MP3 con una voz natural en español.")

    api_key = obtener_api_key()
    if not api_key:
        return

    with st.spinner("Cargando voces..."):
        voces = obtener_voces_disponibles(api_key)

    if not voces:
        st.error("No se pudieron cargar las voces. Verificá tu API key de ElevenLabs.")
        return

    mapa_voces = {v["nombre"]: v["id"] for v in voces}
    voice_id_default = os.getenv("ELEVENLABS_VOICE_ID", "")
    nombre_default = next((v["nombre"] for v in voces if v["id"] == voice_id_default), None)
    idx_default = list(mapa_voces.keys()).index(nombre_default) if nombre_default else 0

    col1, col2 = st.columns(2)
    with col1:
        voz_nombre = st.selectbox("Voz", list(mapa_voces.keys()), index=idx_default, key="p4_voz")
    with col2:
        voz_info = next((v for v in voces if v["nombre"] == voz_nombre), {})
        if voz_info.get("preview_url"):
            st.markdown("<br>", unsafe_allow_html=True)
            with st.expander("▶️ Preview"):
                st.audio(voz_info["preview_url"], format="audio/mp3")

    col3, col4, col5 = st.columns(3)
    with col3:
        stability = st.slider("Estabilidad", 0.0, 1.0, 0.5, 0.05, key="p4_stab")
    with col4:
        similarity = st.slider("Similitud", 0.0, 1.0, 0.75, 0.05, key="p4_sim")
    with col5:
        style = st.slider("Estilo", 0.0, 1.0, 0.4, 0.05, key="p4_style")

    guion = st.session_state.get("ultimo_guion", "")
    texto = st.text_area("Guión a narrar", value=guion, height=250, key="p4_texto")

    if texto:
        st.caption(f"{len(texto):,} caracteres")

    st.markdown("---")
    generar = st.button("🎙️ Generar audio MP3", type="primary", use_container_width=True, key="p4_generar")

    if generar:
        if not texto.strip():
            st.warning("⚠️ El guión está vacío.")
            return
        config = {"stability": stability, "similarity_boost": similarity, "style": style}
        with st.spinner("Generando audio..."):
            audio = generar_audio(texto, mapa_voces[voz_nombre], api_key, config)

        if audio:
            st.success("✅ Audio generado")
            st.audio(audio, format="audio/mp3")
            titulo = st.session_state.get("ultimo_titulo", "narracion")
            nombre_mp3 = titulo[:50].replace(" ", "_") + ".mp3"
            st.download_button("📥 Descargar MP3", data=audio, file_name=nombre_mp3, mime="audio/mpeg", use_container_width=True)
            st.session_state["p4_audio_listo"] = True

    st.markdown("---")
    boton_ir_paso(3)
    if st.session_state.get("p4_audio_listo"):
        boton_siguiente(4, "✅ Audio listo → Ir al Paso 5")
    else:
        st.info("Generá el audio para continuar al paso 5.")


# ─────────────────────────────────────────────────────────────
# PASO 5 — Subir a YouTube
# ─────────────────────────────────────────────────────────────
def mostrar_paso5():
    st.subheader("🚀 Paso 5 — Subir a YouTube")
    st.markdown("Subí tu video final con todos los metadatos generados automáticamente.")

    # Autenticación
    credenciales = obtener_credenciales_guardadas()
    if not credenciales:
        st.warning("🔐 Primero autenticate con Google.")
        if st.button("🔗 Generar link de autorización", key="p5_auth_btn"):
            resultado = generar_url_auth()
            if resultado:
                url_auth, flow = resultado
                st.session_state["oauth_flow"] = flow
                st.session_state["oauth_url"] = url_auth

        if "oauth_url" in st.session_state:
            st.code(st.session_state["oauth_url"])
            codigo = st.text_input("Código de autorización", key="p5_codigo")
            if st.button("✅ Confirmar código", key="p5_confirmar"):
                if codigo.strip():
                    flow = st.session_state.get("oauth_flow")
                    creds = completar_auth_con_codigo(flow, codigo)
                    if creds:
                        st.success("✅ Autenticado con Google")
                        st.rerun()
        return
    else:
        st.success("✅ Sesión de YouTube activa")

    # Metadatos
    titulo = st.text_input("Título *", value=st.session_state.get("ultimo_titulo", ""), max_chars=100, key="p5_titulo")
    descripcion = st.text_area("Descripción *", value=st.session_state.get("ultima_descripcion", ""), height=120, key="p5_desc")
    tags_input = st.text_input("Tags", value=st.session_state.get("ultimos_tags", ""), key="p5_tags")

    col1, col2 = st.columns(2)
    with col1:
        categoria = st.selectbox("Categoría", list(CATEGORIAS_YOUTUBE.keys()), key="p5_cat")
    with col2:
        privacidad = st.selectbox("Privacidad", ["public", "unlisted", "private"],
            format_func=lambda x: {"public": "🌍 Público", "unlisted": "🔗 No listado", "private": "🔒 Privado"}[x], key="p5_priv")

    programar = st.checkbox("Programar publicación", key="p5_programar")
    fecha_iso = None
    if programar:
        col_f, col_h = st.columns(2)
        with col_f:
            fecha = st.date_input("Fecha", min_value=datetime.date.today() + datetime.timedelta(days=1), key="p5_fecha")
        with col_h:
            hora = st.time_input("Hora (UTC)", value=datetime.time(18, 0), key="p5_hora")
        fecha_iso = f"{fecha.isoformat()}T{hora.strftime('%H:%M:%S')}Z"
        st.info(f"⏰ Publicación programada: {fecha.strftime('%d/%m/%Y')} a las {hora.strftime('%H:%M')} UTC")

    archivo = st.file_uploader("Video a subir (MP4, MOV — hasta 2GB)", type=["mp4", "mov", "avi", "mkv"], key="p5_archivo")

    st.markdown("---")
    boton_ir_paso(4)

    subir = st.button("🚀 Subir video a YouTube", type="primary", use_container_width=True, key="p5_subir")

    if subir:
        if not archivo:
            st.warning("⚠️ Seleccioná un archivo de video.")
            return
        if not titulo.strip():
            st.warning("⚠️ El título es obligatorio.")
            return

        youtube = obtener_cliente_youtube_oauth()
        if not youtube:
            return

        tags_lista = [t.strip() for t in tags_input.split(",") if t.strip()]

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{archivo.name.split('.')[-1]}") as tmp:
            tmp.write(archivo.read())
            ruta_tmp = tmp.name

        try:
            url_video = subir_video_youtube(
                youtube=youtube,
                ruta_video=ruta_tmp,
                titulo=titulo,
                descripcion=descripcion,
                tags=tags_lista,
                categoria_id=CATEGORIAS_YOUTUBE[categoria],
                privacidad="private" if programar else privacidad,
                fecha_publicacion=fecha_iso,
            )
            if url_video:
                st.success("🎉 ¡Video publicado en YouTube!")
                st.markdown(f"### [Ver tu video →]({url_video})")
                st.balloons()
                st.session_state.proceso_completados.add(5)
        finally:
            try:
                Path(ruta_tmp).unlink()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# Función principal del proceso guiado
# ─────────────────────────────────────────────────────────────
def mostrar_proceso():
    """Renderiza el proceso guiado completo."""
    st.title("🎯 Proceso Guiado")
    st.markdown("Seguí los pasos en orden para crear y publicar tu video viral.")

    inicializar_estado()

    # Botón para reiniciar el proceso
    col_reset, _ = st.columns([1, 4])
    with col_reset:
        if st.button("🔄 Reiniciar proceso"):
            for key in ["proceso_paso", "proceso_completados", "p1_videos", "p2_contenido",
                        "ref_url", "ref_titulo", "p4_audio_listo"]:
                st.session_state.pop(key, None)
            st.rerun()

    mostrar_progreso()

    paso = st.session_state.proceso_paso

    # Navegación rápida entre pasos completados
    completados = st.session_state.proceso_completados
    if completados:
        with st.expander("🗂 Ir a un paso específico"):
            cols = st.columns(len(PASOS))
            for i, p in enumerate(PASOS):
                with cols[i]:
                    estado = "✅" if p["num"] in completados else ("▶" if p["num"] == paso else "○")
                    if st.button(f"{estado} Paso {p['num']}", key=f"nav_{p['num']}", use_container_width=True):
                        st.session_state.proceso_paso = p["num"]
                        st.rerun()

    st.markdown("")

    # Renderizar el paso actual
    if paso == 1:
        mostrar_paso1()
    elif paso == 2:
        mostrar_paso2()
    elif paso == 3:
        mostrar_paso3()
    elif paso == 4:
        mostrar_paso4()
    elif paso == 5:
        mostrar_paso5()

    # Mensaje de proceso completo
    if len(st.session_state.proceso_completados) == len(PASOS):
        st.markdown("---")
        st.success("🎉 ¡Proceso completo! Tu video fue publicado en YouTube.")
        st.balloons()
