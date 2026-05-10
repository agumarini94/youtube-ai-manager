"""
Módulo 4 — Generador de Voz con ElevenLabs
Convierte texto a voz en español usando ElevenLabs API.
Permite seleccionar voz, previsualizar y descargar el audio MP3.
"""

import streamlit as st
import os
import requests


# URL base de la API de ElevenLabs
ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"


def obtener_api_key() -> str | None:
    """Retorna la API key de ElevenLabs o None si no está configurada."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key or len(api_key) < 10:
        st.error("❌ Falta configurar ELEVENLABS_API_KEY en el archivo .env")
        return None
    return api_key


def obtener_voces_disponibles(api_key: str) -> list:
    """
    Consulta la API de ElevenLabs para obtener todas las voces disponibles
    en la cuenta del usuario (incluyendo las voces clonadas).
    """
    headers = {"xi-api-key": api_key}
    try:
        respuesta = requests.get(f"{ELEVENLABS_API_BASE}/voices", headers=headers, timeout=10)
        respuesta.raise_for_status()
        data = respuesta.json()

        voces = []
        for voz in data.get("voices", []):
            voces.append({
                "id": voz["voice_id"],
                "nombre": voz["name"],
                "categoria": voz.get("category", "premade"),
                "descripcion": voz.get("description", ""),
                "preview_url": voz.get("preview_url", ""),
            })

        return sorted(voces, key=lambda x: x["nombre"])

    except requests.exceptions.RequestException as e:
        st.error(f"❌ Error al obtener voces de ElevenLabs: {e}")
        return []


def generar_audio(texto: str, voice_id: str, api_key: str, configuracion: dict) -> bytes | None:
    """
    Llama a la API de ElevenLabs para convertir texto a voz.
    Retorna los bytes del audio MP3 generado.

    configuracion: dict con stability, similarity_boost y style
    """
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "text": texto,
        "model_id": "eleven_multilingual_v2",  # Modelo con soporte para español
        "voice_settings": {
            "stability": configuracion.get("stability", 0.5),
            "similarity_boost": configuracion.get("similarity_boost", 0.75),
            "style": configuracion.get("style", 0.5),
            "use_speaker_boost": True,
        }
    }

    try:
        respuesta = requests.post(
            f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}",
            json=payload,
            headers=headers,
            timeout=60,  # Textos largos pueden tardar
        )
        respuesta.raise_for_status()
        return respuesta.content

    except requests.exceptions.HTTPError as e:
        if respuesta.status_code == 401:
            st.error("❌ API Key de ElevenLabs inválida o expirada.")
        elif respuesta.status_code == 422:
            st.error("❌ El texto es demasiado largo o tiene un formato inválido.")
        elif respuesta.status_code == 429:
            st.error("❌ Límite de uso de ElevenLabs alcanzado. Intenta más tarde.")
        else:
            st.error(f"❌ Error en ElevenLabs API ({respuesta.status_code}): {e}")
        return None
    except Exception as e:
        st.error(f"❌ Error al generar audio: {e}")
        return None


def obtener_uso_cuenta(api_key: str) -> dict | None:
    """Obtiene el uso de caracteres de la cuenta de ElevenLabs."""
    headers = {"xi-api-key": api_key}
    try:
        respuesta = requests.get(f"{ELEVENLABS_API_BASE}/user/subscription", headers=headers, timeout=10)
        respuesta.raise_for_status()
        data = respuesta.json()
        return {
            "caracteres_usados": data.get("character_count", 0),
            "limite_caracteres": data.get("character_limit", 0),
            "plan": data.get("tier", "free"),
        }
    except Exception:
        return None


def mostrar_generador_voz():
    """Renderiza la interfaz del Generador de Voz en Streamlit."""
    st.title("🎙️ Generador de Voz — ElevenLabs")
    st.markdown("Convierte tu guión a voz en español natural. Descarga el audio MP3 para tu video.")

    with st.expander("❓ ¿Cómo usar este módulo?"):
        st.markdown("""
**¿Qué hace?**
Convierte el guión generado por Claude en una narración de voz humana en español usando ElevenLabs. El resultado es un archivo MP3 listo para poner en tu video.

**Pasos:**
1. **Seleccioná una voz** — filtrá por categoría y escuchá el preview antes de elegir
   - Para español latino buscá voces en la categoría `premade`
   - Hacé clic en "▶️ Escuchar preview" para probar cada voz
2. **Ajustá los parámetros** (valores recomendados para narración):
   - Estabilidad: `0.5`
   - Similitud: `0.75`
   - Estilo: `0.4`
3. **El guión aparece automático** si lo generaste en el módulo anterior
   - Si no, pegá el texto manualmente
4. Presioná **Generar Audio MP3**
5. Escuchá el resultado y presioná **Descargar MP3**

**Tip:** Si la voz suena muy robótica, bajá la Estabilidad a 0.3. Si suena muy exagerada, bajá el Estilo a 0.2.

**Límite del plan gratuito:** 10,000 caracteres por mes (~5-7 minutos de audio).
        """)

    api_key = obtener_api_key()
    if not api_key:
        return

    # ── Información de la cuenta ──────────────────────────────
    uso = obtener_uso_cuenta(api_key)
    if uso:
        chars_restantes = uso["limite_caracteres"] - uso["caracteres_usados"]
        col_uso1, col_uso2, col_uso3 = st.columns(3)
        col_uso1.metric("Plan", uso["plan"].capitalize())
        col_uso2.metric("Caracteres usados", f"{uso['caracteres_usados']:,}")
        col_uso3.metric("Caracteres restantes", f"{chars_restantes:,}")

    st.markdown("---")

    # ── Selección de voz ──────────────────────────────────────
    st.subheader("1. Selecciona una voz")

    with st.spinner("Cargando voces disponibles..."):
        voces = obtener_voces_disponibles(api_key)

    if not voces:
        st.warning("No se pudieron cargar las voces. Verifica tu API key.")
        return

    # Filtrar por categoría
    categorias = list(set(v["categoria"] for v in voces))
    categoria_filtro = st.selectbox(
        "Filtrar por categoría",
        options=["Todas"] + sorted(categorias),
    )

    voces_filtradas = voces if categoria_filtro == "Todas" else [v for v in voces if v["categoria"] == categoria_filtro]

    # Mapa para selección: nombre → id
    mapa_voces = {v["nombre"]: v["id"] for v in voces_filtradas}

    # Determinar voz por defecto desde .env
    voice_id_default = os.getenv("ELEVENLABS_VOICE_ID", "")
    nombre_default = next((v["nombre"] for v in voces_filtradas if v["id"] == voice_id_default), None)
    indice_default = list(mapa_voces.keys()).index(nombre_default) if nombre_default else 0

    voz_seleccionada_nombre = st.selectbox(
        "Voz",
        options=list(mapa_voces.keys()),
        index=indice_default,
    )
    voz_seleccionada_id = mapa_voces[voz_seleccionada_nombre]

    # Preview de la voz seleccionada
    voz_info = next((v for v in voces_filtradas if v["id"] == voz_seleccionada_id), {})
    if voz_info.get("preview_url"):
        with st.expander("▶️ Escuchar preview de la voz"):
            st.audio(voz_info["preview_url"], format="audio/mp3")

    # ── Configuración de la voz ───────────────────────────────
    st.markdown("---")
    st.subheader("2. Ajusta los parámetros de voz")

    col1, col2, col3 = st.columns(3)
    with col1:
        stability = st.slider(
            "Estabilidad",
            min_value=0.0, max_value=1.0, value=0.5, step=0.05,
            help="Mayor = más consistente y robótico. Menor = más expresivo."
        )
    with col2:
        similarity = st.slider(
            "Similitud",
            min_value=0.0, max_value=1.0, value=0.75, step=0.05,
            help="Qué tan fiel al modelo de voz original."
        )
    with col3:
        style = st.slider(
            "Estilo / Expresividad",
            min_value=0.0, max_value=1.0, value=0.5, step=0.05,
            help="Mayor = más dramático y expresivo (usa más cuota)."
        )

    # ── Texto a convertir ─────────────────────────────────────
    st.markdown("---")
    st.subheader("3. Texto a convertir")

    # Detectar si hay un guión generado en la sesión anterior
    guion_previo = st.session_state.get("ultimo_guion", "")
    if guion_previo:
        st.info("💡 Se detectó un guión generado en el Generador de Contenido. Puedes usarlo directamente.")
        usar_guion = st.checkbox("Usar guión del Generador de Contenido", value=True)
    else:
        usar_guion = False

    if usar_guion and guion_previo:
        texto = st.text_area(
            "Texto (puedes editar antes de generar)",
            value=guion_previo,
            height=300,
        )
    else:
        texto = st.text_area(
            "Ingresa el texto a convertir en voz",
            placeholder="Pega aquí tu guión narrado...",
            height=300,
        )

    if texto:
        st.caption(f"Caracteres: {len(texto):,}")

    # ── Generar audio ─────────────────────────────────────────
    st.markdown("---")
    generar = st.button("🎙️ Generar Audio MP3", type="primary", use_container_width=True)

    if generar:
        if not texto or not texto.strip():
            st.warning("⚠️ Escribe o pega el texto que quieres convertir a voz.")
            return

        if len(texto) > 5000:
            st.warning("⚠️ El texto es muy largo (+5000 caracteres). Considera dividirlo en partes.")

        configuracion = {
            "stability": stability,
            "similarity_boost": similarity,
            "style": style,
        }

        with st.spinner(f"Generando audio con la voz '{voz_seleccionada_nombre}'..."):
            audio_bytes = generar_audio(texto, voz_seleccionada_id, api_key, configuracion)

        if audio_bytes:
            st.success("✅ ¡Audio generado exitosamente!")
            st.markdown("---")

            # Reproducir el audio en la app
            st.subheader("▶️ Reproducir audio")
            st.audio(audio_bytes, format="audio/mp3")

            # Descargar el archivo MP3
            titulo_video = st.session_state.get("ultimo_titulo", "narracion")
            nombre_archivo = titulo_video[:50].replace(" ", "_").replace("/", "_") + ".mp3"

            st.download_button(
                label="📥 Descargar MP3",
                data=audio_bytes,
                file_name=nombre_archivo,
                mime="audio/mpeg",
                use_container_width=True
            )

            st.info("💡 Tip: Ve al módulo **Subida a YouTube** para publicar tu video con esta narración.")
