"""
Módulo 1 — Analizador de Canal
Conecta con YouTube Data API v3 para obtener estadísticas del canal
y usa Claude para analizar el contenido y sugerir mejoras.
"""

import streamlit as st
import os
import anthropic
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import plotly.express as px
import pandas as pd


def obtener_cliente_youtube():
    """Inicializa y retorna el cliente de YouTube Data API."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        st.error("❌ Falta configurar YOUTUBE_API_KEY en el archivo .env")
        return None
    return build("youtube", "v3", developerKey=api_key)


def _resolver_por_handle(youtube, handle: str) -> str | None:
    """Llama a channels().list(forHandle=...) y devuelve el Channel ID o None."""
    try:
        resp = youtube.channels().list(
            part="id",
            forHandle=f"@{handle}",
        ).execute()
        items = resp.get("items", [])
        return items[0]["id"] if items else None
    except HttpError:
        return None


def resolver_canal_id(youtube, entrada: str) -> str | None:
    """
    Acepta @handle, URL de canal o Channel ID (UC...) y devuelve el Channel ID.
    Retorna None si no pudo resolverlo.
    """
    entrada = entrada.strip()

    # URL con /channel/UCxxxxxx
    if "youtube.com/channel/" in entrada:
        cid = entrada.split("youtube.com/channel/")[-1].split("/")[0].split("?")[0]
        if cid.startswith("UC"):
            return cid

    # URL con /@handle
    if "youtube.com/@" in entrada:
        handle = entrada.split("youtube.com/@")[-1].split("/")[0].split("?")[0]
        return _resolver_por_handle(youtube, handle)

    # @handle directo
    if entrada.startswith("@"):
        return _resolver_por_handle(youtube, entrada[1:])

    # Channel ID directo
    if entrada.startswith("UC") and len(entrada) >= 20:
        return entrada

    # Último recurso: tratar como handle sin @
    return _resolver_por_handle(youtube, entrada)


def obtener_estadisticas_canal(youtube, channel_id: str) -> dict | None:
    """Obtiene estadísticas generales del canal desde la API de YouTube."""
    try:
        respuesta = youtube.channels().list(
            part="snippet,statistics,contentDetails",
            id=channel_id
        ).execute()

        if not respuesta.get("items"):
            st.error("❌ No se encontró el canal. Verificá el handle, URL o Channel ID ingresado.")
            return None

        canal = respuesta["items"][0]
        stats = canal["statistics"]
        snippet = canal["snippet"]

        return {
            "nombre": snippet.get("title", "Sin nombre"),
            "descripcion": snippet.get("description", ""),
            "pais": snippet.get("country", "No especificado"),
            "fecha_creacion": snippet.get("publishedAt", "")[:10],
            "suscriptores": int(stats.get("subscriberCount", 0)),
            "vistas_totales": int(stats.get("viewCount", 0)),
            "total_videos": int(stats.get("videoCount", 0)),
            "playlist_uploads": canal["contentDetails"]["relatedPlaylists"]["uploads"],
        }
    except HttpError as e:
        st.error(f"❌ Error al consultar YouTube API: {e}")
        return None


def obtener_videos_populares(youtube, playlist_id: str, max_resultados: int = 20) -> list:
    """Obtiene los videos más recientes del canal y sus estadísticas."""
    try:
        # Obtener IDs de videos desde la playlist de uploads
        respuesta_playlist = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=max_resultados
        ).execute()

        ids_videos = [
            item["contentDetails"]["videoId"]
            for item in respuesta_playlist.get("items", [])
        ]

        if not ids_videos:
            return []

        # Obtener estadísticas detalladas de cada video
        respuesta_videos = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(ids_videos)
        ).execute()

        videos = []
        for video in respuesta_videos.get("items", []):
            stats = video.get("statistics", {})
            snippet = video.get("snippet", {})
            videos.append({
                "id": video["id"],
                "titulo": snippet.get("title", "Sin título"),
                "fecha": snippet.get("publishedAt", "")[:10],
                "vistas": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comentarios": int(stats.get("commentCount", 0)),
                "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
            })

        # Ordenar por vistas de mayor a menor
        videos.sort(key=lambda x: x["vistas"], reverse=True)
        return videos

    except HttpError as e:
        st.error(f"❌ Error al obtener videos: {e}")
        return []


def analizar_con_ia(estadisticas_canal: dict, videos: list) -> str:
    """Usa Claude para analizar el canal y generar sugerencias de mejora."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "❌ Falta configurar ANTHROPIC_API_KEY en el archivo .env"

    # Preparar resumen de los top 5 videos para el contexto
    top_videos = videos[:5]
    resumen_videos = "\n".join([
        f"- \"{v['titulo']}\" — {v['vistas']:,} vistas, {v['likes']:,} likes"
        for v in top_videos
    ])

    prompt = f"""Analiza el siguiente canal de YouTube y proporciona sugerencias estratégicas detalladas.

DATOS DEL CANAL:
- Nombre: {estadisticas_canal['nombre']}
- Suscriptores: {estadisticas_canal['suscriptores']:,}
- Vistas totales: {estadisticas_canal['vistas_totales']:,}
- Total de videos: {estadisticas_canal['total_videos']}
- País: {estadisticas_canal['pais']}

TOP 5 VIDEOS MÁS VISTOS:
{resumen_videos}

Proporciona un análisis completo en español con estas secciones:

1. **Resumen del canal** — Estado actual y tipo de contenido que predomina
2. **Qué está funcionando** — Patrones de los videos más exitosos (títulos, temas, formato)
3. **Oportunidades de mejora** — 3-4 sugerencias concretas y accionables
4. **Estrategia recomendada** — Plan para los próximos 30 días
5. **Ideas de videos** — 3 ideas específicas basadas en los temas que mejor funcionan

Sé específico, práctico y directo. Usa bullet points donde sea útil."""

    try:
        cliente = anthropic.Anthropic(api_key=api_key)
        mensaje = cliente.messages.create(
            model="claude-opus-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        return mensaje.content[0].text
    except Exception as e:
        return f"❌ Error al consultar Claude API: {e}"


def mostrar_analizador():
    """Renderiza la interfaz del Analizador de Canal en Streamlit."""
    st.title("📊 Analizador de Canal")
    st.markdown("Conecta con tu canal de YouTube para obtener estadísticas y análisis con IA.")

    with st.expander("❓ ¿Cómo usar este módulo?"):
        st.markdown("""
**¿Qué hace?**
Conecta con tu canal de YouTube, muestra tus estadísticas reales y usa Claude para analizar qué contenido funciona mejor.

**Pasos:**
1. Ingresa tu canal en el campo de abajo (cualquiera de estos formatos funciona):
   - **@handle** → `@MiCanal`
   - **URL del canal** → `https://www.youtube.com/@MiCanal`
   - **Channel ID** → `UCxxxxxxxxxxxxxxxxxxxxxxxxx`
2. Elegí cuántos videos analizar (recomendado: 20)
3. Presioná **Analizar Canal**

**Qué vas a ver:**
- Suscriptores, vistas totales y cantidad de videos
- Tabla con tus videos ordenados por vistas
- Gráfico de los top 10
- Análisis de Claude con sugerencias concretas para mejorar tu canal

**Tip:** Si no tenés canal todavía, podés saltarte este módulo y empezar por Tendencias Virales.
        """)

    canal_default = os.getenv("YOUTUBE_CHANNEL_ID", "")
    canal_input = st.text_input(
        "Canal de YouTube",
        value=canal_default,
        placeholder="@MiCanal  /  https://youtube.com/@MiCanal  /  UCxxxxxxx",
        help="Aceptamos @handle, URL completa del canal o Channel ID (UC...)"
    )

    col1, col2 = st.columns([1, 3])
    with col1:
        max_videos = st.number_input("Máx. videos a analizar", min_value=5, max_value=50, value=20)
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        analizar = st.button("🔍 Analizar Canal", type="primary", use_container_width=True)

    if analizar:
        if not canal_input.strip():
            st.warning("⚠️ Ingresá el handle, URL o Channel ID de tu canal primero.")
            return

        youtube = obtener_cliente_youtube()
        if not youtube:
            return

        with st.spinner("Resolviendo canal..."):
            channel_id = resolver_canal_id(youtube, canal_input)

        if not channel_id:
            st.error(
                "❌ No se pudo encontrar el canal. "
                "Verificá que el @handle o la URL sean correctos y que el canal sea público."
            )
            return

        with st.spinner("Obteniendo estadísticas del canal..."):
            stats = obtener_estadisticas_canal(youtube, channel_id)

        if not stats:
            return

        # ── Métricas principales ──────────────────────────────
        st.markdown("---")
        st.subheader(f"🎬 {stats['nombre']}")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Suscriptores", f"{stats['suscriptores']:,}")
        col2.metric("Vistas totales", f"{stats['vistas_totales']:,}")
        col3.metric("Total de videos", f"{stats['total_videos']:,}")
        col4.metric("País", stats['pais'])

        st.caption(f"Canal creado el {stats['fecha_creacion']}")

        # ── Videos más vistos ─────────────────────────────────
        st.markdown("---")
        st.subheader("🏆 Videos más vistos")

        with st.spinner("Cargando videos del canal..."):
            videos = obtener_videos_populares(youtube, stats["playlist_uploads"], max_videos)

        if not videos:
            st.info("No se encontraron videos en el canal.")
            return

        # Tabla de videos
        df = pd.DataFrame(videos)[["titulo", "fecha", "vistas", "likes", "comentarios"]]
        df.columns = ["Título", "Fecha", "Vistas", "Likes", "Comentarios"]
        df["Vistas"] = df["Vistas"].apply(lambda x: f"{x:,}")
        df["Likes"] = df["Likes"].apply(lambda x: f"{x:,}")
        df["Comentarios"] = df["Comentarios"].apply(lambda x: f"{x:,}")
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Gráfico de vistas
        df_grafico = pd.DataFrame(videos[:10])
        fig = px.bar(
            df_grafico,
            x="vistas",
            y="titulo",
            orientation="h",
            title="Top 10 videos por vistas",
            color="vistas",
            color_continuous_scale="Reds",
            labels={"vistas": "Vistas", "titulo": "Video"},
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        # ── Análisis con IA ───────────────────────────────────
        st.markdown("---")
        st.subheader("🤖 Análisis con IA (Claude)")

        with st.spinner("Claude está analizando tu canal..."):
            analisis = analizar_con_ia(stats, videos)

        st.markdown(analisis)

        # Botón para exportar el análisis
        st.download_button(
            label="📥 Descargar análisis",
            data=analisis,
            file_name=f"analisis_{stats['nombre'].replace(' ', '_')}.txt",
            mime="text/plain"
        )
