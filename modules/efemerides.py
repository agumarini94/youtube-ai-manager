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
from datetime import date, datetime
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
    # ("" ordena antes que cualquier fecha ISO, así que una nunca usada gana siempre).
    candidatas.sort(key=lambda e: (_PRIORIDAD[e["tipo"]], usadas.get(e["id"], "")))
    return candidatas[0]
