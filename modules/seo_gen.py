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


def generar_seo(descripcion: str, tipo: str, canal_handle: str, idioma: str,
                frames_b64: list[str] | None = None) -> dict:
    """
    Genera título, descripción optimizada y tags usando Claude.
    Si hay frames, Claude analiza el video visualmente.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "❌ Falta ANTHROPIC_API_KEY en el .env"}

    handle_txt = f"Incluí '{canal_handle}' en la descripción (al final del primer párrafo o en el cierre) para que los espectadores encuentren el canal." if canal_handle else ""

    prompt = f"""Sos un experto en SEO de YouTube especializado en videos de compilación viral. Tu objetivo es maximizar el CTR (click-through rate) y el alcance orgánico.

DESCRIPCIÓN DEL VIDEO: {descripcion}
TIPO DE CONTENIDO: {tipo}
IDIOMA: {idioma}
{handle_txt}

Generá el paquete SEO completo en JSON con esta estructura exacta:

{{
  "titulo": "Título viral (máx 70 caracteres, con emoji al inicio, que genere curiosidad y ganas de clickear)",
  "descripcion": "Descripción completa de YouTube de 150-180 palabras. Estructura: (1) Párrafo de apertura que engancha (2-3 oraciones que describan lo mejor del video, pensado para un reel o Short viral). (2) 3 a 5 bullets con los momentos más divertidos/épicos del video, con emojis, SIN timestamps ni tiempos. (3) Frase de cierre invitando a suscribirse{', mencionar ' + canal_handle if canal_handle else ''}. (4) Hashtags: mínimo 10 hashtags relevantes al final separados por espacios.",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10", "tag11", "tag12"]
}}

Reglas:
- El título debe tener mucha energía, ser curioso, que dé ganas de clickear
- Los hashtags van al final de la descripción con # (no como array separado)
- Los tags del array son sin # y en minúsculas
- Respondé SOLO con el JSON válido, sin texto extra antes ni después."""

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
        value=st.session_state.get("seo_canal_handle", "@ViralLocos"),
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
                "Compilación viral",
                "Top 10 / Ranking",
                "Fails y momentos graciosos",
                "Momentos épicos de deporte",
                "Animales y mascotas",
                "Reacciones",
                "Gaming highlights",
                "Otro",
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
            resultado = generar_seo(descripcion, tipo, canal_handle, idioma, frames_b64)

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
        titulo_editado = st.text_input(
            "Título (editable)",
            value=titulo,
            key="seo_titulo_edit",
            help=f"{len(titulo)} caracteres — YouTube recomienda máx 70"
        )
        chars = len(titulo_editado)
        color = "🟢" if chars <= 60 else "🟡" if chars <= 70 else "🔴"
        st.caption(f"{color} {chars}/70 caracteres")

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
                # Pasar la ruta del video compilado si existe
                for key in ("comp_resultado_ruta", "video_compilado_ruta", "ve_ruta"):
                    ruta = st.session_state.get(key, "")
                    if ruta and Path(ruta).exists():
                        st.session_state["video_para_subir"] = ruta
                        break
                st.session_state.modulo_activo = "Subida a YouTube"
                st.rerun()
