"""
Módulo — Generador de SEO para YouTube
Genera título, descripción con hashtags y tags para un video ya listo para subir.
"""

import streamlit as st
import os
import anthropic
import json
import base64
import tempfile
import subprocess
from pathlib import Path

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"


def generar_capitulos(clips: list[dict]) -> str:
    """
    Genera timestamps de capítulos para la descripción de YouTube.
    clips: lista con al menos la key 'duracion' (segundos) y opcionalmente 'titulo'.
    El primer capítulo siempre empieza en 0:00.
    """
    if not clips:
        return ""

    lineas = []
    acumulado = 0.0
    for i, clip in enumerate(clips):
        m, s = divmod(int(acumulado), 60)
        h, m = divmod(m, 60)
        timestamp = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        titulo = clip.get("titulo") or clip.get("nombre") or f"Clip {i + 1}"
        titulo = titulo[:50]
        lineas.append(f"{timestamp} - {titulo}")
        acumulado += float(clip.get("duracion", 0))

    return "\n".join(lineas)


def extraer_frames_thumbnail(ruta_video: str, cantidad: int = 6) -> list[str]:
    """Extrae frames del video para que Claude lo analice visualmente."""
    frames_b64 = []
    carpeta = tempfile.mkdtemp()
    try:
        cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", ruta_video]
        resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        duracion = float(resultado.stdout.strip() or "60")

        for i in range(cantidad):
            pct = 0.05 + (0.90 * (i + 0.5) / cantidad)
            tiempo = duracion * pct
            ruta_frame = os.path.join(carpeta, f"frame_{i:02d}.jpg")
            cmd_f = [FFMPEG, "-ss", str(tiempo), "-i", ruta_video,
                     "-frames:v", "1", "-q:v", "3", ruta_frame, "-y", "-loglevel", "error"]
            subprocess.run(cmd_f, capture_output=True, timeout=20)
            if Path(ruta_frame).exists():
                with open(ruta_frame, "rb") as f:
                    frames_b64.append(base64.standard_b64encode(f.read()).decode())
    except Exception:
        pass
    return frames_b64


def _formatear_comentario_encuesta(encuesta: dict) -> str:
    """Convierte el dict encuesta en texto listo para postear como comentario."""
    pregunta = encuesta.get("pregunta", "")
    opciones = encuesta.get("opciones", [])
    if not pregunta or not opciones:
        return ""
    lineas = [f"🗳️ {pregunta}", ""]
    lineas += opciones
    lineas += ["", "¡Comentá tu número! 👇"]
    return "\n".join(lineas)


def generar_seo(descripcion: str, tipo: str, canal_handle: str, idioma: str,
                frames_b64: list[str] | None = None,
                formato: str = "video",
                angulo: str = "",
                clips: list[dict] | None = None) -> dict:
    """
    Genera título, descripción optimizada y tags usando Claude.
    Si hay frames, Claude analiza el video visualmente.
    formato: "video" | "reel" | "ambos" — ajusta el SEO para Shorts si corresponde.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "❌ Falta ANTHROPIC_API_KEY en el .env"}

    es_short = formato in ("reel", "ambos")
    handle_txt = f"Incluí '{canal_handle}' en la descripción." if canal_handle else ""
    angulo_txt = f"\nÁNGULO EMOCIONAL: {angulo}" if angulo else ""

    clips_txt = ""
    if clips:
        titulos_clips = "\n".join(
            f"  - {c.get('titulo', '')[:80]}" for c in clips if c.get("titulo")
        )
        if titulos_clips:
            clips_txt = f"\n\nCLIPS QUE FORMAN EL VIDEO (títulos originales de TikTok):\n{titulos_clips}"

    encuesta_schema = """{
  "pregunta": "Pregunta de la encuesta (ej: '¿Cuál fue la mejor hinchada?', '¿Cuál fue el golazo del reel?')",
  "opciones": ["1️⃣ Opción exacta del clip 1", "2️⃣ Opción exacta del clip 2", "3️⃣ Opción exacta del clip 3", "4️⃣ Opción exacta del clip 4 (opcional)"]
}"""

    encuesta_instrucciones = """
ENCUESTA PARA COMENTARIO FIJADO (campo "encuesta" en el JSON):
- Analizá los clips del video (títulos + frames si los tenés) y detectá los elementos REALES y ESPECÍFICOS que aparecen
- Generá UNA pregunta temática y 2 a 4 opciones BASADAS ÚNICAMENTE en lo que existe en el video
- Ejemplos correctos: clips de hinchadas Brasil/Argentina/España → pregunta "¿Cuál fue la mejor hinchada?" + opciones exactas esas; clips de goles Messi/Benzema/Neymar → pregunta "¿Cuál fue el mejor gol?" + opciones esos jugadores; clips de canciones Argentina/España/Portugal → pregunta "¿Cuál tema te gustó más?" + opciones esas canciones
- NUNCA inventes opciones que no aparecen en los clips
- IMPORTANTE: NO pongas la encuesta en la descripción del video. La descripción debe ser solo texto de SEO sin preguntas de votación. La encuesta va EXCLUSIVAMENTE en el campo JSON "encuesta" para postearse como comentario separado
- Solo retorná null si el contenido es tan variado que no hay un hilo común (ej: 4 clips de temas completamente distintos sin relación)"""

    if es_short:
        prompt = f"""Sos un experto en crecimiento de YouTube Shorts. Tu objetivo es que este Short explote en el algoritmo.

DESCRIPCIÓN DEL VIDEO: {descripcion}
TIPO DE CONTENIDO: {tipo}
IDIOMA: {idioma}
FORMATO: YouTube Short (vertical, menos de 60 segundos){angulo_txt}{clips_txt}
{handle_txt}

CONTEXTO CRÍTICO:
- Los Shorts se descubren casi exclusivamente por el algoritmo, NO por búsqueda
- El título se muestra TRUNCADO (≤40 chars visibles en el feed de Shorts)
- El thumbnail importa para búsqueda y sugeridos
- #Shorts es OBLIGATORIO para que YouTube lo clasifique correctamente
{encuesta_instrucciones}

Generá el paquete SEO en JSON:

{{
  "titulo": "Título de máximo 45 caracteres. Fórmulas que funcionan: '😱 [REACCIÓN EMOCIONAL]', '💀 [SITUACIÓN EXTREMA]', '🤣 [SITUACIÓN GRACIOSA]'. NUNCA genérico. Debe dar FOMO o generar risa solo con leerlo.",
  "variaciones": [
    {{"estilo": "🔢 Con número", "titulo": "Variación con número específico (Los 5..., 3 veces que...), máx 45 chars"}},
    {{"estilo": "❓ Curiosity gap", "titulo": "Variación que genera intriga o sorpresa sin revelar el final, máx 45 chars"}},
    {{"estilo": "😂 Emocional", "titulo": "Variación con reacción emocional fuerte (lloré, no podía creer, etc.), máx 45 chars"}},
    {{"estilo": "🎯 Directo", "titulo": "Variación descriptiva y directa del contenido + año si aplica, máx 45 chars"}}
  ],
  "descripcion": "Línea 1 (obligatoria): frase de gancho de máx 100 chars que aparece en el feed. Línea 2 en blanco. Luego 2-3 bullets breves con los mejores momentos usando emojis. Al final: #Shorts #viral #futbol #goles [otros 6+ hashtags relevantes en {idioma}]{(chr(10) + chr(32)*2 + canal_handle) if canal_handle else ''}.",
  "tags": ["shorts", "viral", "futbol", "goles", "football", "compilation", "mundial2026", "fyp", "reels", "messi"],
  "encuesta": {encuesta_schema}
}}

REGLAS para todos los títulos (principal y variaciones):
- Máximo 45 caracteres incluyendo emoji
- Empezá con emoji llamativo
- Usá MAYÚSCULAS para palabras clave si el idioma es español
- Patrones ganadores: "😱 NADIE LO VIO VENIR", "🔥 EL GOL MÁS ÉPICO", "💀 ESE PENAL IMPERDIBLE", "🤯 MESSI HACE ESTO"
- NUNCA: "Compilación de...", "Los mejores...", títulos de más de 50 chars
- Las 4 variaciones deben ser distintas entre sí y del título principal

Respondé SOLO con el JSON válido, sin texto extra."""
    else:
        prompt = f"""Sos un experto en SEO de YouTube especializado en videos de compilación viral. Tu objetivo es maximizar el CTR y el alcance orgánico.

DESCRIPCIÓN DEL VIDEO: {descripcion}
TIPO DE CONTENIDO: {tipo}
IDIOMA: {idioma}{angulo_txt}{clips_txt}
{handle_txt}
{encuesta_instrucciones}

Generá el paquete SEO completo en JSON:

{{
  "titulo": "Título viral (máx 70 caracteres, con emoji al inicio, que genere curiosidad y ganas de clickear)",
  "variaciones": [
    {{"estilo": "🔢 Con número", "titulo": "Variación con número específico (Los 10..., 5 veces que...), máx 70 chars"}},
    {{"estilo": "❓ Curiosity gap", "titulo": "Variación que genera intriga o sorpresa sin revelar el final, máx 70 chars"}},
    {{"estilo": "😂 Emocional", "titulo": "Variación con reacción emocional fuerte que conecta con el espectador, máx 70 chars"}},
    {{"estilo": "🎯 Directo", "titulo": "Variación descriptiva y directa del contenido con año si aplica, máx 70 chars"}}
  ],
  "descripcion": "Descripción completa de 150-180 palabras. Estructura: (1) Párrafo de apertura que engancha. (2) 3 a 5 bullets con los mejores momentos, con emojis, SIN timestamps. (3) Frase de cierre invitando a suscribirse. (4) Mínimo 10 hashtags relevantes al final.",
  "tags": ["futbol", "goles", "football", "mundial2026", "shorts", "viral", "compilation", "messi", "highlights", "soccer", "deportes", "argentina"],
  "encuesta": {encuesta_schema}
}}

Reglas:
- Título con energía, curioso, que dé ganas de clickear — orientado a fútbol
- Las 4 variaciones deben ser distintas entre sí y del título principal
- Hashtags al final de la descripción con # — incluir siempre #futbol #mundial2026
- Tags del array sin # y en minúsculas
- Respondé SOLO con el JSON válido, sin texto extra."""

    try:
        cliente = anthropic.Anthropic(api_key=api_key)

        if frames_b64:
            contenido = []
            contenido.append({
                "type": "text",
                "text": f"Estas son {len(frames_b64)} capturas del video distribuidas a lo largo de su duración. Analizalas para entender el contenido y generar el SEO más preciso posible."
            })
            for i, b64 in enumerate(frames_b64):
                contenido.append({"type": "text", "text": f"Frame {i+1}/{len(frames_b64)}:"})
                contenido.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
                })
            contenido.append({"type": "text", "text": prompt})
        else:
            contenido = prompt

        respuesta = cliente.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": contenido}]
        )

        texto = respuesta.content[0].text.strip()
        if texto.startswith("```"):
            texto = texto.split("```")[1]
            if texto.startswith("json"):
                texto = texto[4:]
        if texto.endswith("```"):
            texto = texto[:-3]

        return json.loads(texto.strip())

    except json.JSONDecodeError:
        return {"error": "❌ Error al parsear la respuesta. Intentá de nuevo."}
    except Exception as e:
        return {"error": f"❌ Error: {e}"}


def mostrar_seo_gen():
    st.title("📋 Generador de SEO")
    st.markdown("Generá título, descripción con hashtags y tags para tu video, listo para subir a YouTube.")

    with st.expander("❓ ¿Cómo usar este módulo?"):
        st.markdown("""
**¿Cuándo usarlo?**
Cuando ya tenés el video editado y listo para subir, pero necesitás el título, la descripción y los tags optimizados para YouTube.

**Dos formas de usarlo:**
- **Solo descripción:** Describís de qué trata el video y Claude genera todo el SEO
- **Con video:** Subís el video y Claude lo analiza visualmente para hacer un SEO más preciso

**Qué genera:**
- Título con emoji optimizado para CTR (máx 70 caracteres)
- Descripción de ~200 palabras con timestamps, llamada a la acción y hashtags
- 12 tags relevantes listos para copiar

**Tip:** El handle de tu canal (@tucanalaqui) aparece en la descripción para que los espectadores puedan encontrarte fácilmente.
        """)

    # ── Configuración principal ───────────────────────────────
    st.subheader("1. Tu canal")

    canal_handle = st.text_input(
        "Handle de tu canal de YouTube",
        placeholder="@MiCanalDeYouTube",
        value=st.session_state.get("seo_canal_handle", "@viralesDelFutbol"),
        help="Aparecerá en la descripción para que te encuentren"
    )
    if canal_handle:
        st.session_state["seo_canal_handle"] = canal_handle

    st.subheader("2. El video")

    modo = st.radio(
        "¿Cómo describís el video?",
        ["Escribo la descripción", "Subo el video para análisis visual"],
        horizontal=True
    )

    descripcion = ""
    frames_b64 = None

    if modo == "Escribo la descripción":
        descripcion = st.text_area(
            "¿De qué trata el video?",
            placeholder="Ej: Compilación de los mejores goles de chilena de la historia del fútbol. Incluye goles de distintas ligas y momentos épicos de celebración.",
            height=120,
        )
        angulo = st.text_input(
            "Ángulo o promesa emocional del video (opcional)",
            placeholder="Ej: estos goles demuestran por qué Messi es el mejor de la historia",
            help="Si lo completás, el SEO reflejará este ángulo para diferenciarte de videos similares.",
            key="seo_angulo",
        )

    else:
        # Buscar video compilado disponible en sesión
        ruta_compilado = ""
        for key in ("comp_resultado_ruta", "video_compilado_ruta", "ve_ruta"):
            ruta = st.session_state.get(key, "")
            if ruta and Path(ruta).exists():
                ruta_compilado = ruta
                break

        descripcion_extra = st.text_input(
            "Contexto adicional (opcional)",
            placeholder="Ej: Es una compilación de goles épicos, quiero que el SEO apunte a fútbol latinoamericano"
        )
        angulo = st.text_input(
            "Ángulo o promesa emocional del video (opcional)",
            placeholder="Ej: estos goles demuestran por qué Messi es el mejor de la historia",
            help="Si lo completás, el SEO reflejará este ángulo para diferenciarte de videos similares.",
            key="seo_angulo",
        )

        if ruta_compilado:
            st.success(f"✅ Video compilado disponible: `{Path(ruta_compilado).name}`")
            st.video(ruta_compilado)
            fuente_video = st.radio(
                "Video a analizar",
                ["Usar el video compilado", "Subir un video diferente"],
                horizontal=True,
                key="seo_fuente_video"
            )
        else:
            fuente_video = "Subir un video diferente"

        if fuente_video == "Usar el video compilado":
            cache_key = f"seo_frames_{Path(ruta_compilado).name}"
            if cache_key not in st.session_state:
                with st.spinner("Extrayendo frames del video compilado..."):
                    st.session_state[cache_key] = extraer_frames_thumbnail(ruta_compilado, cantidad=6)
            frames_b64 = st.session_state[cache_key]
            if frames_b64:
                st.success(f"✅ {len(frames_b64)} frames extraídos — Claude analizará el video visualmente")
            else:
                st.warning("⚠️ No se pudieron extraer frames. Asegurate de que ffmpeg esté instalado.")
            descripcion = f"Video compilado: {Path(ruta_compilado).name}"
            if descripcion_extra:
                descripcion += f". {descripcion_extra}"
        else:
            archivo = st.file_uploader(
                "Subí tu video",
                type=["mp4", "mov", "avi", "mkv"],
                key="seo_video"
            )

            if archivo:
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{archivo.name.split('.')[-1]}") as tmp:
                    tmp.write(archivo.getvalue())
                    ruta_tmp = tmp.name

                with st.spinner("Extrayendo frames del video..."):
                    frames_b64 = extraer_frames_thumbnail(ruta_tmp, cantidad=6)

                try:
                    Path(ruta_tmp).unlink()
                except Exception:
                    pass

                if frames_b64:
                    st.success(f"✅ {len(frames_b64)} frames extraídos — Claude analizará el video visualmente")
                else:
                    st.warning("⚠️ No se pudieron extraer frames. Asegurate de que ffmpeg esté instalado.")

                descripcion = f"Video del archivo: {archivo.name}"
                if descripcion_extra:
                    descripcion += f". {descripcion_extra}"

    # ── Capítulos automáticos ─────────────────────────────────
    st.subheader("3. Capítulos (opcional)")

    clips_feed = st.session_state.get("compilador_clips", [])
    capitulos_txt = ""

    if clips_feed and any(c.get("duracion", 0) > 0 for c in clips_feed):
        usar_capitulos = st.checkbox(
            "Incluir timestamps de capítulos en la descripción",
            value=True,
            key="seo_capitulos",
            help="Los capítulos de YouTube aumentan el tiempo de visualización y el SEO orgánico.",
        )
        if usar_capitulos:
            orden = st.session_state.get("comp_orden", list(range(len(clips_feed))))
            clips_ord = [clips_feed[i] for i in orden if i < len(clips_feed)]
            capitulos_txt = generar_capitulos(clips_ord)
            with st.expander("📋 Capítulos que se agregarán"):
                st.code(capitulos_txt)
    else:
        capitulos_manual = st.text_area(
            "Pegá timestamps manuales (opcional)",
            placeholder="0:00 - Intro\n0:30 - Clip viral\n1:15 - El mejor momento",
            height=100,
            key="seo_capitulos_manual",
        )
        capitulos_txt = capitulos_manual.strip()

    # ── Tipo y idioma ─────────────────────────────────────────
    st.subheader("4. Configuración")

    col1, col2 = st.columns(2)
    with col1:
        tipo = st.selectbox(
            "Tipo de contenido",
            [
                "Compilación de goles",
                "Top jugadores / Ranking",
                "Datos curiosos del fútbol",
                "Fails y bloopers deportivos",
                "Predicciones / Debate",
                "Mundial 2026",
                "Historia de clubes y jugadores",
                "Highlights de partidos",
            ]
        )
    with col2:
        idioma = st.selectbox(
            "Idioma del SEO",
            ["Español latino", "Español de España", "Inglés americano"]
        )

    # ── Generar ───────────────────────────────────────────────
    st.markdown("---")
    generar = st.button("✨ Generar SEO completo", type="primary", use_container_width=True)

    if generar:
        if not descripcion.strip():
            st.warning("⚠️ Describí el video primero.")
            return

        spinner_msg = "Claude está viendo el video y generando el SEO..." if frames_b64 else "Claude está generando el SEO..."
        with st.spinner(spinner_msg):
            resultado = generar_seo(descripcion, tipo, canal_handle, idioma, frames_b64, angulo=angulo)

        if "error" in resultado:
            st.error(resultado["error"])
            return

        # Agregar capítulos al final de la descripción si los hay
        if capitulos_txt:
            resultado["descripcion"] = resultado.get("descripcion", "") + f"\n\n📌 CAPÍTULOS\n{capitulos_txt}"

        st.session_state["seo_resultado"] = resultado

    # ── Mostrar resultado (persiste entre reruns) ─────────────
    if "seo_resultado" in st.session_state:
        resultado = st.session_state["seo_resultado"]

        st.success("✅ SEO generado")
        st.markdown("---")

        # Título
        st.subheader("🎯 Título")
        titulo = resultado.get("titulo", "")
        if "seo_titulo_override" in st.session_state:
            st.session_state["seo_titulo_edit"] = st.session_state.pop("seo_titulo_override")
        titulo_editado = st.text_input(
            "Título (editable)",
            value=titulo,
            key="seo_titulo_edit",
            help=f"{len(titulo)} caracteres — YouTube recomienda máx 70"
        )
        chars = len(titulo_editado)
        color = "🟢" if chars <= 60 else "🟡" if chars <= 70 else "🔴"
        st.caption(f"{color} {chars}/70 caracteres")

        # Variaciones de título
        variaciones = resultado.get("variaciones", [])
        if variaciones:
            with st.expander("🔀 Variaciones de título alternativas", expanded=True):
                for i, var in enumerate(variaciones):
                    col_txt, col_btn = st.columns([5, 1])
                    with col_txt:
                        st.markdown(f"**{var.get('estilo', '')}**")
                        v_titulo = var.get("titulo", "")
                        v_chars = len(v_titulo)
                        v_color = "🟢" if v_chars <= 60 else "🟡" if v_chars <= 70 else "🔴"
                        st.caption(f"`{v_titulo}` — {v_color} {v_chars} chars")
                    with col_btn:
                        st.markdown("<br>", unsafe_allow_html=True)
                        if st.button("Usar", key=f"usar_var_{i}", use_container_width=True):
                            st.session_state["seo_titulo_override"] = v_titulo
                            st.rerun()

        # Descripción
        st.markdown("---")
        st.subheader("📄 Descripción")
        descripcion_out = resultado.get("descripcion", "")
        descripcion_editada = st.text_area(
            "Descripción completa (editable)",
            value=descripcion_out,
            height=300,
            key="seo_desc_edit"
        )

        # Tags
        st.markdown("---")
        st.subheader("🏷️ Tags")
        tags = resultado.get("tags", [])
        tags_str = ", ".join(tags)
        tags_editados = st.text_area(
            "Tags separados por coma (editables)",
            value=tags_str,
            height=80,
            key="seo_tags_edit"
        )
        cols = st.columns(4)
        for i, tag in enumerate(tags):
            cols[i % 4].code(tag)

        # Encuesta para comentario fijado
        encuesta = resultado.get("encuesta") or {}
        pregunta_encuesta = encuesta.get("pregunta", "") if isinstance(encuesta, dict) else ""
        opciones_encuesta = encuesta.get("opciones", []) if isinstance(encuesta, dict) else []
        if pregunta_encuesta and opciones_encuesta:
            st.markdown("---")
            st.subheader("🗳️ Encuesta (primer comentario fijado)")
            st.caption("Se postea automáticamente como comentario al subir el video. Fijalo desde YouTube Studio para máximo engagement.")
            preview_encuesta = _formatear_comentario_encuesta({
                "pregunta": pregunta_encuesta,
                "opciones": opciones_encuesta,
            })
            st.text_area(
                "Vista previa del comentario",
                value=preview_encuesta,
                height=180,
                key="seo_encuesta_preview",
                disabled=True,
            )

        # Exportar
        st.markdown("---")
        paquete = f"""TÍTULO:
{titulo_editado}

DESCRIPCIÓN:
{descripcion_editada}

TAGS:
{tags_editados}
"""
        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                "📥 Descargar paquete SEO",
                data=paquete,
                file_name="seo_youtube.txt",
                mime="text/plain",
                use_container_width=True
            )
        with col_b:
            if st.button("📤 Usar en Subida a YouTube", use_container_width=True, type="primary"):
                st.session_state["ultimo_titulo"] = titulo_editado
                st.session_state["ultima_descripcion"] = descripcion_editada
                st.session_state["ultimos_tags"] = tags_editados
                if pregunta_encuesta and opciones_encuesta:
                    st.session_state["encuesta_pendiente"] = {
                        "pregunta": pregunta_encuesta,
                        "opciones": opciones_encuesta,
                    }
                else:
                    st.session_state.pop("encuesta_pendiente", None)
                # Pasar la ruta del video compilado si existe
                for key in ("comp_resultado_ruta", "video_compilado_ruta", "ve_ruta"):
                    ruta = st.session_state.get(key, "")
                    if ruta and Path(ruta).exists():
                        st.session_state["video_para_subir"] = ruta
                        break
                st.session_state.modulo_activo = "Subida a YouTube"
                st.rerun()
