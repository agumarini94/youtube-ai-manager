"""
Módulo — Pronósticos del Mundial 2026
Obtiene partidos del día desde ESPN API y genera predicciones simulando 4 IAs con Claude real.
Video: 3 slides custom (título, tabla de marcadores, CTA) con fondo moderno oscuro.
"""

import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import requests
from PIL import Image, ImageDraw, ImageFont

ESPN_URL = "http://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
FFMPEG   = "/opt/homebrew/bin/ffmpeg"
_TZ_ARG  = timezone(timedelta(hours=-3))

_W, _H, _FPS  = 1080, 1920, 30
_SECS_TITULO  = 4.0
_SECS_CTA     = 5.0

def _secs_tabla(n_partidos: int) -> float:
    """3s por partido + base mínima de 6s, máximo 16s."""
    return min(16.0, max(6.0, 6.0 + n_partidos * 3.0))

# Colores por IA (para etiquetas en la tabla)
_AI_COLORS = {
    "Claude":   (100, 180, 255),   # azul claro
    "ChatGPT":  (80,  200, 140),   # verde
    "Gemini":   (180, 130, 255),   # violeta
    "Grok":     (255, 130,  60),   # naranja
}


# ── Font ────────────────────────────────────────────────────────────────────────

def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in (
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
    ):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def _fit_font(draw: ImageDraw.ImageDraw, text: str, start_size: int, max_w: int):
    """Reduce font size hasta que el texto quepa en max_w."""
    size = start_size
    while size > 22:
        f = _font(size)
        if _tw(draw, text, f) <= max_w:
            return f
        size -= 4
    return _font(22)


def _cx(draw: ImageDraw.ImageDraw, text: str, font, y: int,
        fill, stroke_fill=(0, 0, 0), stroke: int = 3) -> int:
    """Texto centrado con stroke. Devuelve altura del texto."""
    w = _tw(draw, text, font)
    x = (_W - w) // 2
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)
    return _th(draw, text, font)


def _arrow(draw: ImageDraw.ImageDraw, x: int, y_center: int,
           color, length: int = 36, head: int = 10):
    """Flecha → dibujada con PIL (sin depender del font para el símbolo)."""
    # Cuerpo
    draw.rectangle([x, y_center - 2, x + length, y_center + 2], fill=color)
    # Cabeza triangular
    draw.polygon([
        (x + length,          y_center - head // 2 - 1),
        (x + length + head,   y_center),
        (x + length,          y_center + head // 2 + 1),
    ], fill=color)


# ── Background moderno ───────────────────────────────────────────────────────────

def _modern_bg() -> Image.Image:
    """
    Campo de fútbol estilo 2026: franjas de pasto visibles, líneas blancas del
    campo, luz de estadio desde arriba, viñeta en bordes. Campo claramente visible.
    """
    base = Image.new("RGBA", (_W, _H))
    d    = ImageDraw.Draw(base)

    # ── Franjas de pasto (visibles, colores ricos) ──────────────────────────────
    c_a = (22,  92, 30, 255)   # verde oscuro
    c_b = (34, 122, 44, 255)   # verde más claro
    stripe = 88
    for y in range(0, _H, stripe):
        d.rectangle([0, y, _W, min(y + stripe, _H)],
                    fill=c_a if (y // stripe) % 2 == 0 else c_b)

    # ── Líneas blancas del campo ─────────────────────────────────────────────────
    mid = _H // 2
    cx  = _W // 2
    lw  = (255, 255, 255, 160)   # color de líneas

    # Borde del campo
    d.rectangle([38, 38, _W - 38, _H - 38], outline=lw, width=5)
    # Línea del medio
    d.line([(38, mid), (_W - 38, mid)], fill=lw, width=5)
    # Círculo central
    r = 215
    d.ellipse([cx - r, mid - r, cx + r, mid + r], outline=lw, width=5)
    # Punto central
    d.ellipse([cx - 7, mid - 7, cx + 7, mid + 7], fill=lw)
    # Área penal arriba
    pa_w = 390
    d.rectangle([(cx - pa_w // 2), 38, (cx + pa_w // 2), 38 + 210],
                outline=(255, 255, 255, 135), width=4)
    # Área penal abajo
    d.rectangle([(cx - pa_w // 2), _H - 38 - 210, (cx + pa_w // 2), _H - 38],
                outline=(255, 255, 255, 135), width=4)
    # Arco del área (arriba y abajo)
    d.arc([cx - 90, 210, cx + 90, 370], start=0,   end=180, fill=(255, 255, 255, 100), width=3)
    d.arc([cx - 90, _H - 370, cx + 90, _H - 210], start=180, end=360, fill=(255, 255, 255, 100), width=3)

    # ── Luz de estadio desde arriba (spot central) ───────────────────────────────
    light = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    ld    = ImageDraw.Draw(light)
    for rad in range(680, 0, -20):
        alpha = int(28 * (1 - rad / 680) ** 1.6)
        ld.ellipse([cx - rad, -120, cx + rad, rad // 2], fill=(200, 255, 160, alpha))
    base = Image.alpha_composite(base, light)

    # ── Viñeta: oscurece solo los bordes, el centro queda visible ────────────────
    vig = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    vd  = ImageDraw.Draw(vig)
    for i in range(48):
        alpha = int(185 * (1 - i / 48) ** 2.4)
        m = i * 7
        vd.rectangle([m, m, _W - m, _H - m], outline=(0, 0, 0, alpha), width=7)
    base = Image.alpha_composite(base, vig)

    # ── Overlay moderado (texto legible, campo todavía visible) ──────────────────
    ovl = Image.new("RGBA", (_W, _H), (0, 0, 0, 82))
    base = Image.alpha_composite(base, ovl)

    # ── Acento dorado arriba y abajo ─────────────────────────────────────────────
    img  = base.convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0,       _W, 6],   fill=(255, 205, 0))
    draw.rectangle([0, _H - 6,  _W, _H],  fill=(255, 205, 0))

    return img


# ── Slides ───────────────────────────────────────────────────────────────────────

def _slide_titulo(fecha: str, n: int) -> str:
    img  = _modern_bg()
    draw = ImageDraw.Draw(img)

    y = 310
    y += _cx(draw, "PREDICCIONES", _font(96), y,
              fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke=5) + 10
    y += _cx(draw, "DE LA IA", _font(96), y,
              fill=(255, 210, 0), stroke_fill=(0, 0, 0), stroke=5) + 46

    # Línea dorada decorativa
    draw.line([(_W // 2 - 230, y), (_W // 2 + 230, y)], fill=(255, 210, 0), width=4)
    y += 44

    y += _cx(draw, "MUNDIAL 2026", _font(66), y,
              fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke=3) + 30
    y += _cx(draw, fecha, _font(50), y,
              fill=(190, 190, 190), stroke_fill=(0, 0, 0), stroke=2) + 58
    y += _cx(draw, "Segun 4 inteligencias artificiales", _font(40), y,
              fill=(160, 160, 160), stroke_fill=(0, 0, 0), stroke=1) + 14
    _cx(draw, f"{n} partido{'s' if n != 1 else ''} de hoy", _font(40), y,
        fill=(160, 160, 160), stroke_fill=(0, 0, 0), stroke=1)

    out = tempfile.mktemp(suffix="_pron_titulo.jpg")
    img.save(out, "JPEG", quality=93)
    return out


def _slide_tabla(partidos: list[dict]) -> str:
    img  = _modern_bg()

    # Dimensiones de la tabla
    HDR_H   = 62    # altura cabecera de partido
    ROW_H   = 54    # altura fila de IA
    DIV_GAP = 22    # espacio entre partidos
    PAD     = 48    # padding interior de la card
    N_IA    = 4     # número de IAs

    n      = len(partidos)
    card_h = PAD * 2 + n * (HDR_H + N_IA * ROW_H) + max(0, n - 1) * DIV_GAP
    card_w = _W - 56
    card_x = 28
    _SAFE_TOP = 140
    _SAFE_BOT = 280
    _safe_h   = _H - _SAFE_TOP - _SAFE_BOT
    card_y    = _SAFE_TOP + max(0, (_safe_h - card_h) // 2)

    # Card oscura semi-transparente
    card_layer = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card_layer)
    cd.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=28, fill=(0, 0, 0, 215),
    )
    # Borde dorado sutil en la card
    cd.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=28, outline=(255, 200, 0, 70), width=2,
    )
    img = Image.alpha_composite(img.convert("RGBA"), card_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    f_hdr   = _font(44)
    f_label = _font(38)
    f_score = _font(54)

    y      = card_y + PAD
    _DOT_W      = 18
    _ARROW_BODY = 110
    _ARROW_HEAD = 10
    _GAP        = 14

    for i, p in enumerate(partidos):
        # Cabecera del partido (sin hora)
        hdr = f"{p['local'].upper()} VS {p['visitante'].upper()}"
        hw  = _tw(draw, hdr, f_hdr)
        draw.text(((_W - hw) // 2, y), hdr, font=f_hdr, fill=(255, 210, 0))
        y += HDR_H

        # Filas por IA — fila centrada con flecha de longitud fija
        for ai_name, score in [
            ("Claude",  p.get("claude",  "?-?")),
            ("ChatGPT", p.get("chatgpt", "?-?")),
            ("Gemini",  p.get("gemini",  "?-?")),
            ("Grok",    p.get("grok",    "?-?")),
        ]:
            color = _AI_COLORS.get(ai_name, (200, 200, 200))
            lw    = _tw(draw, ai_name, f_label)
            lh    = _th(draw, ai_name, f_label)
            sw    = _tw(draw, score,   f_score)
            sh    = _th(draw, score,   f_score)

            total_w = _DOT_W + lw + _GAP + _ARROW_BODY + _ARROW_HEAD + _GAP + sw
            rx      = (_W - total_w) // 2
            y_mid   = y + ROW_H // 2

            # Punto de color
            draw.ellipse([rx, y_mid - 5, rx + 10, y_mid + 5], fill=color)
            rx += _DOT_W

            # Nombre de la IA
            draw.text((rx, y_mid - lh // 2), ai_name, font=f_label, fill=color)
            rx += lw + _GAP

            # Flecha fija
            _arrow(draw, rx, y_mid, color=(95, 95, 95), length=_ARROW_BODY, head=_ARROW_HEAD)
            rx += _ARROW_BODY + _ARROW_HEAD + _GAP

            # Marcador con stroke
            sy = y_mid - sh // 2
            for dx in (-2, 0, 2):
                for dy in (-2, 0, 2):
                    if dx or dy:
                        draw.text((rx + dx, sy + dy), score, font=f_score, fill=(0, 0, 0))
            draw.text((rx, sy), score, font=f_score, fill=(255, 255, 255))

            y += ROW_H

        # Divisor entre partidos
        if i < n - 1:
            div_y = y + DIV_GAP // 2
            draw.line([card_x + 46, div_y, card_x + card_w - 46, div_y], fill=(55, 55, 55), width=1)
            y += DIV_GAP

    out = tempfile.mktemp(suffix="_pron_tabla.jpg")
    img.save(out, "JPEG", quality=93)
    return out


def _slide_cta(cta: str) -> str:
    img  = _modern_bg()
    draw = ImageDraw.Draw(img)

    MAX_W   = _W - 120   # margen de 60px a cada lado
    lineas  = [l for l in cta.split("\n") if l.strip()]
    fuentes = []
    for i, l in enumerate(lineas):
        start = 78 if i == 0 else 60
        fuentes.append(_fit_font(draw, l, start, MAX_W))

    gap     = 22
    alturas = [_th(draw, l, f) for l, f in zip(lineas, fuentes)]
    total_h = sum(alturas) + gap * (len(lineas) - 1)
    y       = (_H - total_h) // 2

    for i, (linea, f) in enumerate(zip(lineas, fuentes)):
        fill = (255, 210, 0) if i == 0 else (255, 255, 255)
        h    = _cx(draw, linea, f, y, fill=fill, stroke_fill=(0, 0, 0), stroke=4)
        y   += h + gap

    out = tempfile.mktemp(suffix="_pron_cta.jpg")
    img.save(out, "JPEG", quality=93)
    return out


# ── FFmpeg ───────────────────────────────────────────────────────────────────────

def _img_a_clip(img_path: str, salida: str, dur: float) -> tuple[bool, str]:
    cmd = [
        FFMPEG, "-y", "-loop", "1", "-framerate", str(_FPS), "-i", img_path,
        "-vf", (f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
                f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"),
        "-t", str(dur), "-r", str(_FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", "-pix_fmt", "yuv420p", salida,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    return (True, "") if r.returncode == 0 else (False, r.stderr.decode(errors="ignore")[-300:])


def _concat(clips: list[str], salida: str) -> tuple[bool, str]:
    lista = tempfile.mktemp(suffix="_concat_pron.txt")
    with open(lista, "w") as f:
        f.writelines(f"file '{c}'\n" for c in clips)
    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lista,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", "-pix_fmt", "yuv420p", salida,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    Path(lista).unlink(missing_ok=True)
    return (True, "") if r.returncode == 0 else (False, r.stderr.decode(errors="ignore")[-300:])


def _add_audio(vin: str, vout: str, musica: str | None, vol: float, dur: float) -> tuple[bool, str]:
    fade = max(0.0, dur - 1.5)
    if musica and Path(musica).exists():
        cmd = [
            FFMPEG, "-y", "-i", vin,
            "-stream_loop", "-1", "-i", musica,
            "-map", "0:v", "-map", "1:a",
            "-af", f"volume={vol},afade=t=out:st={fade}:d=1.5",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(dur), "-pix_fmt", "yuv420p", vout,
        ]
    else:
        cmd = [FFMPEG, "-y", "-i", vin, "-c:v", "copy", "-an", "-t", str(dur), vout]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    return (True, "") if r.returncode == 0 else (False, r.stderr.decode(errors="ignore")[-300:])


# ── ESPN API ─────────────────────────────────────────────────────────────────────

def obtener_partidos_hoy() -> list[dict]:
    """Partidos del Mundial 2026 pendientes de hoy (excluye finalizados)."""
    try:
        resp = requests.get(ESPN_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    partidos = []
    for event in data.get("events", []):
        comp        = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        status_type = comp.get("status", {}).get("type", {})
        if status_type.get("completed", False) or status_type.get("name", "") in (
            "STATUS_FINAL", "STATUS_FULL_TIME", "STATUS_POSTPONED", "STATUS_CANCELED",
            "STATUS_IN_PROGRESS", "STATUS_HALFTIME", "STATUS_END_PERIOD",
        ):
            continue

        hora = ""
        fecha_str = event.get("date", "")
        if fecha_str:
            try:
                dt   = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
                hora = dt.astimezone(_TZ_ARG).strftime("%H:%M")
            except Exception:
                pass

        partidos.append({
            "local":     home.get("team", {}).get("displayName", "?"),
            "visitante": away.get("team", {}).get("displayName", "?"),
            "hora":      hora,
        })

    return partidos


# ── Generación con Claude ─────────────────────────────────────────────────────────

def generar_pronosticos_claude(partidos: list[dict]) -> dict:
    """
    Claude genera pronósticos simulando 4 IAs (Claude, ChatGPT, Gemini, Grok).
    Retorna: {partidos, cta, titulo, hashtags, texto (preview para Telegram)}.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en .env")
    if not partidos:
        raise ValueError("No hay partidos pendientes hoy en el Mundial 2026.")

    fecha_hoy     = datetime.now(_TZ_ARG).strftime("%d/%m/%Y")
    fecha_corta   = datetime.now(_TZ_ARG).strftime("%d %b %Y").upper()  # "21 JUN 2026"
    partidos_usar = partidos[:4]

    lista = "\n".join(
        f"- {p['local']} vs {p['visitante']}"
        for p in partidos_usar
    )

    # Equipos únicos para título y hashtags dinámicos
    equipos = []
    for p in partidos_usar:
        for eq in (p["local"], p["visitante"]):
            tag = "#" + eq.replace(" ", "").replace("-", "")
            if tag not in equipos:
                equipos.append(tag)
    equipos_str = " vs ".join(f"{p['local']} - {p['visitante']}" for p in partidos_usar)

    ejemplo = json.dumps([
        {
            "local": p["local"], "visitante": p["visitante"],
            "claude": "2-1", "chatgpt": "1-1", "gemini": "0-2", "grok": "2-0",
        }
        for p in partidos_usar
    ], ensure_ascii=False)

    # Día de semana para rotar el CTA (evita que siempre sea el mismo)
    dia_semana = datetime.now(_TZ_ARG).weekday()  # 0=lunes … 6=domingo
    ctas_base = [
        "¿CUÁL IA\\nACERTÓ MÁS?\\nCOMENTÁ ABAJO",
        "¿CON CUÁL\\nPONÉS PLATA?\\nDEJÁ TU PRONÓSTICO",
        "¿CUÁL IA\\nES LA MÁS LISTA?\\nVOTÁ EN COMENTARIOS",
        "¿QUIÉN ACERTÓ?\\nDEJÁ TU APUESTA\\nEN LOS COMENTARIOS",
        "¿LA IA SABE\\nMÁS QUE VOS?\\nDEMOSTRÁLO ABAJO",
        "¿CUÁL IA\\nCONFIARÍAS PARA\\nAPOSTAR HOY?",
        "¿COINCIDÍS\\nCON ALGUNA IA?\\nCOMENTÁ TU PRONÓSTICO",
    ]
    cta_ejemplo = ctas_base[dia_semana % len(ctas_base)]

    prompt = (
        f"Hoy {fecha_hoy} se juegan estos partidos del Mundial 2026:\n{lista}\n\n"
        "Generá pronósticos para cada partido simulando 4 IAs con estilos MUY diferentes:\n"
        "- claude: analítico y estadístico, favorece al equipo con mejor ranking FIFA, marcadores ajustados\n"
        "- chatgpt: conservador y defensivo, prefiere empates o diferencias mínimas (0-0, 1-1, 1-0)\n"
        "- gemini: creativo e impredecible, apuesta por el underdog o resultados sorpresivos\n"
        "- grok: irreverente y provocador, elige el resultado más inesperado posible\n\n"
        "REGLAS:\n"
        "- OBLIGATORIO: los 4 pronósticos deben diferir entre sí. Al menos 3 resultados distintos por partido.\n"
        "- Marcadores exactos solo: '2-1', '0-0', '3-2' (sin espacios alrededor del guion)\n"
        "- CTA: pregunta viral de 3 líneas en MAYUSCULAS, sin emojis, que genere comentarios y debate. "
        f"Usa este estilo como inspiración pero SÉ CREATIVO y varía: '{cta_ejemplo}'\n\n"
        "Respondé SOLO con JSON válido sin markdown:\n"
        "{\n"
        f'  "partidos": {ejemplo},\n'
        f'  "cta": "{cta_ejemplo}",\n'
        f'  "titulo": "⚽ PRONÓSTICOS IA | {equipos_str} | Mundial 2026 | {fecha_corta}",\n'
        f'  "hashtags": ["#Shorts","#Mundial2026","#WorldCup2026","#Pronosticos","#IA","#InteligenciaArtificial","#futbol","#fyp","#parati","#Grok"'
        + "".join(f',"{t}"' for t in equipos[:4])
        + ']\n'
        "}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    resp   = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1100,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    datos = json.loads(raw.strip())

    # Preview de texto para Telegram
    lineas = [f"🔮 Predicciones IA — {fecha_hoy}\n"]
    for p in datos.get("partidos", []):
        lineas.append(f"⚽ {p['local']} vs {p['visitante']}")
        lineas.append(f"  🤖 Claude:   {p.get('claude','?-?')}")
        lineas.append(f"  💬 ChatGPT:  {p.get('chatgpt','?-?')}")
        lineas.append(f"  🧠 Gemini:   {p.get('gemini','?-?')}")
        lineas.append(f"  ⚡ Grok:     {p.get('grok','?-?')}")
        lineas.append("")
    datos["texto"] = "\n".join(lineas).strip()

    return datos


# ── Video principal ───────────────────────────────────────────────────────────────

def crear_video_pronosticos(
    datos: dict,
    output: str,
    musica: str | None = None,
    volumen: float = 0.18,
) -> tuple[bool, str]:
    """
    Crea el video: Slide 1 título | Slide 2 tabla | Slide 3 CTA.
    Fondo moderno oscuro con grilla geométrica.
    """
    partidos = datos.get("partidos", [])
    cta      = datos.get("cta", "Y VOS,\n¿CON QUE IA\nCOINCIDIS?\nCOMENTA ABAJO")
    fecha    = datetime.now(_TZ_ARG).strftime("%d/%m/%Y")

    if not partidos:
        return False, "No hay partidos para generar el video."

    carpeta  = Path(output).parent
    tmp_imgs: list[str] = []
    clips:    list[str] = []

    try:
        # Slide 1 — título
        img1 = _slide_titulo(fecha, len(partidos))
        tmp_imgs.append(img1)
        c1 = str(carpeta / "pron_c1.mp4")
        ok, err = _img_a_clip(img1, c1, _SECS_TITULO)
        if not ok:
            return False, f"Error clip título: {err}"
        clips.append(c1)

        # Slide 2 — tabla (duración dinámica según número de partidos)
        secs_tabla = _secs_tabla(len(partidos))
        img2 = _slide_tabla(partidos)
        tmp_imgs.append(img2)
        c2 = str(carpeta / "pron_c2.mp4")
        ok, err = _img_a_clip(img2, c2, secs_tabla)
        if not ok:
            return False, f"Error clip tabla: {err}"
        clips.append(c2)

        # Slide 3 — CTA
        img3 = _slide_cta(cta)
        tmp_imgs.append(img3)
        c3 = str(carpeta / "pron_c3.mp4")
        ok, err = _img_a_clip(img3, c3, _SECS_CTA)
        if not ok:
            return False, f"Error clip CTA: {err}"
        clips.append(c3)

        # Concatenar
        slideshow = str(carpeta / "pron_slides.mp4")
        ok, err   = _concat(clips, slideshow)
        if not ok:
            return False, f"Error concat: {err}"

        # Audio
        total_dur = _SECS_TITULO + secs_tabla + _SECS_CTA
        ok, err   = _add_audio(slideshow, output, musica, volumen, total_dur)
        Path(slideshow).unlink(missing_ok=True)
        if not ok:
            return False, f"Error audio: {err}"

        if not Path(output).exists():
            return False, "El video no se generó."
        return True, ""

    finally:
        for p in tmp_imgs:
            Path(p).unlink(missing_ok=True)
