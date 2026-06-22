"""
Módulo 3 — Generador de Contenido con IA
Usa Claude para generar automáticamente:
- Guión narrado para el video
- Título optimizado para YouTube SEO
- Descripción con keywords
- 5 tags relevantes
- Ideas para el thumbnail
"""

import streamlit as st
import os
import anthropic
import yt_dlp
import json
import subprocess
import tempfile
import base64
from pathlib import Path


def extraer_video_id(url: str) -> str | None:
    """Extrae el video ID de una URL de YouTube."""
    import re
    patrones = [
        r"youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
    ]
    for patron in patrones:
        match = re.search(patron, url)
        if match:
            return match.group(1)
    return None


def obtener_info_video(url: str) -> dict | None:
    """
    Obtiene título y metadata de un video de YouTube
    usando YouTube Data API v3 (evita bloqueos de bot).
    """
    from googleapiclient.discovery import build

    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        st.error("❌ Falta configurar YOUTUBE_API_KEY en el archivo .env")
        return None

    video_id = extraer_video_id(url)
    if not video_id:
        st.error("❌ URL inválida. Asegúrate de que sea un link de YouTube.")
        return None

    try:
        youtube = build("youtube", "v3", developerKey=api_key)
        respuesta = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=video_id
        ).execute()

        if not respuesta.get("items"):
            st.error("❌ No se encontró el video. Puede ser privado o eliminado.")
            return None

        item = respuesta["items"][0]
        snippet = item["snippet"]
        stats = item.get("statistics", {})

        return {
            "titulo": snippet.get("title", ""),
            "descripcion": snippet.get("description", "")[:500],
            "canal": snippet.get("channelTitle", ""),
            "vistas": int(stats.get("viewCount", 0)),
            "duracion": item.get("contentDetails", {}).get("duration", ""),
        }
    except Exception as e:
        st.error(f"❌ Error al obtener información del video: {e}")
        return None


FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"


def extraer_frames_video(ruta_video: str, cantidad: int = 8) -> list[str]:
    """
    Extrae frames del video usando ffmpeg y los devuelve como base64.
    Extrae más frames y usa rutas absolutas para mayor confiabilidad.
    """
    frames_b64 = []
    carpeta = tempfile.mkdtemp()

    try:
        # Obtener duración del video
        cmd_duracion = [
            FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", ruta_video
        ]
        resultado = subprocess.run(cmd_duracion, capture_output=True, text=True, timeout=15)
        duracion = float(resultado.stdout.strip() or "60")

        # Extraer frames distribuidos evitando los primeros y últimos segundos
        for i in range(cantidad):
            # Distribuir entre 5% y 95% del video para evitar intro/outro
            pct = 0.05 + (0.90 * (i + 0.5) / cantidad)
            tiempo = duracion * pct
            ruta_frame = os.path.join(carpeta, f"frame_{i:02d}.jpg")
            cmd_frame = [
                FFMPEG, "-ss", str(tiempo), "-i", ruta_video,
                "-frames:v", "1", "-q:v", "3", ruta_frame, "-y", "-loglevel", "error"
            ]
            subprocess.run(cmd_frame, capture_output=True, timeout=20)

            if Path(ruta_frame).exists():
                with open(ruta_frame, "rb") as f:
                    frames_b64.append(base64.standard_b64encode(f.read()).decode())

    except Exception:
        pass
    finally:
        for f in Path(carpeta).glob("*.jpg"):
            try:
                f.unlink()
            except Exception:
                pass

    return frames_b64


def analizar_video_local(ruta_video: str, descripcion_usuario: str = "") -> dict | None:
    """
    Analiza un video local usando Claude Vision sobre frames extraídos.
    Devuelve un dict con el análisis del contenido para usar como base del guión.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        st.error("❌ Falta configurar ANTHROPIC_API_KEY en el archivo .env")
        return None

    # Extraer frames
    with st.spinner("Extrayendo frames del video..."):
        frames = extraer_frames_video(ruta_video, cantidad=4)

    if not frames:
        st.warning("⚠️ No se pudieron extraer frames. Usando solo la descripción del usuario.")

    # Construir el mensaje para Claude con imágenes
    contenido_mensaje = []

    for i, frame_b64 in enumerate(frames):
        contenido_mensaje.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}
        })

    descripcion_extra = f"\nDescripción adicional del usuario: {descripcion_usuario}" if descripcion_usuario else ""

    contenido_mensaje.append({
        "type": "text",
        "text": f"""Analizá estos frames de un video que el usuario quiere usar como base para crear contenido de YouTube.{descripcion_extra}

Describí en español:
1. ¿Qué tipo de contenido es? (goles, highlights, debate, datos curiosos, fútbol callejero, etc.)
2. ¿Qué está pasando en el video?
3. ¿Qué hace a este video interesante o viral?
4. ¿Qué estilo de narración le quedaría bien?

Sé breve y directo. Esta descripción se usará para generar el guión."""
    })

    try:
        cliente = anthropic.Anthropic(api_key=api_key)
        respuesta = cliente.messages.create(
            model="claude-opus-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": contenido_mensaje}]
        )
        analisis = respuesta.content[0].text
        return {"analisis": analisis, "frames_extraidos": len(frames)}
    except Exception as e:
        st.error(f"❌ Error al analizar el video: {e}")
        return None


def calcular_palabras_por_duracion(minutos: int, segundos: int, idioma: str) -> int:
    """
    Calcula la cantidad de palabras objetivo según la duración y el idioma.
    Velocidades promedio de narración:
    - Español: ~135 palabras/minuto
    - Inglés: ~150 palabras/minuto
    """
    wpm = 150 if "inglés" in idioma.lower() else 135
    duracion_total_min = minutos + segundos / 60
    return max(50, int(duracion_total_min * wpm))


def generar_contenido_completo(tema: str, tipo_contenido: str, duracion_guion: str, idioma_voz: str,
                                minutos_exactos: int = 0, segundos_exactos: int = 0,
                                frames_b64: list[str] | None = None) -> dict:
    """
    Llama a Claude para generar todos los elementos de contenido:
    guión, título, descripción, tags e ideas de thumbnail.

    Si se pasan minutos_exactos/segundos_exactos, el guión se ajusta
    a esa duración exacta en palabras. Si no, usa duracion_guion como referencia.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "❌ Falta configurar ANTHROPIC_API_KEY en el archivo .env"}

    # Determinar instrucción de longitud del guión
    if minutos_exactos > 0 or segundos_exactos > 0:
        palabras_objetivo = calcular_palabras_por_duracion(minutos_exactos, segundos_exactos, idioma_voz)
        duracion_fmt = f"{minutos_exactos}:{segundos_exactos:02d}"
        instruccion_duracion = f"DURACIÓN EXACTA DEL VIDEO: {duracion_fmt} minutos → el guión debe tener EXACTAMENTE {palabras_objetivo} palabras (±10 palabras). Ni más, ni menos."
    else:
        instruccion_duracion = f"DURACIÓN OBJETIVO DEL GUIÓN: {duracion_guion}"

    prompt = f"""Sos un creador de contenido joven argentino que hace videos virales para YouTube. Hablás de forma natural, como en la calle, con energía y humor. Tu estilo es entretenido, cercano, nunca aburrido.

TEMA DEL VIDEO: {tema}
TIPO DE CONTENIDO: {tipo_contenido}
{instruccion_duracion}
IDIOMA/VARIANTE: {idioma_voz}

Genera el paquete completo en formato JSON con exactamente esta estructura:

{{
  "titulo": "Título que genere curiosidad máxima (máx 70 caracteres, con emoji, que la gente no pueda no clickear)",
  "guion": "Narración en voz en off con estas características OBLIGATORIAS: (1) Sonás como una persona real hablando, no un robot ni un locutor de tele. (2) Usás expresiones coloquiales argentinas: 'che', 'boludo', 'una locura', 'no puede ser', 'mirá esto', 'te juro que', 'lo más groso', 'se mandó', 'la rompió', 'qué flashero', 'no te lo podés perder', 'te vas a volar la cabeza', etc. (3) Frases cortas, con ritmo, como cuando le contás algo a un amigo. (4) Mucha energía en los momentos clave. (5) PROHIBIDO: palabras técnicas, frases largas y formales, tono de noticiero, indicaciones de edición, nombres de clips, tiempos, transiciones o cualquier texto entre corchetes. Solo el texto hablado, fluido, de corrido.",
  "descripcion": "Descripción de YouTube de 150-200 palabras con keywords naturalmente integradas. Incluye párrafo inicial, lista de lo que verán, hashtags al final.",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
  "thumbnail_ideas": [
    "Idea 1: imagen, texto del thumbnail, colores y estilo visual",
    "Idea 2: alternativa",
    "Idea 3: alternativa"
  ],
  "hora_publicacion": "Mejor día y hora para publicar este tipo de contenido y por qué"
}}

IMPORTANTE: Responde ÚNICAMENTE con el JSON válido, sin texto adicional antes ni después."""

    try:
        cliente = anthropic.Anthropic(api_key=api_key)

        # Si hay frames del video, incluirlos en el mensaje para que Claude vea el contenido real
        if frames_b64:
            contenido_mensaje = []
            contenido_mensaje.append({
                "type": "text",
                "text": f"Estas son {len(frames_b64)} capturas del video, distribuidas a lo largo de su duración. Usá estas imágenes como base principal para generar el guión — describí lo que VES, no inventes situaciones que no aparezcan en los frames."
            })
            for i, b64 in enumerate(frames_b64):
                contenido_mensaje.append({
                    "type": "text",
                    "text": f"Frame {i+1}/{len(frames_b64)} (~{int(100*(i+0.5)/len(frames_b64))}% del video):"
                })
                contenido_mensaje.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
                })
            contenido_mensaje.append({"type": "text", "text": prompt})
        else:
            contenido_mensaje = prompt

        mensaje = cliente.messages.create(
            model="claude-opus-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": contenido_mensaje}]
        )

        texto_respuesta = mensaje.content[0].text.strip()

        # Limpiar posibles bloques de código markdown
        if texto_respuesta.startswith("```"):
            texto_respuesta = texto_respuesta.split("```")[1]
            if texto_respuesta.startswith("json"):
                texto_respuesta = texto_respuesta[4:]
        if texto_respuesta.endswith("```"):
            texto_respuesta = texto_respuesta[:-3]

        contenido = json.loads(texto_respuesta.strip())
        return contenido

    except json.JSONDecodeError:
        # Si el JSON falla, devolver el texto raw para que el usuario lo vea
        return {"error": "❌ Error al parsear respuesta de Claude. Intenta de nuevo.", "raw": texto_respuesta}
    except Exception as e:
        return {"error": f"❌ Error al consultar Claude API: {e}"}


def mostrar_generador_contenido():
    """Renderiza la interfaz del Generador de Contenido en Streamlit."""
    st.title("✍️ Generador de Contenido con IA")
    st.markdown("Genera automáticamente guión, título, descripción, tags y thumbnail para tu video.")

    with st.expander("❓ ¿Cómo usar este módulo?"):
        st.markdown("""
**¿Qué hace?**
Claude genera en segundos todo el contenido listo para tu video: guión narrado, título SEO, descripción, tags e ideas de thumbnail.

**Dos formas de usarlo:**

**Opción A — URL de referencia (recomendado):**
1. Copiá la URL de un video viral que encontraste en Tendencias
2. Seleccioná "Analizar video de referencia (URL)"
3. Pegá la URL y hacé clic en "Analizar video de referencia"
4. Claude se inspira en ese video para crear tu contenido

**Opción B — Tema manual:**
1. Seleccioná "Escribir tema manualmente"
2. Describí el tema (ej: "Compilación de los 10 goles más épicos del Mundial 2026")

**Configuración recomendada:**
- Tipo: `Compilación de goles`
- Duración: `5-7 minutos`
- Idioma: `Español latino`

**Qué vas a recibir:**
- Guión de voz en off listo para grabar (solo texto hablado, sin indicaciones técnicas)
- Título optimizado para YouTube (máx 70 caracteres)
- Descripción con keywords y hashtags
- 8 tags relevantes
- 3 ideas de thumbnail con descripción visual
- Mejor día y hora para publicar

**Importante:** Descargá el paquete completo con el botón al final — lo vas a necesitar en el módulo de Subida a YouTube.
        """)

    # ── Configuración del contenido ───────────────────────────
    st.subheader("1. Define tu video")

    # Opción: tema libre, URL de referencia o video local
    modo_input = st.radio(
        "¿Cómo quieres definir el tema?",
        options=["Escribir tema manualmente", "Analizar video de referencia (URL)", "Subir video desde mi computadora"],
        horizontal=True
    )

    tema_final = ""

    if modo_input == "Escribir tema manualmente":
        tema_manual = st.text_area(
            "Describe el tema del video",
            placeholder="Ej: Compilación de los 10 goles más épicos del Mundial 2026",
            height=80,
        )
        tema_final = tema_manual.strip()

    elif modo_input == "Analizar video de referencia (URL)":
        url_referencia = st.text_input(
            "URL del video de referencia",
            placeholder="https://www.youtube.com/watch?v=...",
        )
        if url_referencia and st.button("🔗 Analizar video de referencia"):
            with st.spinner("Obteniendo información del video..."):
                info = obtener_info_video(url_referencia)
            if info:
                tema_final = f"Video inspirado en: '{info['titulo']}' (canal: {info['canal']}, {info['vistas']:,} vistas)"
                st.success(f"✅ Video encontrado: **{info['titulo']}**")
                st.session_state["tema_desde_url"] = tema_final
        tema_final = st.session_state.get("tema_desde_url", "")

    else:
        # ── Subir video local ─────────────────────────────────
        st.info("Claude va a ver el video directamente y generar el guión basado en lo que aparece en pantalla.")
        archivo_local = st.file_uploader(
            "Subí tu video",
            type=["mp4", "mov", "avi", "mkv", "webm"],
            help="Funciona con videos del celu, grabaciones propias o cualquier archivo de video"
        )
        descripcion_extra = st.text_input(
            "Contexto adicional (opcional)",
            placeholder="Ej: Es un video de goles de chilena, quiero que el guión sea para una compilación",
        )

        if archivo_local:
            # Guardar archivo temporalmente para extracción de frames
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{archivo_local.name.split('.')[-1]}") as tmp:
                tmp.write(archivo_local.getvalue())
                ruta_tmp = tmp.name

            with st.spinner("Extrayendo frames del video..."):
                frames_extraidos = extraer_frames_video(ruta_tmp, cantidad=8)

            try:
                Path(ruta_tmp).unlink()
            except Exception:
                pass

            if frames_extraidos:
                st.success(f"✅ {len(frames_extraidos)} frames extraídos — Claude verá el video directamente al generar")
                st.session_state["frames_video_local"] = frames_extraidos
            else:
                st.warning("⚠️ No se pudieron extraer frames. Verificá que ffmpeg esté instalado.")

            contexto = f"Video propio del usuario ({archivo_local.name})"
            if descripcion_extra:
                contexto += f". Contexto adicional: {descripcion_extra}"
            tema_final = contexto
            st.session_state["tema_desde_video_local"] = tema_final
        else:
            # Limpiar frames si se cambió de video
            if "frames_video_local" in st.session_state:
                del st.session_state["frames_video_local"]

        tema_final = st.session_state.get("tema_desde_video_local", "")

    # ── Opciones de generación ────────────────────────────────
    st.markdown("---")
    st.subheader("2. Configura el estilo")

    col1, col2, col3 = st.columns(3)

    with col1:
        tipo_contenido = st.selectbox(
            "Tipo de contenido",
            options=[
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
        idioma_voz = st.selectbox(
            "Idioma del guión",
            options=["Español latino", "Español de España", "Inglés americano"],
        )

    with col3:
        st.markdown("**Duración del guión**")
        modo_duracion = st.radio("", ["Aproximada", "Exacta"], horizontal=True, label_visibility="collapsed")

    if modo_duracion == "Aproximada":
        duracion_guion = st.select_slider(
            "Duración aproximada",
            options=["1-2 minutos", "2-3 minutos", "5-7 minutos", "8-10 minutos", "12-15 minutos"],
            value="5-7 minutos",
        )
        min_exactos, seg_exactos = 0, 0
    else:
        st.markdown("**Ingresá la duración exacta del video:**")
        col_m, col_s, col_info = st.columns([1, 1, 2])
        with col_m:
            min_exactos = st.number_input("Minutos", min_value=0, max_value=60, value=1)
        with col_s:
            seg_exactos = st.number_input("Segundos", min_value=0, max_value=59, value=30)
        with col_info:
            if min_exactos > 0 or seg_exactos > 0:
                palabras = calcular_palabras_por_duracion(min_exactos, seg_exactos, idioma_voz)
                st.markdown("<br>", unsafe_allow_html=True)
                st.info(f"📝 Guión de ~**{palabras} palabras**\n\n⏱ {min_exactos}:{seg_exactos:02d} min")
        duracion_guion = f"{min_exactos}:{seg_exactos:02d} minutos"

    # ── Botón de generación ───────────────────────────────────
    st.markdown("---")
    generar = st.button("✨ Generar Contenido Completo", type="primary", use_container_width=True)

    if generar:
        if not tema_final:
            st.warning("⚠️ Primero define el tema del video.")
            return

        frames = st.session_state.get("frames_video_local") if modo_input == "Subir video desde mi computadora" else None

        spinner_msg = "Claude está viendo tu video y generando el contenido..." if frames else "Claude está generando tu paquete de contenido..."
        with st.spinner(spinner_msg):
            contenido = generar_contenido_completo(
                tema_final, tipo_contenido, duracion_guion, idioma_voz,
                minutos_exactos=min_exactos, segundos_exactos=seg_exactos,
                frames_b64=frames
            )

        if "error" in contenido:
            st.error(contenido["error"])
            if "raw" in contenido:
                with st.expander("Ver respuesta raw"):
                    st.text(contenido["raw"])
            return

        # Guardar en session_state para reutilizar en otros módulos
        st.session_state["ultimo_guion"] = contenido.get("guion", "")
        st.session_state["ultimo_titulo"] = contenido.get("titulo", "")
        st.session_state["ultima_descripcion"] = contenido.get("descripcion", "")
        st.session_state["ultimos_tags"] = ", ".join(contenido.get("tags", []))

        st.success("✅ ¡Contenido generado exitosamente!")
        st.markdown("---")

        # ── Mostrar título ────────────────────────────────────
        st.subheader("🎯 Título")
        titulo = contenido.get("titulo", "")
        st.info(titulo)
        st.caption(f"Longitud: {len(titulo)} caracteres")

        # ── Mostrar guión ─────────────────────────────────────
        st.markdown("---")
        st.subheader("📝 Guión Narrado")
        guion = contenido.get("guion", "")
        st.text_area("Guión completo", value=guion, height=300, key="guion_display")

        # ── Mostrar descripción ───────────────────────────────
        st.markdown("---")
        st.subheader("📄 Descripción de YouTube")
        descripcion = contenido.get("descripcion", "")
        st.text_area("Descripción", value=descripcion, height=200, key="desc_display")

        # ── Mostrar tags ──────────────────────────────────────
        st.markdown("---")
        st.subheader("🏷️ Tags")
        tags = contenido.get("tags", [])
        cols_tags = st.columns(4)
        for i, tag in enumerate(tags):
            cols_tags[i % 4].code(tag)

        # ── Mostrar ideas de thumbnail ────────────────────────
        st.markdown("---")
        st.subheader("🖼️ Ideas para Thumbnail")
        thumbnail_ideas = contenido.get("thumbnail_ideas", [])
        for i, idea in enumerate(thumbnail_ideas, 1):
            with st.expander(f"Idea {i}"):
                st.markdown(idea)

        # ── Recomendación de horario ──────────────────────────
        hora = contenido.get("hora_publicacion", "")
        if hora:
            st.markdown("---")
            st.subheader("⏰ Mejor horario de publicación")
            st.success(hora)

        # ── Exportar todo ─────────────────────────────────────
        st.markdown("---")
        paquete_completo = f"""TÍTULO:
{titulo}

GUIÓN:
{guion}

DESCRIPCIÓN:
{descripcion}

TAGS:
{', '.join(tags)}

IDEAS PARA THUMBNAIL:
{chr(10).join([f'{i+1}. {idea}' for i, idea in enumerate(thumbnail_ideas)])}

HORARIO RECOMENDADO:
{hora}
"""
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            st.download_button(
                "📥 Descargar paquete completo",
                data=paquete_completo,
                file_name="contenido_youtube.txt",
                mime="text/plain",
                use_container_width=True
            )
        with col_d2:
            st.download_button(
                "📥 Descargar solo el guión",
                data=guion,
                file_name="guion_narrado.txt",
                mime="text/plain",
                use_container_width=True
            )

        st.info("💡 Tip: Ve al módulo **Generador de Voz** para convertir el guión en audio MP3.")
