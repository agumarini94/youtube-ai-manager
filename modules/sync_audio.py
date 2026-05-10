"""
Módulo — Sincronizador de Audio
Genera narración sincronizada con cada clip del video:
1. Detecta cortes de escena con ffmpeg
2. Claude analiza cada clip y escribe narración proporcional a su duración
3. ElevenLabs genera audio por clip
4. ffmpeg une todo en una pista sincronizada
"""

import streamlit as st
import os
import subprocess
import tempfile
import json
import base64
import anthropic
import requests
from pathlib import Path


FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
WPM_ES = 135  # palabras por minuto en español narrado


# ─────────────────────────────────────────────────────────────
# Utilidades de video
# ─────────────────────────────────────────────────────────────

def obtener_duracion(ruta: str) -> float:
    """Retorna la duración total del video en segundos."""
    cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", ruta]
    resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    try:
        return float(resultado.stdout.strip())
    except ValueError:
        return 0.0


def detectar_escenas(ruta: str, umbral: float = 0.35) -> list[float]:
    """
    Usa ffmpeg para detectar cortes de escena.
    Retorna lista de timestamps (en segundos) donde hay un corte.
    umbral: 0.0-1.0, mayor = menos sensible a cambios de escena.
    """
    cmd = [
        FFMPEG, "-i", ruta,
        "-filter:v", f"select='gt(scene,{umbral})',showinfo",
        "-f", "null", "-", "-loglevel", "info"
    ]
    resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    salida = resultado.stderr

    timestamps = [0.0]
    for linea in salida.splitlines():
        if "pts_time:" in linea:
            try:
                parte = linea.split("pts_time:")[1].split()[0]
                t = float(parte)
                if t > 0.5:  # ignorar cortes muy al inicio
                    timestamps.append(round(t, 2))
            except (ValueError, IndexError):
                pass

    timestamps.sort()
    return timestamps


def extraer_frame(ruta: str, tiempo: float, carpeta: str, nombre: str) -> str | None:
    """Extrae un frame del video en el tiempo dado. Retorna la ruta del frame."""
    ruta_frame = os.path.join(carpeta, f"{nombre}.jpg")
    cmd = [
        FFMPEG, "-ss", str(tiempo), "-i", ruta,
        "-frames:v", "1", "-q:v", "3",
        ruta_frame, "-y", "-loglevel", "error"
    ]
    subprocess.run(cmd, capture_output=True, timeout=15)
    return ruta_frame if Path(ruta_frame).exists() else None


def extraer_frames_clip(ruta: str, inicio: float, duracion: float, carpeta: str, idx: int) -> list[str]:
    """
    Extrae 3 frames de un clip (inicio, mitad, final).
    Retorna lista de paths en base64.
    """
    puntos = [0.15, 0.50, 0.85]  # % del clip donde tomar el frame
    frames_b64 = []
    for j, pct in enumerate(puntos):
        tiempo = inicio + duracion * pct
        ruta_f = extraer_frame(ruta, tiempo, carpeta, f"clip_{idx:03d}_f{j}")
        if ruta_f:
            frames_b64.append(frame_a_base64(ruta_f))
    return frames_b64


def frame_a_base64(ruta: str) -> str:
    """Convierte un frame a base64 para enviarlo a Claude."""
    with open(ruta, "rb") as f:
        return base64.standard_b64encode(f.read()).decode()


# ─────────────────────────────────────────────────────────────
# Análisis con Claude Vision
# ─────────────────────────────────────────────────────────────

ESTILOS_NARRACION = {
    "Entretenimiento general": "Narrá de forma entretenida y energética, como contándole algo increíble a un amigo.",
    "Informativo": "Describí lo que pasa de forma clara y directa, agregando contexto útil. Explicá qué está pasando y por qué es relevante.",
    "Picante / Opinión fuerte": "No te guardés nada — opiná fuerte, cuestioná, generá debate. Frases como 'esto es un escándalo', 'no me cierra', 'es re polémico', 'la verdad que...'.",
    "Fútbol": "Narrá como relator de fútbol argentino: 'qué golazo', 'la rompió', 'se la mandó', 'qué crack', 'impresionante la jugada'. Usá todo el vocabulario del ambiente futbolero.",
    "Humor": "Buscá el lado gracioso de cada momento. Ironía, exageración, comentarios absurdos. El objetivo es hacer reír al espectador.",
    "Reacciones": "Exagerá las reacciones: '¡NO PUEDE SER!', '¡TE JURO QUE NO LO CREO!', transmití sorpresa y emoción máxima en cada momento.",
    "Educativo": "Explicá lo que se ve con datos interesantes. Enseñá algo. Usá 'sabías que...', 'lo que está pasando acá es...', 'el motivo de esto es...'.",
}


def analizar_clips_con_claude(clips: list[dict], contexto_video: str,
                               estilo_narracion: str = "Entretenimiento general",
                               usar_conectores: bool = True,
                               duracion_objetivo_seg: float = 0,
                               num_clips_hint: int = 0) -> list[str]:
    """
    Recibe lista de clips con frames_b64 (lista de hasta 3 frames) y duracion_seg.
    Retorna lista de narraciones, una por clip, ajustada en palabras.
    estilo_narracion: clave de ESTILOS_NARRACION para personalizar el tono.
    usar_conectores: si True, Claude recibe la narración anterior para generar transiciones naturales.
    duracion_objetivo_seg: si > 0, escala las palabras de cada clip para que el total dure exactamente ese tiempo.
    num_clips_hint: si > 0, se le informa a Claude cuántos clips tiene el video real (puede diferir del detectado).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    cliente = anthropic.Anthropic(api_key=api_key)

    instruccion_estilo = ESTILOS_NARRACION.get(estilo_narracion, ESTILOS_NARRACION["Entretenimiento general"])

    narraciones = []
    total = len(clips)

    # Si hay duración objetivo, calcular factor de escala para que el total de palabras encaje
    duracion_real_total = sum(c["duracion_seg"] for c in clips)
    factor_escala = (duracion_objetivo_seg / duracion_real_total) if duracion_objetivo_seg > 0 and duracion_real_total > 0 else 1.0

    barra = st.progress(0, text="Claude analizando clips...")

    for i, clip in enumerate(clips):
        duracion = clip["duracion_seg"]
        palabras_objetivo = max(8, int(duracion * WPM_ES / 60 * factor_escala))
        frames = clip.get("frames_b64", [])

        contenido = []

        # Enviar hasta 3 frames del clip (inicio, mitad, final)
        etiquetas = ["inicio del clip", "mitad del clip", "final del clip"]
        for j, b64 in enumerate(frames):
            etiqueta = etiquetas[j] if j < len(etiquetas) else f"frame {j+1}"
            contenido.append({"type": "text", "text": f"— Imagen {j+1}/{len(frames)} ({etiqueta}):"})
            contenido.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
            })

        # Instrucción de conexión con clip anterior
        if i == 0:
            instruccion_conexion = "Primera frase: enganchá al espectador de entrada con energía."
        elif usar_conectores and narraciones:
            ultimas_palabras = " ".join(narraciones[-1].split()[-8:])
            instruccion_conexion = (
                f"La narración anterior terminó con: '...{ultimas_palabras}'. "
                f"Conectá naturalmente usando una frase de transición como 'y acá', 'pero mirá esto', "
                f"'y en este video también', 'mientras tanto', 'y lo que sigue es', según lo que veas en las imágenes."
            )
        else:
            instruccion_conexion = "Conectá naturalmente con lo anterior."

        contenido.append({
            "type": "text",
            "text": f"""Sos Santi, 24 años, vivís en Buenos Aires, subís videos a YouTube desde hace 3 años. Hablás exactamente como cualquier pibe argentino cuando le cuenta algo increíble a un amigo: rápido, con emoción real, sin sonar a locutor ni a robot.

VIDEO: {contexto_video} | Clip {i+1} de {total} (detectados) {f"| {num_clips_hint} clips reales en el video" if num_clips_hint > 0 else ""} | Duración: {duracion:.1f}s

ESTILO: {estilo_narracion} — {instruccion_estilo}

EJEMPLOS de cómo QUERÉS sonar:
✅ "boludo mirá la cara que pone, no lo puede creer, esto no pasa ni en el FIFA"
✅ "pará pará pará — ¿viste lo que acaba de hacer? una locura total"
✅ "te juro que cuando lo vi la primera vez lo tuve que ver tres veces más"

EJEMPLOS de cómo NO querés sonar:
❌ "en este clip podemos observar una interesante jugada"
❌ "a continuación veremos el siguiente momento del video"
❌ "mirá esto, no puede ser, qué golazo, una locura" (lista de frases pegadas sin sentido)

{instruccion_conexion}
{"Último clip: cerrá con algo que invite a suscribirse pero sin sonar a libreto, que salga natural." if i == total - 1 else ""}

⚠️ ANTI-ALUCINACIÓN: Describí solo lo que VES. Si no reconocés quién es: "este chabón", "el tipo", "el pibe". Cero nombres propios inventados.

Escribí entre {palabras_objetivo - 8} y {palabras_objetivo + 8} palabras. Texto directo, sin comillas, sin aclaraciones."""
        })

        try:
            respuesta = cliente.messages.create(
                model="claude-opus-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": contenido}]
            )
            texto = respuesta.content[0].text.strip()
            narraciones.append(texto)
        except Exception as e:
            narraciones.append(f"Clip {i+1}.")

        barra.progress((i + 1) / total, text=f"Analizando clip {i+1} de {total}...")

    barra.empty()
    return narraciones


# ─────────────────────────────────────────────────────────────
# Generación de audio con ElevenLabs
# ─────────────────────────────────────────────────────────────

def generar_audio_clip(texto: str, voice_id: str, api_key: str) -> bytes | None:
    """Genera audio MP3 para un fragmento de texto con ElevenLabs."""
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "text": texto,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.4}
    }
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            json=payload, headers=headers, timeout=60
        )
        if r.status_code == 401:
            st.error("❌ API key de ElevenLabs inválida o expirada. Revisá ELEVENLABS_API_KEY en tu archivo .env")
            return None
        if r.status_code == 422:
            # Modelo no disponible en el plan actual → intentar con modelo básico
            payload["model_id"] = "eleven_monolingual_v1"
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                json=payload, headers=headers, timeout=60
            )
        r.raise_for_status()
        return r.content
    except Exception as e:
        st.warning(f"Error generando audio para clip: {e}")
        return None


def generar_silencio(duracion_seg: float, carpeta: str, idx: int) -> str:
    """Genera un archivo de silencio en MP3 de la duración dada."""
    ruta = os.path.join(carpeta, f"silencio_{idx}.mp3")
    cmd = [
        FFMPEG, "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duracion_seg), "-q:a", "9", "-acodec", "libmp3lame",
        ruta, "-y", "-loglevel", "error"
    ]
    subprocess.run(cmd, capture_output=True, timeout=15)
    return ruta


def combinar_audios(clips_audio: list[dict], carpeta: str) -> str | None:
    """
    Une todos los audios de clips con silencios para sincronizar exactamente con el video.
    Cada clip de audio se rellena con silencio hasta igualar la duración del clip de video.
    clips_audio: lista de {ruta_audio, inicio_seg, duracion_seg}
    """
    archivos_concat = []
    cursor = 0.0  # posición actual en la línea de tiempo

    for clip in clips_audio:
        inicio = clip["inicio_seg"]
        duracion_clip = clip["duracion_seg"]
        ruta = clip.get("ruta_audio")

        # Si hay un gap antes de este clip, rellenar con silencio
        gap = inicio - cursor
        if gap > 0.1:
            ruta_sil = generar_silencio(gap, carpeta, len(archivos_concat))
            archivos_concat.append(ruta_sil)

        if ruta and Path(ruta).exists():
            dur_audio = obtener_duracion(ruta)
            archivos_concat.append(ruta)

            # Si el audio es más corto que el clip de video, rellenar el final con silencio
            # Esto es lo que mantiene la sincronización clip a clip
            padding = duracion_clip - dur_audio
            if padding > 0.05:
                ruta_sil = generar_silencio(padding, carpeta, len(archivos_concat))
                archivos_concat.append(ruta_sil)

            cursor = inicio + duracion_clip
        else:
            # Sin audio: silencio del largo completo del clip
            ruta_sil = generar_silencio(duracion_clip, carpeta, len(archivos_concat))
            archivos_concat.append(ruta_sil)
            cursor = inicio + duracion_clip

    if not archivos_concat:
        return None

    # Crear archivo de lista para ffmpeg concat
    lista_path = os.path.join(carpeta, "lista_concat.txt")
    with open(lista_path, "w") as f:
        for ruta in archivos_concat:
            f.write(f"file '{ruta}'\n")

    salida = os.path.join(carpeta, "narracion_sincronizada.mp3")
    cmd = [
        FFMPEG, "-f", "concat", "-safe", "0",
        "-i", lista_path, "-c", "copy",
        salida, "-y", "-loglevel", "error"
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)
    return salida if Path(salida).exists() else None


# ─────────────────────────────────────────────────────────────
# Interfaz Streamlit
# ─────────────────────────────────────────────────────────────

def mostrar_sync_audio():
    st.title("🎵 Sincronizador de Audio")
    st.markdown("Genera narración sincronizada automáticamente con cada clip de tu video.")

    with st.expander("❓ ¿Cómo funciona?"):
        st.markdown("""
**El problema que resuelve:**
Cuando armás una compilación (ej: goles, fails, animales), la narración genérica no coincide con lo que se ve en pantalla. Este módulo genera narración específica para **cada clip individual**.

**Proceso automático:**
1. Subís tu video compilado
2. ffmpeg detecta los cortes de escena automáticamente
3. Claude analiza cada clip y escribe narración exacta para ese momento
4. ElevenLabs genera el audio de cada clip
5. Todo se une en un MP3 sincronizado con tu video

**Resultado:** Un MP3 donde cada frase corresponde exactamente a lo que se ve en pantalla.

**En tu editor (CapCut/Premiere):** Importás el MP3 sincronizado y lo ponés en la pista de audio — listo, no necesitás ajustar nada.
        """)

    # ── Subir video ───────────────────────────────────────────
    st.subheader("1. Subí tu video compilado")
    archivo = st.file_uploader("Video (MP4, MOV)", type=["mp4", "mov", "avi", "mkv"], key="sync_video")

    if not archivo:
        st.info("Subí el video para continuar.")
        return

    # ── Configuración ─────────────────────────────────────────
    st.subheader("2. Configuración")

    col1, col2, col3 = st.columns(3)
    with col1:
        contexto = st.text_input(
            "¿De qué trata el video?",
            placeholder="Ej: goles increíbles de fútbol, fails graciosos, animales",
        )
    with col2:
        umbral_escena = st.slider(
            "Sensibilidad de detección de cortes",
            min_value=0.1, max_value=0.8, value=0.35, step=0.05,
            help="Menor = detecta más cortes. Mayor = solo cortes muy abruptos."
        )
    with col3:
        duracion_minima = st.number_input(
            "Duración mínima de clip (segundos)",
            min_value=1, max_value=10, value=2,
            help="Clips más cortos que esto se ignoran."
        )

    num_clips_hint = st.number_input(
        "Cantidad de clips del video (opcional)",
        min_value=0, max_value=200, value=0,
        help="Si sabés cuántos clips tiene tu video, ingresalo acá. Claude lo usa como contexto para narrar mejor. No cambia la detección automática.",
    )
    if num_clips_hint > 0:
        st.caption(f"Claude sabrá que el video tiene {num_clips_hint} clips en total.")

    col4, col5 = st.columns(2)
    with col4:
        estilo_narracion = st.selectbox(
            "Estilo de narración",
            options=list(ESTILOS_NARRACION.keys()),
            help="Define el tono que la IA va a usar para narrar cada clip.",
        )
        st.caption(ESTILOS_NARRACION[estilo_narracion])
    with col5:
        usar_conectores = st.toggle(
            "Conectar narraciones entre clips",
            value=True,
            help="Claude lee el final del clip anterior y genera una frase de transición natural (ej: 'y acá pasa algo increíble...').",
        )

    st.markdown("**Duración exacta del video** (opcional — para ajustar el largo total de la narración)")
    col_dur1, col_dur2, col_dur3 = st.columns([1, 1, 3])
    with col_dur1:
        dur_min = st.number_input("Minutos", min_value=0, max_value=60, value=0, key="sync_dur_min")
    with col_dur2:
        dur_seg = st.number_input("Segundos", min_value=0, max_value=59, value=0, key="sync_dur_seg")
    with col_dur3:
        duracion_objetivo_seg = dur_min * 60 + dur_seg
        if duracion_objetivo_seg > 0:
            st.markdown("<br>", unsafe_allow_html=True)
            st.info(f"⏱ La narración se ajustará para durar exactamente **{dur_min}:{dur_seg:02d}**")

    # ── ElevenLabs voz ────────────────────────────────────────
    st.subheader("3. Voz")
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

    if not elevenlabs_key:
        st.error("❌ Falta ELEVENLABS_API_KEY en el .env")
        return

    try:
        r = requests.get("https://api.elevenlabs.io/v1/voices",
                         headers={"xi-api-key": elevenlabs_key}, timeout=8)
        if r.status_code == 401:
            st.error("❌ La API key de ElevenLabs es inválida o expiró. Revisá ELEVENLABS_API_KEY en tu .env y reiniciá la app.")
            return
        r.raise_for_status()
        voces = r.json().get("voices", [])
        mapa = {v["name"]: v["voice_id"] for v in voces}
        nombre_default = next((v["name"] for v in voces if v["voice_id"] == voice_id), None)
        idx_default = list(mapa.keys()).index(nombre_default) if nombre_default and nombre_default in mapa else 0

        col_voz, col_prev = st.columns([3, 1])
        with col_voz:
            voz_elegida = st.selectbox("Seleccioná la voz", list(mapa.keys()), index=idx_default)
            voice_id = mapa[voz_elegida]
        with col_prev:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("▶️ Escuchar preview", use_container_width=True, key="btn_preview_voz"):
                cache_key = f"preview_{voice_id}"
                if cache_key not in st.session_state:
                    with st.spinner("Generando preview..."):
                        preview = generar_audio_clip(
                            "Hola, ¿cómo estás? Soy el narrador de este video.",
                            voice_id, elevenlabs_key
                        )
                    if preview:
                        st.session_state[cache_key] = preview
                st.rerun()

        # Mostrar preview si ya está cacheado para la voz actual
        preview_cached = st.session_state.get(f"preview_{voice_id}")
        if preview_cached:
            st.audio(preview_cached, format="audio/mp3")

    except Exception as e:
        st.warning(f"No se pudieron cargar las voces: {e}. Se usará la voz por defecto.")

    # ── Botón de procesamiento ────────────────────────────────
    st.markdown("---")

    # Detectar si cambió el video (nombre distinto al procesado antes)
    nombre_actual = archivo.name if archivo else ""
    if st.session_state.get("sync_nombre_video") != nombre_actual:
        for k in ["sync_clips_raw", "sync_narraciones", "sync_ruta_video", "sync_carpeta_temp"]:
            st.session_state.pop(k, None)

    procesar = st.button("🚀 Generar narración sincronizada", type="primary", use_container_width=True)

    if procesar:
        if not contexto.strip():
            st.warning("⚠️ Contanos de qué trata el video para que Claude pueda narrar bien.")
            return

        carpeta_temp = tempfile.mkdtemp()

        # Guardar video en disco temporal (persiste entre reruns)
        ext = archivo.name.split(".")[-1]
        ruta_video = os.path.join(carpeta_temp, f"video.{ext}")
        with open(ruta_video, "wb") as f:
            f.write(archivo.getvalue())

        # Paso 1 — Detectar escenas
        with st.spinner("Detectando cortes de escena..."):
            duracion_total = obtener_duracion(ruta_video)
            timestamps = detectar_escenas(ruta_video, umbral_escena)
            timestamps.append(duracion_total)

        clips_raw = []
        for i in range(len(timestamps) - 1):
            inicio = timestamps[i]
            fin = timestamps[i + 1]
            duracion = fin - inicio
            if duracion >= duracion_minima:
                clips_raw.append({"inicio": inicio, "fin": fin, "duracion": duracion})

        if not clips_raw:
            st.error("No se detectaron clips. Probá bajar la sensibilidad de detección.")
            return

        # Paso 2 — Extraer 3 frames por clip
        with st.spinner("Extrayendo frames de cada clip..."):
            carpeta_frames = os.path.join(carpeta_temp, "frames")
            os.makedirs(carpeta_frames, exist_ok=True)
            clips_con_frames = []
            for i, clip in enumerate(clips_raw):
                frames_b64 = extraer_frames_clip(ruta_video, clip["inicio"], clip["duracion"], carpeta_frames, i)
                clips_con_frames.append({
                    "inicio_seg": clip["inicio"],
                    "duracion_seg": clip["duracion"],
                    "frames_b64": frames_b64,
                })

        # Paso 3 — Claude analiza y escribe narración por clip
        st.info("Claude está analizando cada clip y escribiendo la narración...")
        narraciones = analizar_clips_con_claude(clips_con_frames, contexto,
                                                estilo_narracion=estilo_narracion,
                                                usar_conectores=usar_conectores,
                                                duracion_objetivo_seg=duracion_objetivo_seg,
                                                num_clips_hint=num_clips_hint)

        # Guardar todo en session_state para que persista al apretar el segundo botón
        st.session_state["sync_clips_raw"] = clips_raw
        st.session_state["sync_narraciones"] = narraciones
        st.session_state["sync_ruta_video"] = ruta_video
        st.session_state["sync_carpeta_temp"] = carpeta_temp
        st.session_state["sync_nombre_video"] = nombre_actual
        st.session_state["sync_duracion_total"] = duracion_total

    # ── Mostrar narraciones (persiste entre reruns) ───────────
    if "sync_clips_raw" in st.session_state:
        clips_raw = st.session_state["sync_clips_raw"]
        narraciones = list(st.session_state["sync_narraciones"])
        duracion_total = st.session_state.get("sync_duracion_total", 0)

        st.success(f"✅ Se detectaron **{len(clips_raw)} clips** en {duracion_total:.0f} segundos de video")

        with st.expander(f"Ver {len(clips_raw)} clips detectados"):
            for i, c in enumerate(clips_raw):
                m_i, s_i = divmod(int(c["inicio"]), 60)
                m_f, s_f = divmod(int(c["fin"]), 60)
                st.text(f"Clip {i+1}: {m_i}:{s_i:02d} → {m_f}:{s_f:02d} ({c['duracion']:.1f}s)")

        st.subheader("📝 Narración generada por clip")
        st.caption("Podés editar cualquier texto antes de generar el audio.")

        narraciones_editadas = []
        for i, (clip, texto) in enumerate(zip(clips_raw, narraciones)):
            m_i, s_i = divmod(int(clip["inicio"]), 60)
            col_t, col_n = st.columns([1, 4])
            with col_t:
                st.markdown(f"**Clip {i+1}**\n\n`{m_i}:{s_i:02d}`\n\n`{clip['duracion']:.1f}s`")
                st.download_button(
                    "📄 Texto",
                    data=texto,
                    file_name=f"clip_{i+1:02d}_{m_i}m{s_i:02d}s.txt",
                    mime="text/plain",
                    key=f"dl_clip_{i}",
                    use_container_width=True,
                )
            with col_n:
                editado = st.text_area(
                    f"Narración clip {i+1}",
                    value=texto,
                    height=80,
                    key=f"narr_{i}",
                    label_visibility="collapsed"
                )
                narraciones_editadas.append(editado)
            st.divider()

        # ── Texto unificado completo ───────────────────────────
        st.markdown("---")
        st.subheader("📋 Narración completa unificada")
        texto_unificado = " ".join(narraciones_editadas)
        palabras_total = len(texto_unificado.split())
        duracion_estimada_seg = palabras_total / WPM_ES * 60
        m_est, s_est = divmod(int(duracion_estimada_seg), 60)
        st.caption(f"~{palabras_total} palabras · duración estimada: {m_est}:{s_est:02d} min")
        st.text_area(
            "Texto completo",
            value=texto_unificado,
            height=250,
            key="narracion_unificada",
            label_visibility="collapsed",
        )
        st.download_button(
            "📥 Descargar texto completo",
            data=texto_unificado,
            file_name="narracion_completa.txt",
            mime="text/plain",
            use_container_width=True,
        )

        # ── Paso 4: Generar audio ─────────────────────────────
        st.markdown("---")
        generar_audio_btn = st.button("🎙️ Generar audio sincronizado", type="primary", use_container_width=True)

        if generar_audio_btn:
            carpeta_temp = st.session_state["sync_carpeta_temp"]
            carpeta_audios = os.path.join(carpeta_temp, "audios")
            os.makedirs(carpeta_audios, exist_ok=True)

            clips_audio = []
            barra_audio = st.progress(0, text="Generando audio por clip...")

            for i, (clip, texto) in enumerate(zip(clips_raw, narraciones_editadas)):
                audio_bytes = generar_audio_clip(texto, voice_id, elevenlabs_key)
                ruta_audio = None
                if audio_bytes:
                    ruta_audio = os.path.join(carpeta_audios, f"clip_{i:03d}.mp3")
                    with open(ruta_audio, "wb") as f:
                        f.write(audio_bytes)
                clips_audio.append({
                    "inicio_seg": clip["inicio"],
                    "duracion_seg": clip["duracion"],
                    "ruta_audio": ruta_audio,
                })
                barra_audio.progress((i + 1) / len(clips_raw), text=f"Audio {i+1}/{len(clips_raw)}...")

            barra_audio.empty()

            with st.spinner("Combinando audios con sincronización..."):
                ruta_final = combinar_audios(clips_audio, carpeta_temp)

            if ruta_final and Path(ruta_final).exists():
                st.success("🎉 ¡Narración sincronizada lista!")
                st.audio(ruta_final, format="audio/mp3")

                with open(ruta_final, "rb") as f:
                    st.download_button(
                        "📥 Descargar MP3 sincronizado",
                        data=f.read(),
                        file_name="narracion_sincronizada.mp3",
                        mime="audio/mpeg",
                        use_container_width=True
                    )

                st.info("""
**Cómo usarlo en tu editor:**
1. Importá el MP3 descargado en CapCut o tu editor
2. Poné el audio en la pista de sonido desde el segundo 0
3. La narración ya está sincronizada con cada clip — no hace falta ajustar nada
                """)
            else:
                st.error("❌ Error al combinar los audios. Verificá que ffmpeg esté instalado.")
