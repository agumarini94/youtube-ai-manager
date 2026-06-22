"""
Módulo 2 — Buscador de Tendencias Virales
Busca videos en YouTube y TikTok con filtros avanzados,
y usa Claude para analizar el potencial viral.
"""

import streamlit as st
import os
import anthropic
import yt_dlp
from datetime import datetime, timedelta


# Nichos para YouTube
NICHOS_YOUTUBE = {
    "⚽ Goles épicos": "goles epicos futbol compilation 2026",
    "🏆 Mundial 2026": "world cup 2026 highlights goals",
    "🌟 Mejores jugadas": "mejores jugadas futbol 2026",
    "😂 Fails de fútbol": "football fails compilation 2026",
    "🥅 Atajadas increíbles": "best goalkeeper saves compilation",
    "🇦🇷 Selección Argentina": "argentina seleccion futbol 2026",
    "🔥 Champions League": "champions league goals highlights 2026",
    "👦 Jóvenes talentos": "young football talents skills 2026",
}

# Nichos para TikTok
NICHOS_TIKTOK = {
    "⚽ Goles virales": "goles futbol",
    "🌍 Mundial 2026": "mundial 2026",
    "🌟 Cracks y habilidades": "cracks futbol habilidades",
    "😂 Fails de fútbol": "futbolfails",
    "🥅 Atajadas": "atajadas futbol",
    "🇦🇷 Fútbol argentino": "futbol argentino",
    "🔥 Highlights": "football highlights",
    "💥 Momentos épicos": "momentos epicos futbol",
}

# Opciones de antigüedad del video
FECHAS = {
    "Cualquier fecha": None,
    "Última semana": 7,
    "Último mes": 30,
    "Últimos 3 meses": 90,
    "Último año": 365,
}

# Mínimo de vistas recomendados
MINIMO_VISTAS = {
    "Sin mínimo": 0,
    "+ 10K vistas": 10_000,
    "+ 100K vistas": 100_000,
    "+ 500K vistas": 500_000,
    "+ 1M de vistas": 1_000_000,
}

# Ordenamiento de resultados
ORDENAR_POR = {
    "Más vistas": "vistas",
    "Más recientes": "fecha",
    "Más cortos": "duracion_asc",
    "Más largos": "duracion_desc",
}


def buscar_en_youtube(termino: str, max_resultados: int, filtros: dict) -> list:
    """
    Busca videos en YouTube con extract_flat para máxima velocidad
    y aplica todos los filtros manualmente sobre los resultados.
    """
    # Traemos más resultados para compensar los que serán filtrados
    cantidad_busqueda = min(max_resultados * 4, 50)

    opciones = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "cookiesfrombrowser": ("chrome",),
        "ffmpeg_location": "/opt/homebrew/bin",
    }

    # Calcular fecha límite si aplica
    fecha_limite = None
    if filtros.get("dias"):
        fecha_limite = datetime.now() - timedelta(days=filtros["dias"])

    videos_raw = []
    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(f"ytsearch{cantidad_busqueda}:{termino}", download=False)
            if not info:
                return []
            for entry in (info.get("entries") or []):
                if not entry:
                    continue
                videos_raw.append(entry)
    except Exception as e:
        st.error(f"❌ Error al buscar en YouTube: {e}")
        return []

    # Aplicar filtros manualmente
    videos = []
    for entry in videos_raw:
        if len(videos) >= max_resultados:
            break

        duracion = int(entry.get("duration") or 0)
        vistas = int(entry.get("view_count") or 0)
        fecha_raw = entry.get("upload_date") or ""

        # Filtro de duración mínima
        if filtros["min_dur_seg"] > 0 and duracion < filtros["min_dur_seg"]:
            continue
        # Filtro de duración máxima
        if filtros["max_dur_seg"] > 0 and duracion > filtros["max_dur_seg"]:
            continue
        # Filtro de vistas mínimas
        if filtros["min_vistas"] > 0 and vistas < filtros["min_vistas"]:
            continue
        # Filtro de fecha
        if fecha_limite and fecha_raw and len(fecha_raw) == 8:
            try:
                fecha_video = datetime.strptime(fecha_raw, "%Y%m%d")
                if fecha_video < fecha_limite:
                    continue
            except ValueError:
                pass

        fecha_fmt = f"{fecha_raw[6:8]}/{fecha_raw[4:6]}/{fecha_raw[:4]}" if len(fecha_raw) == 8 else "—"

        # Licencia: extract_flat no trae esta info, se marca como "verificar"
        licencia = "Creative Commons" if filtros.get("solo_cc") else "Estándar de YouTube"

        videos.append({
            "titulo": entry.get("title", "Sin título"),
            "canal": entry.get("uploader") or entry.get("channel") or "Desconocido",
            "vistas": vistas,
            "duracion": duracion,
            "fecha": fecha_fmt,
            "fecha_raw": fecha_raw,
            "licencia": licencia,
            "url": f"https://www.youtube.com/watch?v={entry.get('id', '')}",
            "plataforma": "YouTube",
        })

    return videos


def buscar_en_tiktok(termino: str, max_resultados: int, filtros: dict) -> list:
    """Busca videos en TikTok con filtros básicos."""
    opciones = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "ffmpeg_location": "/opt/homebrew/bin",
    }
    videos = []
    cantidad = min(max_resultados * 2, 30)

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(f"ttsearch{cantidad}:{termino}", download=False)
            for entry in (info.get("entries") or []):
                if not entry:
                    continue
                duracion = int(entry.get("duration") or 0)
                vistas = int(entry.get("view_count") or 0)

                # Aplicar filtros manualmente para TikTok
                if filtros["min_dur_seg"] > 0 and duracion < filtros["min_dur_seg"]:
                    continue
                if filtros["max_dur_seg"] > 0 and duracion > filtros["max_dur_seg"]:
                    continue
                if filtros["min_vistas"] > 0 and vistas < filtros["min_vistas"]:
                    continue
                if len(videos) >= max_resultados:
                    break

                url = entry.get("url") or entry.get("webpage_url") or f"https://www.tiktok.com/@{entry.get('uploader', '')}/video/{entry.get('id', '')}"
                videos.append({
                    "titulo": entry.get("title", "Sin título"),
                    "canal": entry.get("uploader", "Desconocido"),
                    "vistas": vistas,
                    "duracion": duracion,
                    "fecha": "—",
                    "fecha_raw": "",
                    "licencia": "TikTok",
                    "url": url,
                    "plataforma": "TikTok",
                })
    except Exception as e:
        st.error(f"❌ Error al buscar en TikTok: {e}")
        st.info("💡 TikTok puede requerir estar logueado. Si el error persiste, usá YouTube.")

    return videos


def ordenar_videos(videos: list, criterio: str) -> list:
    """Ordena la lista de videos según el criterio elegido."""
    if criterio == "vistas":
        return sorted(videos, key=lambda x: x["vistas"], reverse=True)
    elif criterio == "fecha":
        return sorted(videos, key=lambda x: x.get("fecha_raw", ""), reverse=True)
    elif criterio == "duracion_asc":
        return sorted(videos, key=lambda x: int(x.get("duracion") or 0))
    elif criterio == "duracion_desc":
        return sorted(videos, key=lambda x: int(x.get("duracion") or 0), reverse=True)
    return videos


def formatear_duracion(segundos) -> str:
    """Convierte segundos a formato MM:SS o HH:MM:SS."""
    try:
        segundos = int(segundos or 0)
    except (TypeError, ValueError):
        return "—"
    if segundos <= 0:
        return "—"
    minutos, segs = divmod(segundos, 60)
    horas, minutos = divmod(minutos, 60)
    if horas > 0:
        return f"{horas}:{minutos:02d}:{segs:02d}"
    return f"{minutos}:{segs:02d}"


def rankear_con_ia(videos: list, nicho: str, plataforma: str) -> str:
    """Usa Claude para analizar los videos y determinar potencial viral."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "❌ Falta configurar ANTHROPIC_API_KEY en el archivo .env"
    if not videos:
        return "No hay videos para analizar."

    lista_videos = "\n".join([
        f"{i+1}. \"{v['titulo']}\" — {v['vistas']:,} vistas — @{v['canal']} — {formatear_duracion(v['duracion'])}"
        for i, v in enumerate(videos[:15])
    ])

    prompt = f"""Sos un experto en contenido viral de fútbol para {plataforma}. El canal es "viralesDelFutbol" — publica compilaciones, datos curiosos, debates y reels de fútbol para audiencia hispanohablante (principalmente Argentina y LATAM).

PLATAFORMA: {plataforma}
NICHO ANALIZADO: {nicho}
VIDEOS ENCONTRADOS:
{lista_videos}

Analizá estos videos y respondé en español con estas secciones:

1. **Top 3 temas con mayor potencial viral para fútbol**
   - Por qué funciona, qué lo hace viral, cómo replicar el formato en un canal de compilaciones

2. **Patrones detectados**
   - Palabras clave recurrentes en los títulos
   - Tipo de contenido que domina (goles, fails, highlights, debates)
   - Duración ideal detectada

3. **Ideas concretas para viralesDelFutbol**
   - 3 ideas de videos basadas en estos datos (con contexto del Mundial 2026 si aplica)
   - Título sugerido para cada idea (máx 60 chars, con emoji)
   - Por qué funcionaría ahora en el canal

4. **Keywords SEO recomendadas**
   - 8-10 keywords de fútbol para usar en títulos y descripciones

Sé específico y práctico. Considerá que el Mundial 2026 empieza el 11 de junio — es una oportunidad de tráfico masivo."""

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


def mostrar_tendencias():
    """Renderiza la interfaz del Buscador de Tendencias en Streamlit."""
    st.title("⚽ Buscador de Tendencias de Fútbol")
    st.markdown("Buscá los videos de fútbol más virales en YouTube o TikTok para inspirar el próximo contenido del canal.")

    with st.expander("❓ ¿Cómo usar este módulo?"):
        st.markdown("""
**¿Qué hace?**
Busca los videos más vistos en YouTube o TikTok por nicho, con filtros avanzados, y usa Claude para identificar cuáles tienen más potencial viral.

**Pasos:**
1. Elegí la **plataforma**: YouTube o TikTok
2. Seleccioná un **nicho** o escribí tu propio término
3. Configurá los **filtros** según lo que buscás
4. Presioná **Buscar Tendencias**

**Filtros disponibles:**
- **Duración** — filtrá por rango de minutos (ideal: 5-15 min para compilaciones)
- **Mínimo de vistas** — ignorá videos con pocas vistas
- **Antigüedad** — solo videos recientes (útil para tendencias actuales)
- **Solo Creative Commons** — videos que podés reutilizar sin copyright
- **Ordenar por** — vistas, fecha, duración

**Tip de copyright:** Activá "Solo Creative Commons" si querés usar los clips directamente sin riesgo de strikes en YouTube.
        """)

    # ── Plataforma ────────────────────────────────────────────
    plataforma = st.radio("Plataforma", options=["YouTube", "TikTok"], horizontal=True)
    nichos = NICHOS_YOUTUBE if plataforma == "YouTube" else NICHOS_TIKTOK
    icono = "▶️" if plataforma == "YouTube" else "🎵"

    st.markdown("---")

    # ── Término de búsqueda ───────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        nicho_seleccionado = st.selectbox(f"Nicho en {plataforma}", options=list(nichos.keys()))
    with col2:
        termino_personalizado = st.text_input(
            "O ingresa un término personalizado (opcional)",
            placeholder="Ej: goles increíbles 2026, fails de arqueros",
        )

    # ── Filtros avanzados ─────────────────────────────────────
    st.markdown("#### Filtros avanzados")

    col_f1, col_f2, col_f3 = st.columns(3)

    with col_f1:
        st.markdown("**⏱ Duración del video**")
        dur_min = st.number_input("Mínimo (minutos)", min_value=0, max_value=60, value=0)
        dur_max = st.number_input("Máximo (minutos)", min_value=0, max_value=120, value=0, help="0 = sin límite")

    with col_f2:
        st.markdown("**👁 Vistas y fecha**")
        min_vistas_label = st.selectbox("Mínimo de vistas", options=list(MINIMO_VISTAS.keys()), index=2)
        antiguedad_label = st.selectbox("Antigüedad máxima", options=list(FECHAS.keys()), index=2)

    with col_f3:
        st.markdown("**⚙️ Opciones extra**")
        solo_cc = st.checkbox(
            "Solo Creative Commons",
            help="Agrega 'creative commons' al término de búsqueda para encontrar videos reutilizables"
        )
        ordenar_label = st.selectbox("Ordenar resultados por", options=list(ORDENAR_POR.keys()))
        max_resultados = st.slider("Cantidad de resultados", min_value=5, max_value=25, value=15)

    # ── Botón de búsqueda ─────────────────────────────────────
    st.markdown("---")
    buscar = st.button(f"🔍 Buscar en {plataforma}", type="primary", use_container_width=True)

    if buscar:
        termino = termino_personalizado.strip() if termino_personalizado.strip() else nichos[nicho_seleccionado]
        nombre_nicho = termino_personalizado.strip() if termino_personalizado.strip() else nicho_seleccionado

        # Armar dict de filtros
        filtros = {
            "min_dur_seg": int(dur_min * 60),
            "max_dur_seg": int(dur_max * 60) if dur_max > 0 else 0,
            "min_vistas": MINIMO_VISTAS[min_vistas_label],
            "dias": FECHAS[antiguedad_label],
            "solo_cc": solo_cc,
        }

        # Mostrar resumen de filtros activos
        filtros_activos = []
        if dur_min > 0 or dur_max > 0:
            rango = f"{dur_min}min" if dur_min else "0"
            rango += f" – {dur_max}min" if dur_max else " en adelante"
            filtros_activos.append(f"Duración: {rango}")
        if filtros["min_vistas"] > 0:
            filtros_activos.append(f"Mínimo {min_vistas_label}")
        if filtros["dias"]:
            filtros_activos.append(f"Últimos {filtros['dias']} días")
        if solo_cc:
            filtros_activos.append("Solo Creative Commons")

        if filtros_activos:
            st.info(f"Filtros activos: {' · '.join(filtros_activos)}")

        # Si Creative Commons está activo, añadirlo al término de búsqueda
        termino_busqueda = f"{termino} creative commons" if solo_cc and plataforma == "YouTube" else termino

        with st.spinner(f"Buscando en {plataforma}: '{termino_busqueda}'..."):
            if plataforma == "YouTube":
                videos = buscar_en_youtube(termino_busqueda, max_resultados, filtros)
            else:
                videos = buscar_en_tiktok(termino, max_resultados, filtros)

        if not videos:
            st.warning("No se encontraron videos con esos filtros. Probá ampliar el rango de duración o bajar el mínimo de vistas.")
            return

        # Ordenar según criterio elegido
        videos = ordenar_videos(videos, ORDENAR_POR[ordenar_label])

        st.success(f"✅ {len(videos)} videos encontrados en **{plataforma}** — **{nombre_nicho}**")
        st.markdown("---")

        # ── Lista de resultados ───────────────────────────────
        st.subheader(f"📋 Resultados {icono} {plataforma}")

        for i, video in enumerate(videos, 1):
            with st.container():
                col_num, col_info, col_stats = st.columns([0.5, 4, 1.5])

                with col_num:
                    st.markdown(f"**#{i}**")

                with col_info:
                    st.markdown(f"**[{video['titulo']}]({video['url']})**")
                    licencia_badge = "🟢 CC" if "Creative Commons" in video["licencia"] else "🔴 ©"
                    st.caption(
                        f"@{video['canal']} · {formatear_duracion(video['duracion'])} · "
                        f"{video['fecha']} · {licencia_badge} {video['licencia']}"
                    )

                with col_stats:
                    vistas = video["vistas"]
                    if vistas >= 1_000_000:
                        vistas_str = f"{vistas/1_000_000:.1f}M 👁"
                    elif vistas >= 1_000:
                        vistas_str = f"{vistas/1_000:.0f}K 👁"
                    else:
                        vistas_str = f"{vistas} 👁" if vistas > 0 else "N/D"
                    st.metric("Vistas", vistas_str)

            st.divider()

        # ── Análisis IA ───────────────────────────────────────
        st.markdown("---")
        st.subheader("🤖 Análisis de Potencial Viral (Claude)")

        with st.spinner("Claude está analizando el potencial viral..."):
            analisis = rankear_con_ia(videos, nombre_nicho, plataforma)

        st.markdown(analisis)

        # ── Exportar ──────────────────────────────────────────
        st.markdown("---")
        col_exp1, col_exp2 = st.columns(2)

        with col_exp1:
            lista_texto = "\n".join([
                f"{i+1}. {v['titulo']}\n   Vistas: {v['vistas']:,} · Duración: {formatear_duracion(v['duracion'])} · {v['licencia']}\n   {v['url']}"
                for i, v in enumerate(videos)
            ])
            st.download_button(
                "📥 Descargar lista de videos",
                data=lista_texto,
                file_name=f"tendencias_{plataforma}_{nombre_nicho.replace(' ', '_')}.txt",
                mime="text/plain"
            )

        with col_exp2:
            st.download_button(
                "📥 Descargar análisis IA",
                data=analisis,
                file_name=f"analisis_{plataforma}_{nombre_nicho.replace(' ', '_')}.txt",
                mime="text/plain"
            )
