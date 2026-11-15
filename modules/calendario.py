"""
Módulo — Calendario de Efemérides

Es la pantalla de `data/efemerides.json`: la MISMA fuente de verdad que usa el reel
de las 19:00 (`workflow.ejecutar_reel_del_dia_pipeline`). Muestra qué se va a publicar
cada día y permite editar el dataset sin abrir el JSON a mano.

Antes esta pantalla tenía su propia lista de eventos y le pedía a Claude que inventara
fechas de fútbol, en paralelo al dataset curado del bot. Eran dos calendarios que se
podían contradecir; quedó uno solo.

El gancho y el título que se muestran acá salen de efemerides.texto_gancho() y
efemerides.titulo() — las mismas funciones que corre el bot, no una copia.
"""

import streamlit as st
from datetime import date

from modules import efemerides


_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

_COLOR_TIPO = {
    "homenaje":    "#2d1b2e",
    "aniversario": "#1b2436",
    "cumpleaños":  "#1e2e1b",
}

_BADGE_TIPO = {
    "homenaje":    "🕯️ Homenaje",
    "aniversario": "📅 Aniversario",
    "cumpleaños":  "🎂 Cumpleaños",
}

_TIPOS = ["cumpleaños", "aniversario", "homenaje"]


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
    return f"En {dias} días"


def _formato_fecha(f: date) -> str:
    return f"{f.day} de {_MESES[f.month]} de {f.year}"


def _campos_entrada(entrada: dict | None, prefijo: str) -> dict:
    """
    Formulario de una efeméride. Devuelve el dict con el formato del dataset.
    `entrada` es None para un alta y la entrada actual para una edición.
    """
    e = entrada or {}

    col1, col2, col3 = st.columns(3)
    with col1:
        dia = st.number_input("Día", min_value=1, max_value=31,
                              value=int(e.get("dia", 1)), key=f"{prefijo}_dia")
    with col2:
        mes = st.number_input("Mes", min_value=1, max_value=12,
                              value=int(e.get("mes", 1)), key=f"{prefijo}_mes")
    with col3:
        anio = st.number_input(
            "Año del hecho", min_value=1850, max_value=efemerides.hoy_israel().year,
            value=int(e.get("anio", 2000)), key=f"{prefijo}_anio",
            help="El año en que nació el jugador, falleció, o pasó el hecho. "
                 "De acá sale el número de años que se muestra en el gancho.",
        )

    tipo = st.selectbox(
        "Tipo", _TIPOS,
        index=_TIPOS.index(e["tipo"]) if e.get("tipo") in _TIPOS else 0,
        key=f"{prefijo}_tipo",
        help="Si dos efemérides caen el mismo día, el bot prioriza "
             "homenaje > aniversario > cumpleaños.",
    )

    col4, col5 = st.columns(2)
    with col4:
        jugador = st.text_input("Jugador (nombre completo)", value=e.get("jugador", ""),
                                key=f"{prefijo}_jugador",
                                placeholder="Diego Maradona")
    with col5:
        corto = st.text_input("Nombre corto", value=e.get("corto", ""),
                              key=f"{prefijo}_corto", placeholder="Maradona",
                              help="El que entra en el gancho de pantalla, en MAYÚSCULAS.")

    hashtag = st.text_input(
        "Hashtag de TikTok (sin #)", value=e.get("hashtag", ""),
        key=f"{prefijo}_hashtag", placeholder="maradona",
        help="Con este hashtag el bot busca los clips. En minúsculas, sin espacios "
             "ni acentos. Si no alcanza, el bot reintenta con goals/skills/edit/highlights.",
    )

    contexto = st.text_area(
        "Contexto", value=e.get("contexto", ""), height=80, key=f"{prefijo}_contexto",
        help="Dato verificado del hecho. No se publica tal cual, pero es la fuente de "
             "la que sale todo: si no está acá, el reel no lo afirma.",
    )

    evento = st.text_input(
        "Evento (solo aniversarios)", value=e.get("evento", ""), key=f"{prefijo}_evento",
        placeholder="el gol a los ingleses",
        help="Lo interpola el título por defecto de los aniversarios: "
             "'Hoy hace N años: {evento}'.",
    )

    with st.expander("Opcionales — pisar el gancho y el título automáticos"):
        st.caption(
            "Dejalos vacíos y se usan las plantillas por tipo. Se completan a mano "
            "para los casos raros: por ejemplo el cumpleaños de alguien que ya falleció, "
            "que no 'cumple' años. Placeholders: {n}, {CORTO}, {corto}, {JUGADOR}, "
            "{jugador}, {evento}."
        )
        gancho = st.text_input("Gancho a medida", value=e.get("gancho", ""),
                               key=f"{prefijo}_gancho", placeholder="{n} AÑOS DEL 10")
        titulo = st.text_input("Título a medida", value=e.get("titulo", ""),
                               key=f"{prefijo}_titulo",
                               placeholder="{n} años del nacimiento de {jugador} 🎂")
        fallecido = st.checkbox(
            "El jugador ya falleció", value=bool(e.get("fallecido")),
            key=f"{prefijo}_fallecido",
            help="Marca el reel como respetuoso: música tranquila, sin encuesta "
                 "ni frase polémica al final.",
        )

    nueva = {
        "id": efemerides.generar_id(dia, mes, corto),
        "dia": int(dia),
        "mes": int(mes),
        "anio": int(anio),
        "tipo": tipo,
        "jugador": jugador.strip(),
        "corto": corto.strip(),
        "hashtag": hashtag.strip().lower(),
        "contexto": contexto.strip(),
    }
    # Los opcionales solo entran si tienen valor, para no ensuciar el JSON.
    if evento.strip():
        nueva["evento"] = evento.strip()
    if gancho.strip():
        nueva["gancho"] = gancho.strip()
    if titulo.strip():
        nueva["titulo"] = titulo.strip()
    if fallecido:
        nueva["fallecido"] = True
    return nueva


def _previsualizar(entrada: dict, fecha: date) -> None:
    """Muestra el gancho y el título que generaría esta entrada en esa fecha."""
    try:
        gancho = efemerides.texto_gancho(entrada, fecha)
        titulo = efemerides.titulo(entrada, fecha)
    except Exception as e:  # plantilla a medida con un placeholder que no existe
        st.error(f"No se pudo armar el texto: {e}")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.caption("Gancho en pantalla")
        st.code(gancho, language=None)
    with col2:
        st.caption(f"Título de YouTube — {len(titulo)} caracteres")
        st.code(titulo, language=None)
    if efemerides.es_respetuosa(entrada):
        st.caption("🕯️ Reel respetuoso: música tranquila, sin encuesta ni frase polémica.")


def mostrar_calendario():
    st.title("📅 Calendario de Efemérides")
    st.markdown(
        "El dataset del **reel de las 19:00**. Cada día que tiene efeméride, el bot "
        "arma y publica un reel solo; los días sin efeméride no publica nada."
    )

    with st.expander("❓ ¿Cómo funciona?"):
        st.markdown("""
**Esta pantalla edita `data/efemerides.json`**, que es la única fuente de verdad del
reel de las 19:00 (hora de Israel). El gancho que se quema en pantalla y el título de
YouTube salen de acá, nunca de texto libre de Claude: así el reel no afirma nada que
no esté en el dataset.

**Qué hace el bot con cada entrada:**
1. A las 19:00 busca si hoy hay efeméride. Si no hay, no publica — eso es lo esperado.
2. Si hay más de una el mismo día, prioriza **homenaje > aniversario > cumpleaños**, y
   dentro del mismo tipo elige la que hace más tiempo no se usa.
3. Busca clips en TikTok con el **hashtag**, y si no alcanzan reintenta con
   `hashtag + goals/skills/edit/highlights`.
4. Quema el **gancho** en pantalla y sube el video con el **título**.

**El año es el del hecho**, no el de la efeméride: para un cumpleaños va el año de
nacimiento, para un homenaje el del fallecimiento. El bot calcula los años solo.
        """)

    hoy = efemerides.hoy_israel()
    todas = efemerides.cargar()

    if not todas:
        st.error(
            "No se pudo leer `data/efemerides.json` o está vacío. "
            "Si el archivo existe, revisá que sea JSON válido antes de editar desde acá."
        )

    # ── Próximas efemérides ───────────────────────────────────────────────────
    col_f1, col_f2 = st.columns([1, 2])
    with col_f1:
        dias = st.selectbox(
            "Mostrar próximos", [30, 60, 90, 365], index=0,
            format_func=lambda d: "Todo el año" if d == 365 else f"{d} días",
            key="efem_dias",
        )
    with col_f2:
        tipos_sel = st.multiselect(
            "Filtrar por tipo", _TIPOS, default=_TIPOS, key="efem_tipos",
        )

    proximas = [(f, e) for f, e in efemerides.proximas(dias, hoy)
                if e["tipo"] in tipos_sel]

    st.caption(
        f"{len(proximas)} efemérides en el período · {len(todas)} en el dataset completo "
        f"· hoy en Israel es {_formato_fecha(hoy)}"
    )
    st.markdown("---")

    if not proximas:
        st.info("No hay efemérides en el período y los tipos seleccionados.")

    for fecha, entrada in proximas:
        dias_faltan = (fecha - hoy).days
        color_urgencia = _urgencia_color(dias_faltan)
        color_fondo = _COLOR_TIPO.get(entrada["tipo"], "#1a1a2e")
        badge = _BADGE_TIPO.get(entrada["tipo"], entrada["tipo"])
        n_anios = efemerides.anios(entrada, fecha)

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
                        {entrada['jugador']}
                    </span>
                    <span style="
                        background:{color_urgencia};
                        color:#fff;
                        border-radius:12px;
                        padding:3px 12px;
                        font-size:13px;
                        font-weight:700;
                    ">{_urgencia_label(dias_faltan)}</span>
                </div>
                <div style="margin-top:4px; color:#aaa; font-size:13px;">
                    📆 {_formato_fecha(fecha)} &nbsp;·&nbsp; {badge} &nbsp;·&nbsp;
                    {n_anios} años &nbsp;·&nbsp; <code>#{entrada['hashtag']}</code>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander(f"Ver qué publicaría el {fecha.day}/{fecha.month} · editar · borrar"):
            _previsualizar(entrada, fecha)
            st.caption("Contexto (la fuente del dato)")
            st.markdown(f"> {entrada.get('contexto', '—')}")
            st.caption(f"ID: `{entrada['id']}`")

            st.markdown("---")
            tab_editar, tab_borrar = st.tabs(["✏️ Editar", "🗑️ Borrar"])

            with tab_editar:
                editada = _campos_entrada(entrada, f"ed_{entrada['id']}")
                st.caption("Vista previa con los valores de arriba:")
                _previsualizar(editada, efemerides.proxima_fecha(editada, hoy))
                if st.button("💾 Guardar cambios", key=f"save_{entrada['id']}",
                             type="primary", use_container_width=True):
                    errores = efemerides.validar(editada, todas, id_original=entrada["id"])
                    if errores:
                        for err in errores:
                            st.error(err)
                    else:
                        restantes = [e for e in todas if e["id"] != entrada["id"]]
                        efemerides.guardar(restantes + [editada])
                        st.success(f"✅ Guardada: {editada['jugador']}")
                        st.rerun()

            with tab_borrar:
                st.warning(
                    f"Se va a borrar **{entrada['jugador']}** del dataset. "
                    f"El {fecha.day}/{fecha.month} deja de tener reel salvo que haya "
                    "otra efeméride ese día."
                )
                if st.checkbox("Sí, borrar esta efeméride", key=f"conf_{entrada['id']}"):
                    if st.button("🗑️ Borrar definitivamente", key=f"del_{entrada['id']}",
                                 use_container_width=True):
                        efemerides.guardar([e for e in todas if e["id"] != entrada["id"]])
                        st.success("Borrada.")
                        st.rerun()

    # ── Alta ──────────────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("➕ Agregar una efeméride nueva"):
        nueva = _campos_entrada(None, "nueva")
        st.caption("Vista previa:")
        _previsualizar(nueva, efemerides.proxima_fecha(nueva, hoy))
        if st.button("➕ Agregar al dataset", type="primary", use_container_width=True):
            errores = efemerides.validar(nueva, todas)
            if errores:
                for err in errores:
                    st.error(err)
            else:
                efemerides.guardar(todas + [nueva])
                st.success(f"✅ Agregada: {nueva['jugador']} — {nueva['dia']}/{nueva['mes']}")
                st.rerun()
