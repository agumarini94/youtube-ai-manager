"""
YouTube AI Manager — App principal
Interfaz Streamlit con navegación lateral entre módulos
"""

import streamlit as st
from dotenv import load_dotenv
import os

# Cargar variables de entorno desde .env
load_dotenv()

# Importar módulos de la app
from modules.analyzer import mostrar_analizador
from modules.trends import mostrar_tendencias
from modules.content_gen import mostrar_generador_contenido
from modules.voice_gen import mostrar_generador_voz
from modules.uploader import mostrar_subidor
from modules.downloader import mostrar_descargador
from modules.proceso import mostrar_proceso
from modules.sync_audio import mostrar_sync_audio
from modules.seo_gen import mostrar_seo_gen
from modules.video_editor import mostrar_video_editor
from modules.tiktok_feed import mostrar_tiktok_feed
from modules.compilador import mostrar_compilador
from modules.historial import mostrar_historial
from modules.calendario import mostrar_calendario
from modules.thumbnail_gen import mostrar_thumbnail_gen
from modules.clip_splitter import mostrar_clip_splitter

# ─────────────────────────────────────────────
# Configuración general de la página
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="YouTube AI Manager",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# Estilos CSS personalizados
# ─────────────────────────────────────────────
st.markdown("""
<style>
    /* Fondo del sidebar */
    [data-testid="stSidebar"] {
        background-color: #0f0f0f;
    }
    /* Texto del sidebar */
    [data-testid="stSidebar"] * {
        color: #ffffff !important;
    }
    /* Botones de navegación */
    div.stButton > button {
        width: 100%;
        border-radius: 8px;
        border: 1px solid #333;
        background-color: #1a1a1a;
        color: white;
        padding: 10px;
        margin-bottom: 4px;
        text-align: left;
        font-size: 14px;
    }
    div.stButton > button:hover {
        background-color: #ff0000;
        border-color: #ff0000;
    }
    /* Ocultar el footer de Streamlit */
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# Sidebar — Navegación principal
# ─────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/b/b8/YouTube_Logo_2017.svg", width=140)
    st.markdown("## 🤖 YouTube AI Manager")
    st.markdown("---")
    st.markdown("### Módulos")

    # Inicializar el módulo activo en el estado de sesión
    if "modulo_activo" not in st.session_state:
        st.session_state.modulo_activo = "Feed TikTok"

    # Botón destacado para el proceso guiado
    st.markdown("### 🚀 Flujo principal")
    if st.button("📱 Feed de TikTok", key="btn_feed", use_container_width=True):
        st.session_state.modulo_activo = "Feed TikTok"
    if st.button("🎞️ Compilador", key="btn_compilador", use_container_width=True):
        st.session_state.modulo_activo = "Compilador"
    if st.button("🖼️ Generador de Thumbnail", key="btn_thumb", use_container_width=True):
        st.session_state.modulo_activo = "Generador de Thumbnail"
    if st.button("🎵 Narración Sincronizada", key="btn_sync", use_container_width=True):
        st.session_state.modulo_activo = "Sincronizador de Audio"
    if st.button("🎬 Editor de Video", key="btn_editor", use_container_width=True):
        st.session_state.modulo_activo = "Editor de Video"
    if st.button("📋 Generador de SEO", key="btn_seo", use_container_width=True):
        st.session_state.modulo_activo = "Generador de SEO"
    if st.button("🚀 Subida a YouTube", key="btn_subida", use_container_width=True):
        st.session_state.modulo_activo = "Subida a YouTube"

    if st.button("✂️ Cortador de Streams", key="btn_clips", use_container_width=True):
        st.session_state.modulo_activo = "Cortador de Streams"

    st.markdown("### Herramientas extra")
    modulos = {
        "📅 Calendario Viral": "Calendario Viral",
        "📋 Historial": "Historial",
        "📊 Analizador de Canal": "Analizador de Canal",
        "🔥 Tendencias Virales": "Tendencias Virales",
        "✍️ Generador de Contenido": "Generador de Contenido",
        "🎙️ Generador de Voz": "Generador de Voz",
        "⬇️ Descargador de Videos": "Descargador de Videos",
        "🎯 Proceso Guiado": "Proceso Guiado",
    }

    for label, nombre in modulos.items():
        if st.button(label, key=f"btn_{nombre}"):
            st.session_state.modulo_activo = nombre

    st.markdown("---")

    # Verificar estado de las API keys
    st.markdown("### Estado de API Keys")
    keys_estado = {
        "Anthropic": os.getenv("ANTHROPIC_API_KEY"),
        "YouTube API": os.getenv("YOUTUBE_API_KEY"),
        "ElevenLabs": os.getenv("ELEVENLABS_API_KEY"),
    }
    for nombre_key, valor in keys_estado.items():
        if valor and len(valor) > 10:
            st.markdown(f"✅ {nombre_key}")
        else:
            st.markdown(f"❌ {nombre_key} — no configurada")

    st.markdown("---")

    with st.expander("📖 Flujo completo"):
        st.markdown("""
1. 🔥 **Tendencias** — encontrá el tema viral
2. ⬇️ **Descargador** — bajá el video de referencia
3. ✍️ **Generador** — creá guión + SEO con IA
4. 🎙️ **Voz** — convertí el guión a MP3
5. 🎬 Editá en CapCut con clips + MP3
6. 🚀 **Subida** — publicá en YouTube
        """)

    st.caption("v1.0.0 — Powered by Claude + YouTube API")

# ─────────────────────────────────────────────
# Contenido principal — Renderizar módulo activo
# ─────────────────────────────────────────────
modulo = st.session_state.modulo_activo

if modulo == "Feed TikTok":
    mostrar_tiktok_feed()

elif modulo == "Compilador":
    mostrar_compilador()

elif modulo == "Generador de Thumbnail":
    mostrar_thumbnail_gen()

elif modulo == "Proceso Guiado":
    mostrar_proceso()

elif modulo == "Analizador de Canal":
    mostrar_analizador()

elif modulo == "Tendencias Virales":
    mostrar_tendencias()

elif modulo == "Generador de Contenido":
    mostrar_generador_contenido()

elif modulo == "Generador de Voz":
    mostrar_generador_voz()

elif modulo == "Descargador de Videos":
    mostrar_descargador()

elif modulo == "Sincronizador de Audio":
    mostrar_sync_audio()

elif modulo == "Generador de SEO":
    mostrar_seo_gen()

elif modulo == "Editor de Video":
    mostrar_video_editor()

elif modulo == "Subida a YouTube":
    mostrar_subidor()

elif modulo == "Historial":
    mostrar_historial()

elif modulo == "Calendario Viral":
    mostrar_calendario()

elif modulo == "Cortador de Streams":
    mostrar_clip_splitter()
