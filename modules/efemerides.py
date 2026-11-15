"""
Efemérides de fútbol — motivo del segundo reel diario (19:00 hora Israel).

El dataset vive en data/efemerides.json y es la ÚNICA fuente de verdad: el gancho
que se quema en pantalla, el título de YouTube y la línea de contexto salen de ahí,
nunca de texto libre generado por Claude. Así se cumple la regla de no afirmar nada
que no esté en la fuente.

Si un día tiene más de una efeméride se prioriza homenaje > aniversario > cumpleaños,
y dentro del mismo tipo se elige la que hace más tiempo no se usa (data/efemerides_usadas.json).
"""
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz

_ARCHIVO = Path(__file__).parent.parent / "data" / "efemerides.json"
_USADAS = Path(__file__).parent.parent / "data" / "efemerides_usadas.json"

TZ_ISRAEL = pytz.timezone("Asia/Jerusalem")

# Menor número = más prioridad cuando varias efemérides caen el mismo día.
_PRIORIDAD = {"homenaje": 0, "aniversario": 1, "cumpleaños": 2}

# Plantillas por defecto. Una entrada puede pisarlas con sus propios campos
# "gancho" / "titulo" (ej. los cumpleaños de Pelé y Maradona, que no "cumplen").
_GANCHO_DEFAULT = {
    "cumpleaños":  "{CORTO} CUMPLE {n}",
    "aniversario": "{n} AÑOS DE ESTE MOMENTO",
    "homenaje":    "{n} AÑOS SIN {CORTO}",
}
_TITULO_DEFAULT = {
    "cumpleaños":  "{jugador} cumple {n} años 🎂",
    "aniversario": "Hoy hace {n} años: {evento}",
    "homenaje":    "Recordando a {jugador}, a {n} años de su partida",
}


def cargar() -> list[dict]:
    """Lee el dataset. Devuelve [] si el archivo no existe o está corrupto."""
    try:
        with open(_ARCHIVO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def guardar(entradas: list[dict]) -> None:
    """
    Reescribe el dataset completo, ordenado por mes y día. Escritura atómica
    (.tmp + replace) para que el job de las 19:00 nunca lea un archivo a medio escribir.
    """
    ordenadas = sorted(entradas, key=lambda e: (int(e["mes"]), int(e["dia"]), e["id"]))
    _ARCHIVO.parent.mkdir(exist_ok=True)
    tmp = _ARCHIVO.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ordenadas, f, ensure_ascii=False, indent=2)
    tmp.replace(_ARCHIVO)


def generar_id(dia: int, mes: int, corto: str) -> str:
    """ID con el formato del dataset: '25-11-maradona'."""
    slug = unicodedata.normalize("NFKD", corto.strip().lower())
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return f"{int(dia):02d}-{int(mes):02d}-{slug}"


def validar(entrada: dict, existentes: list[dict] | None = None,
            id_original: str | None = None) -> list[str]:
    """
    Revisa una entrada antes de guardarla. Devuelve la lista de errores ([] si está bien).

    Es la misma validación para alta y edición: en edición hay que pasar `id_original`
    para que no se choque consigo misma al controlar que el ID sea único.
    """
    errores: list[str] = []
    existentes = existentes if existentes is not None else cargar()

    # ── Fecha ──
    try:
        dia, mes = int(entrada.get("dia", 0)), int(entrada.get("mes", 0))
    except (TypeError, ValueError):
        errores.append("El día y el mes tienen que ser números.")
        dia = mes = 0
    if not 1 <= mes <= 12:
        errores.append("El mes tiene que estar entre 1 y 12.")
    else:
        try:
            # 2024 es bisiesto: así 29/02 se acepta como fecha válida del dataset.
            date(2024, mes, dia)
        except ValueError:
            errores.append(f"El {dia}/{mes} no existe en el calendario.")

    # ── Año ──
    try:
        anio = int(entrada.get("anio", 0))
        if not 1850 <= anio <= hoy_israel().year:
            errores.append(f"El año tiene que estar entre 1850 y {hoy_israel().year}.")
    except (TypeError, ValueError):
        errores.append("El año tiene que ser un número.")

    # ── Tipo ──
    tipo = entrada.get("tipo")
    if tipo not in _PRIORIDAD:
        errores.append(f"El tipo tiene que ser uno de: {', '.join(_PRIORIDAD)}.")

    # ── Textos obligatorios ──
    for campo, etiqueta in (("jugador", "jugador"), ("corto", "nombre corto"),
                            ("contexto", "contexto")):
        if not str(entrada.get(campo, "")).strip():
            errores.append(f"Falta el {etiqueta}.")

    # El título por defecto de los aniversarios interpola {evento}: sin eso sale vacío.
    if tipo == "aniversario" and not str(entrada.get("evento", "")).strip():
        errores.append("Los aniversarios necesitan el campo 'evento' (lo usa el título).")

    # ── Hashtag ──
    hashtag = str(entrada.get("hashtag", "")).strip()
    if not hashtag:
        errores.append("Falta el hashtag.")
    elif not re.fullmatch(r"[a-z0-9_]+", hashtag):
        errores.append(
            "El hashtag va sin '#', en minúsculas y sin espacios ni acentos "
            "(así se busca en TikTok): por ejemplo 'maradona' o 'cr7goals'."
        )

    # ── ID único ──
    id_nuevo = str(entrada.get("id", "")).strip()
    if not id_nuevo:
        errores.append("Falta el ID.")
    else:
        chocados = [e for e in existentes
                    if e["id"] == id_nuevo and e["id"] != id_original]
        if chocados:
            errores.append(f"Ya existe una efeméride con el ID '{id_nuevo}'.")

    return errores


def _cargar_usadas() -> dict:
    try:
        with open(_USADAS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def marcar_usada(entrada: dict, hoy: date | None = None) -> None:
    """Registra que `entrada` se usó hoy, para desempatar en años siguientes."""
    hoy = hoy or hoy_israel()
    usadas = _cargar_usadas()
    usadas[entrada["id"]] = hoy.isoformat()
    _USADAS.parent.mkdir(exist_ok=True)
    tmp = _USADAS.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(usadas, f, ensure_ascii=False, indent=2)
    tmp.replace(_USADAS)


def hoy_israel() -> date:
    """La fecha de hoy en hora de Israel — el reel del día se publica a las 19:00 allá."""
    return datetime.now(TZ_ISRAEL).date()


def anios(entrada: dict, hoy: date | None = None) -> int:
    """Años cumplidos desde el hecho (ej. 66 para Maradona el 30/10/2026)."""
    hoy = hoy or hoy_israel()
    return hoy.year - int(entrada["anio"])


def es_respetuosa(entrada: dict) -> bool:
    """
    True si el reel tiene que ir con tono neutro: música tranquila, título sobrio,
    sin encuesta ni frase polémica al final. Aplica a homenajes y a los cumpleaños
    de leyendas ya fallecidas.
    """
    return entrada.get("tipo") == "homenaje" or bool(entrada.get("fallecido"))


def texto_gancho(entrada: dict, hoy: date | None = None) -> str:
    """Gancho para quemar en pantalla: MAYÚSCULAS, corto, armado desde el dataset."""
    n = anios(entrada, hoy)
    plantilla = entrada.get("gancho") or _GANCHO_DEFAULT[entrada["tipo"]]
    return plantilla.format(
        n=n, CORTO=entrada["corto"].upper(), corto=entrada["corto"],
        JUGADOR=entrada["jugador"].upper(), jugador=entrada["jugador"],
    ).upper()


def titulo(entrada: dict, hoy: date | None = None) -> str:
    """Título de YouTube. Menciona el motivo y no afirma nada fuera del dataset."""
    n = anios(entrada, hoy)
    plantilla = entrada.get("titulo") or _TITULO_DEFAULT[entrada["tipo"]]
    base = plantilla.format(
        n=n, jugador=entrada["jugador"], corto=entrada["corto"],
        evento=entrada.get("evento", ""),
    ).strip()
    # YouTube corta en 100 caracteres; " #Shorts" ayuda a que entre en el feed de Shorts.
    sufijo = " #Shorts"
    if len(base) + len(sufijo) > 100:
        base = base[: 100 - len(sufijo) - 1].rstrip(" ,.;:-") + "…"
    return base + sufijo


def proxima_fecha(entrada: dict, desde: date | None = None) -> date:
    """
    La próxima vez que cae esta efeméride, contando desde `desde` (hoy incluido).
    Si el día/mes ya pasó este año, devuelve la del año que viene.
    """
    desde = desde or hoy_israel()
    try:
        this_year = date(desde.year, entrada["mes"], entrada["dia"])
    except ValueError:
        # 29/02 en un año no bisiesto: se corre al 1 de marzo, igual que lo haría YouTube.
        this_year = date(desde.year, 3, 1)
    if this_year >= desde:
        return this_year
    try:
        return date(desde.year + 1, entrada["mes"], entrada["dia"])
    except ValueError:
        return date(desde.year + 1, 3, 1)


def proximas(dias: int = 30, desde: date | None = None) -> list[tuple[date, dict]]:
    """
    Las efemérides que caen en los próximos `dias` días, de la más cercana a la más
    lejana. Devuelve (fecha_de_la_proxima_ocurrencia, entrada) — la fecha hace falta
    para calcular los años con precisión cuando el día todavía no llegó.

    Es solo para la pantalla de la web: el reel de las 19:00 sigue usando
    efemeride_de_hoy(), que resuelve los empates del mismo día.
    """
    desde = desde or hoy_israel()
    limite = desde + timedelta(days=dias)
    con_fecha = [(proxima_fecha(e, desde), e) for e in cargar()]
    dentro = [(f, e) for f, e in con_fecha if f <= limite]
    dentro.sort(key=lambda par: (par[0], _PRIORIDAD.get(par[1]["tipo"], 9)))
    return dentro


def efemeride_de_hoy(hoy: date | None = None) -> dict | None:
    """
    La efeméride que le toca al día de hoy, o None si hoy no hay ninguna —
    en ese caso el bot simplemente no publica el segundo reel.
    """
    hoy = hoy or hoy_israel()
    candidatas = [e for e in cargar() if e["dia"] == hoy.day and e["mes"] == hoy.month]
    if not candidatas:
        return None
    usadas = _cargar_usadas()
    # Desempate: primero por prioridad de tipo, después la que hace más tiempo no se usa
    # ("" ordena antes que cualquier fecha ISO, así que una nunca usada gana siempre), y
    # al final por id. Ese último criterio evita que decida el orden del archivo: la
    # pantalla del Calendario reescribe efemerides.json ordenado y no tiene por qué
    # cambiar qué reel sale un día con dos efemérides del mismo tipo.
    candidatas.sort(key=lambda e: (_PRIORIDAD[e["tipo"]], usadas.get(e["id"], ""), e["id"]))
    return candidatas[0]
