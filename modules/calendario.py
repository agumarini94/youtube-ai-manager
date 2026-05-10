"""
Módulo — Calendario de Eventos Virales
Muestra eventos próximos relevantes para planificar contenido en YouTube.
"""

import os
import anthropic
import streamlit as st
from datetime import date, timedelta


# ── Base de eventos ───────────────────────────────────────────────────────────
# cada evento tiene: inicio, fin (opcional), nombre, categoria, emoji, ideas[]

EVENTOS = [
    # ── Mayo 2026 ─────────────────────────────────────────────────────────────
    {
        "inicio": date(2026, 5, 4),
        "fin": None,
        "nombre": "Star Wars Day",
        "categoria": "Entretenimiento",
        "emoji": "⚔️",
        "ideas": [
            "Fails de cosplay de Star Wars — compilación TikTok",
            "Escenas icónicas recreadas que salieron horrible",
            "Reacciones graciosas de personas que nunca vieron Star Wars",
        ],
    },
    {
        "inicio": date(2026, 5, 5),
        "fin": None,
        "nombre": "Cinco de Mayo",
        "categoria": "Cultura",
        "emoji": "🇲🇽",
        "ideas": [
            "Fails de fiestas y celebraciones latinas — compilación",
            "Humor latino viral de TikTok para la fecha",
            "Reacciones de gringos probando comida mexicana por primera vez",
        ],
    },
    {
        "inicio": date(2026, 5, 10),
        "fin": None,
        "nombre": "Día de la Madre",
        "categoria": "Fechas Especiales",
        "emoji": "💐",
        "ideas": [
            "Hijos cocinando para sus mamás — fails épicos de cocina",
            "Regalos de Día de la Madre que salieron muy mal",
            "Momentos tiernos y graciosos de mamás en TikTok",
        ],
    },
    {
        "inicio": date(2026, 5, 12),
        "fin": date(2026, 5, 16),
        "nombre": "Eurovision 2026",
        "categoria": "Entretenimiento",
        "emoji": "🎤",
        "ideas": [
            "Los momentos más extraños y virales de Eurovision — compilación",
            "Outfits más ridículos de la historia de Eurovision",
            "Reacciones del público que se volvieron meme",
        ],
    },
    {
        "inicio": date(2026, 5, 30),
        "fin": date(2026, 5, 31),
        "nombre": "Final Champions League",
        "categoria": "Deportes",
        "emoji": "🏟️",
        "ideas": [
            "Fails de jugadores en finales de Champions — compilación histórica",
            "Reacciones de hinchas viendo la final en vivo",
            "Los penales más nerviosos de la historia del fútbol",
        ],
    },
    # ── Junio 2026 ────────────────────────────────────────────────────────────
    {
        "inicio": date(2026, 6, 8),
        "fin": date(2026, 6, 8),
        "nombre": "Roland Garros — Final",
        "categoria": "Deportes",
        "emoji": "🎾",
        "ideas": [
            "Reacciones más épicas del tenis en tierra batida",
            "Fails y momentos virales del torneo",
        ],
    },
    {
        "inicio": date(2026, 6, 11),
        "fin": date(2026, 7, 19),
        "nombre": "FIFA World Cup 2026",
        "categoria": "Deportes",
        "emoji": "⚽",
        "ideas": [
            "Compilación de fails y errores épicos del Mundial 2026",
            "Reacciones más graciosas de hinchas viendo los partidos",
            "Los goles que no fueron — fails de arqueros y delanteros",
            "Hinchas que predijeron resultados y fallaron completamente",
            "Celebraciones de gol que salieron muy mal — compilación",
            "Momentos virales de TikTok durante el Mundial",
        ],
    },
    {
        "inicio": date(2026, 6, 21),
        "fin": None,
        "nombre": "Día del Padre",
        "categoria": "Fechas Especiales",
        "emoji": "👨‍👧",
        "ideas": [
            "Padres intentando entender TikTok — compilación",
            "Fails de papás siendo 'cool' con sus hijos",
            "Momentos más tiernos y graciosos de papás en redes",
        ],
    },
    # ── Julio 2026 ────────────────────────────────────────────────────────────
    {
        "inicio": date(2026, 7, 4),
        "fin": None,
        "nombre": "Independence Day (EE.UU.)",
        "categoria": "Cultura",
        "emoji": "🎆",
        "ideas": [
            "Fails épicos con fuegos artificiales del 4 de julio",
            "BBQ fails — asados americanos que salieron desastrosos",
            "Reacciones graciosas de extranjeros celebrando el 4 de julio",
        ],
    },
    {
        "inicio": date(2026, 7, 7),
        "fin": date(2026, 7, 19),
        "nombre": "Wimbledon 2026",
        "categoria": "Deportes",
        "emoji": "🎾",
        "ideas": [
            "Momentos más virales de Wimbledon — reacciones y fails",
            "La elegancia de Wimbledon vs. los fails del público",
        ],
    },
    {
        "inicio": date(2026, 7, 19),
        "fin": None,
        "nombre": "Final del Mundial 2026",
        "categoria": "Deportes",
        "emoji": "🏆",
        "ideas": [
            "Reacciones más épicas de hinchas viendo la final en vivo",
            "Compilación de los mejores momentos del Mundial completo",
            "El antes y después de la final — reacciones de los países",
        ],
    },
    # ── Agosto 2026 ──────────────────────────────────────────────────────────
    {
        "inicio": date(2026, 8, 24),
        "fin": date(2026, 9, 7),
        "nombre": "US Open 2026",
        "categoria": "Deportes",
        "emoji": "🎾",
        "ideas": [
            "Reacciones del público en el US Open — compilación viral",
            "Momentos más tensos y graciosos del US Open",
        ],
    },
    {
        "inicio": date(2026, 8, 1),
        "fin": date(2026, 8, 31),
        "nombre": "Back to School",
        "categoria": "Tendencia",
        "emoji": "🎒",
        "ideas": [
            "Fails épicos del primer día de clases — compilación TikTok",
            "Padres comprando útiles — momentos graciosos",
            "Primer día de colegio: expectativa vs. realidad",
            "Reacciones de papás cuando los chicos vuelven al cole",
        ],
    },
    # ── Septiembre 2026 ──────────────────────────────────────────────────────
    {
        "inicio": date(2026, 9, 1),
        "fin": date(2026, 9, 1),
        "nombre": "Inicio temporada NFL",
        "categoria": "Deportes",
        "emoji": "🏈",
        "ideas": [
            "Fails y tackles más épicos del fútbol americano — compilación",
            "Reacciones de fans en el estadio que se volvieron virales",
        ],
    },
    # ── Octubre 2026 ─────────────────────────────────────────────────────────
    {
        "inicio": date(2026, 10, 31),
        "fin": None,
        "nombre": "Halloween",
        "categoria": "Fechas Especiales",
        "emoji": "🎃",
        "ideas": [
            "Sustos de Halloween que salieron demasiado bien — compilación viral",
            "Disfraces de Halloween que fallaron épicamente",
            "Reacciones de niños cuando los asustan — fails graciosos",
            "Decoraciones de Halloween que salieron desastrosas",
            "Compilación: los mejores jumpscares de TikTok en Halloween",
        ],
    },
    # ── Noviembre 2026 ───────────────────────────────────────────────────────
    {
        "inicio": date(2026, 11, 1),
        "fin": date(2026, 11, 2),
        "nombre": "Día de los Muertos",
        "categoria": "Cultura",
        "emoji": "💀",
        "ideas": [
            "Altares más creativos del Día de Muertos en TikTok",
            "Maquillajes de calavera que salieron horrible — compilación",
            "Celebraciones más virales del Día de los Muertos",
        ],
    },
    {
        "inicio": date(2026, 11, 26),
        "fin": None,
        "nombre": "Thanksgiving (EE.UU.)",
        "categoria": "Cultura",
        "emoji": "🦃",
        "ideas": [
            "Turkey fails — pavos del Thanksgiving que salieron desastrosos",
            "Familias en Thanksgiving — momentos graciosos en TikTok",
            "Fails de cocina del Día de Acción de Gracias",
        ],
    },
    {
        "inicio": date(2026, 11, 27),
        "fin": None,
        "nombre": "Black Friday",
        "categoria": "Tendencia",
        "emoji": "🛒",
        "ideas": [
            "Peleas y chaos en el Black Friday — compilación épica",
            "Gente haciendo cola toda la noche para entrar al shopping",
            "Compras del Black Friday que no valieron para nada — fails",
        ],
    },
    # ── Diciembre 2026 ───────────────────────────────────────────────────────
    {
        "inicio": date(2026, 12, 25),
        "fin": None,
        "nombre": "Navidad",
        "categoria": "Fechas Especiales",
        "emoji": "🎄",
        "ideas": [
            "Decoraciones navideñas que salieron desastrosas — fails épicos",
            "Regalos de Navidad con reacciones inesperadas — compilación TikTok",
            "Papá Noel con niños asustados — los videos más virales",
            "Luces de Navidad que fallaron espectacularmente",
            "Chicos creyendo todavía en Papá Noel — momentos tiernos",
        ],
    },
    {
        "inicio": date(2026, 12, 31),
        "fin": None,
        "nombre": "Nochevieja / Año Nuevo",
        "categoria": "Fechas Especiales",
        "emoji": "🥂",
        "ideas": [
            "Brindis de Año Nuevo que salieron completamente mal",
            "Fails con fuegos artificiales de fin de año — compilación",
            "Reacciones más graciosas esperando la medianoche en TikTok",
            "Propósitos de Año Nuevo que fallaron en el primer día",
        ],
    },
    # ── 2027 ─────────────────────────────────────────────────────────────────
    {
        "inicio": date(2027, 2, 7),
        "fin": None,
        "nombre": "Super Bowl LXI",
        "categoria": "Deportes",
        "emoji": "🏈",
        "ideas": [
            "Reacciones de hinchas en el Super Bowl — las más virales",
            "Fails del medio tiempo y los comerciales más raros",
            "El Super Bowl explicado para personas que no entienden nada",
        ],
    },
    {
        "inicio": date(2027, 2, 14),
        "fin": None,
        "nombre": "San Valentín",
        "categoria": "Fechas Especiales",
        "emoji": "💘",
        "ideas": [
            "Propuestas de San Valentín que salieron muy mal — fails románticos",
            "Cenas románticas que se convirtieron en desastre — compilación",
            "Regalos de San Valentín más raros que encontraron en TikTok",
        ],
    },
    {
        "inicio": date(2027, 3, 17),
        "fin": None,
        "nombre": "St. Patrick's Day",
        "categoria": "Cultura",
        "emoji": "🍀",
        "ideas": [
            "Fails de las celebraciones del St. Patrick's en TikTok",
            "Personas teñidas de verde que no querían — compilación",
        ],
    },
]

COLORES_CATEGORIA = {
    "Deportes":         "#1e3a5f",
    "Entretenimiento":  "#3d1a5f",
    "Fechas Especiales": "#1a3d1f",
    "Cultura":          "#3d2a1a",
    "Tendencia":        "#2a1a3d",
}

BADGES_CATEGORIA = {
    "Deportes":         "🏅 Deportes",
    "Entretenimiento":  "🎭 Entretenimiento",
    "Fechas Especiales": "📅 Fecha Especial",
    "Cultura":          "🌍 Cultura",
    "Tendencia":        "📈 Tendencia",
}


def _dias_hasta(inicio: date) -> int:
    return (inicio - date.today()).days


def _formato_fecha(inicio: date, fin: date | None) -> str:
    meses = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
             "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    if fin and fin != inicio:
        if inicio.month == fin.month:
            return f"{inicio.day}–{fin.day} {meses[inicio.month]} {inicio.year}"
        return f"{inicio.day} {meses[inicio.month]} – {fin.day} {meses[fin.month]} {fin.year}"
    return f"{inicio.day} {meses[inicio.month]} {inicio.year}"


def _urgencia_color(dias: int) -> str:
    if dias <= 7:
        return "#ff4444"
    if dias <= 30:
        return "#ff9500"
    return "#00c851"


def _urgencia_label(dias: int) -> str:
    if dias == 0:
        return "¡HOY!"
    if dias == 1:
        return "¡Mañana!"
    if dias <= 7:
        return f"En {dias} días"
    if dias <= 30:
        return f"En {dias} días"
    return f"En {dias} días"


def _generar_ideas_claude(evento: dict) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "❌ No hay ANTHROPIC_API_KEY configurada."

    ideas_base = "\n".join(f"- {i}" for i in evento["ideas"])
    fecha_str = _formato_fecha(evento["inicio"], evento["fin"])

    prompt = f"""Sos un experto en contenido viral para YouTube. El canal es ViralLocos, un canal enfocado en compilaciones de TikTok, fails, humor y momentos graciosos.

Evento próximo: {evento['emoji']} {evento['nombre']} ({fecha_str})
Categoría: {evento['categoria']}

Ideas iniciales que ya tenemos:
{ideas_base}

Generá 5 ideas NUEVAS y específicas de videos para este canal, pensando en:
- Qué tipo de videos de TikTok van a viralizarse alrededor de esta fecha
- Títulos concretos que usen keywords de búsqueda de YouTube
- Formatos que funcionan en el nicho de fails/humor/compilaciones

Respondé SOLO con una lista numerada de las 5 ideas. Sin intro, sin conclusión."""

    try:
        cliente = anthropic.Anthropic(api_key=api_key)
        resp = cliente.messages.create(
            model="claude-opus-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as e:
        return f"❌ Error al generar ideas: {e}"


def mostrar_calendario():
    st.title("📅 Calendario de Eventos Virales")
    st.markdown(
        "Eventos próximos para planificar tu contenido. "
        "Publicar alrededor de estas fechas multiplica las chances de viralizar."
    )

    hoy = date.today()

    # ── Filtros ───────────────────────────────────────────────────────────────
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        categorias = ["Todas"] + sorted({e["categoria"] for e in EVENTOS})
        cat_sel = st.selectbox("Filtrar por categoría", categorias, key="cal_cat")
    with col_f2:
        meses_adelante = st.selectbox(
            "Mostrar próximos",
            [1, 2, 3, 6, 12],
            index=2,
            format_func=lambda m: f"{m} mes{'es' if m > 1 else ''}",
            key="cal_meses",
        )

    limite = hoy + timedelta(days=meses_adelante * 30)

    # Filtrar y ordenar
    eventos_filtrados = [
        e for e in EVENTOS
        if e["inicio"] >= hoy
        and e["inicio"] <= limite
        and (cat_sel == "Todas" or e["categoria"] == cat_sel)
    ]
    eventos_filtrados.sort(key=lambda e: e["inicio"])

    if not eventos_filtrados:
        st.info("No hay eventos en el período seleccionado para esa categoría.")
        return

    st.markdown(f"**{len(eventos_filtrados)} eventos** en los próximos {meses_adelante} meses")
    st.markdown("---")

    # ── Cards de eventos ──────────────────────────────────────────────────────
    for evento in eventos_filtrados:
        dias = _dias_hasta(evento["inicio"])
        color_urgencia = _urgencia_color(dias)
        badge_cat = BADGES_CATEGORIA.get(evento["categoria"], evento["categoria"])
        fecha_str = _formato_fecha(evento["inicio"], evento["fin"])
        color_fondo = COLORES_CATEGORIA.get(evento["categoria"], "#1a1a2e")

        st.markdown(
            f"""
            <div style="
                background: {color_fondo};
                border-left: 4px solid {color_urgencia};
                border-radius: 8px;
                padding: 14px 18px;
                margin-bottom: 10px;
            ">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:20px; font-weight:700; color:#fff;">
                        {evento['emoji']} {evento['nombre']}
                    </span>
                    <span style="
                        background:{color_urgencia};
                        color:#fff;
                        border-radius:12px;
                        padding:3px 12px;
                        font-size:13px;
                        font-weight:700;
                    ">{_urgencia_label(dias)}</span>
                </div>
                <div style="margin-top:4px; color:#aaa; font-size:13px;">
                    📆 {fecha_str} &nbsp;·&nbsp; {badge_cat}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander(f"Ver ideas de contenido para {evento['nombre']}"):
            st.markdown("**Ideas pre-armadas para tu canal:**")
            for idea in evento["ideas"]:
                st.markdown(f"- {idea}")

            st.markdown("")

            key_btn = f"btn_ai_{evento['nombre'].replace(' ', '_')}"
            key_result = f"ai_result_{evento['nombre'].replace(' ', '_')}"

            if st.button(
                "✨ Generar más ideas con Claude AI",
                key=key_btn,
                use_container_width=True,
            ):
                with st.spinner("Generando ideas..."):
                    resultado = _generar_ideas_claude(evento)
                st.session_state[key_result] = resultado

            if key_result in st.session_state:
                st.markdown("**Ideas generadas por Claude:**")
                st.markdown(st.session_state[key_result])

        st.markdown("")
