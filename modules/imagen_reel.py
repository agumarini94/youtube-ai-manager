"""
Módulo — Reel de Imágenes con Pregunta Viral
Crea un Short de ~23s con 6 imágenes libres + pregunta viral generada por Claude.

Texto superpuesto con Pillow (no requiere drawtext en FFmpeg).
"""

import json
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

import anthropic
import requests
from PIL import Image, ImageDraw, ImageFont

FFMPEG  = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

_IMG_W        = 1080
_IMG_H        = 1920
_FPS          = 30
_SECS_POR_IMG = 2.5
_N_IMAGENES   = 6
_XFADE_DUR    = 0.8
_CARD_DUR     = 4.0

# 7 clips × 4s − 6 transiciones × 0.8s = 23.2s
_TOTAL_DUR  = (_N_IMAGENES + 1) * _SECS_POR_IMG - _N_IMAGENES * _XFADE_DUR
_CARD_START = _TOTAL_DUR - _CARD_DUR   # 19.2s


# ── Helpers FFmpeg ─────────────────────────────────────────────────────────────

def _run(cmd: list, timeout: int = 180) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    stderr = r.stderr.decode(errors="ignore")
    ok = r.returncode == 0 and Path(cmd[-1]).exists()
    return ok, stderr


# ── Helpers Pillow ─────────────────────────────────────────────────────────────

def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _texto_con_stroke(draw: ImageDraw.ImageDraw, pos: tuple, texto: str,
                      font, fill: tuple, stroke_fill: tuple, stroke: int, spacing: int = 10):
    """Dibuja texto con contorno grueso (efecto thumbnail)."""
    x, y = pos
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), texto, font=font,
                          fill=stroke_fill, spacing=spacing)
    draw.text((x, y), texto, font=font, fill=fill, spacing=spacing)


def _crear_overlay_pregunta(pregunta: str) -> str:
    """
    Crea PNG RGBA con la pregunta GRANDE centrada verticalmente (estilo portada).
    Se superpone durante todo el video sobre las imágenes.
    """
    img  = Image.new("RGBA", (_IMG_W, _IMG_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(88)

    lineas = textwrap.wrap(pregunta.upper(), width=14)
    texto  = "\n".join(lineas)

    bbox   = draw.textbbox((0, 0), texto, font=font, spacing=18)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    x   = (_IMG_W - tw) // 2
    y   = int(_IMG_H * 0.18)
    pad = 35

    draw.rounded_rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad],
        radius=24,
        fill=(0, 0, 0, 200),
    )

    _texto_con_stroke(
        draw, (x, y), texto, font,
        fill=(255, 255, 255, 255),
        stroke_fill=(0, 0, 0, 255),
        stroke=6,
        spacing=18,
    )

    path = tempfile.mktemp(suffix="_overlay.png")
    img.save(path)
    return path


def _crear_imagen_tarjeta(pregunta: str) -> str:
    """
    Tarjeta final: fondo muy oscuro + pregunta enorme centrada + CTA.
    """
    img  = Image.new("RGB", (_IMG_W, _IMG_H), (8, 8, 18))
    draw = ImageDraw.Draw(img)

    font_big = _load_font(96)
    lineas   = textwrap.wrap(pregunta.upper(), width=13)
    texto    = "\n".join(lineas)

    bbox   = draw.textbbox((0, 0), texto, font=font_big, spacing=20)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (_IMG_W - tw) // 2
    y = (_IMG_H - th) // 2 - 80

    pad = 40
    draw.rounded_rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad],
        radius=28,
        fill=(20, 20, 40),
    )

    _texto_con_stroke(
        draw, (x, y), texto, font_big,
        fill=(255, 255, 255, 255),
        stroke_fill=(0, 0, 0, 255),
        stroke=6,
        spacing=20,
    )

    # CTA debajo — "💬 Comentá tu respuesta"
    font_cta = _load_font(48)
    cta      = "Comentá tu respuesta 👇"
    bbox_c   = draw.textbbox((0, 0), cta, font=font_cta)
    cw       = bbox_c[2] - bbox_c[0]
    cx       = (_IMG_W - cw) // 2
    cy       = y + th + pad + 50
    _texto_con_stroke(
        draw, (cx, cy), cta, font_cta,
        fill=(255, 255, 255, 255),
        stroke_fill=(0, 0, 0, 255),
        stroke=3,
        spacing=0,
    )

    path = tempfile.mktemp(suffix="_card.png")
    img.save(path)
    return path


# ── Descarga de imágenes ───────────────────────────────────────────────────────

def _descargar_pexels(termino: str, carpeta: str, idx: int) -> str | None:
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": termino, "per_page": 3, "orientation": "portrait"},
            timeout=15,
        )
        photos = resp.json().get("photos", [])
        if not photos:
            return None
        url  = photos[0]["src"]["large2x"]
        dest = os.path.join(carpeta, f"img_{idx:02d}.jpg")
        r    = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        return dest
    except Exception:
        return None


def _descargar_pixabay(termino: str, carpeta: str, idx: int) -> str | None:
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        return None
    try:
        resp = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": api_key, "q": termino,
                "image_type": "photo", "orientation": "vertical",
                "per_page": 3, "safesearch": "true",
            },
            timeout=15,
        )
        hits = resp.json().get("hits", [])
        if not hits:
            return None
        url  = hits[0]["largeImageURL"]
        dest = os.path.join(carpeta, f"img_{idx:02d}.jpg")
        r    = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        return dest
    except Exception:
        return None


def descargar_imagenes(terminos: list[str], carpeta: str) -> list[str]:
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    rutas: list[str] = []
    for idx, termino in enumerate(terminos[:_N_IMAGENES]):
        ruta = _descargar_pexels(termino, carpeta, idx)
        if not ruta:
            ruta = _descargar_pixabay(termino, carpeta, idx)
        if ruta and Path(ruta).exists():
            rutas.append(ruta)
    return rutas


# ── Clips individuales ─────────────────────────────────────────────────────────

def _imagen_a_clip(img_path: str, salida: str, duracion: float) -> tuple[bool, str]:
    """Imagen estática (JPG/PNG) → clip H264 1080×1920 a 30fps."""
    vf = (
        f"scale={_IMG_W}:{_IMG_H}:force_original_aspect_ratio=increase,"
        f"crop={_IMG_W}:{_IMG_H},"
        f"setsar=1"
    )
    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-framerate", str(_FPS),
        "-i", img_path,
        "-vf", vf,
        "-t", str(duracion),
        "-r", str(_FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", "-pix_fmt", "yuv420p",
        salida,
    ]
    return _run(cmd, timeout=120)


# ── Concatenación xfade ────────────────────────────────────────────────────────

def _concatenar_con_xfade(clips: list[str], salida: str) -> tuple[bool, str]:
    n = len(clips)
    if n < 2:
        return False, "Se necesitan al menos 2 clips."

    cmd = [FFMPEG, "-y"]
    for c in clips:
        cmd += ["-i", c]

    filters = []
    prev    = "0:v"
    offset  = 0.0
    for i in range(1, n):
        label  = f"vx{i}"
        offset = round(offset + _SECS_POR_IMG - _XFADE_DUR, 3)
        filters.append(
            f"[{prev}][{i}:v]xfade=transition=fade"
            f":duration={_XFADE_DUR}:offset={offset}[{label}]"
        )
        prev = label

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{prev}]",
        "-r", str(_FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", "-pix_fmt", "yuv420p",
        salida,
    ]
    return _run(cmd, timeout=300)


# ── Overlay de texto con Pillow + FFmpeg overlay ───────────────────────────────

def _agregar_overlay_y_audio(
    video_in: str,
    overlay_png: str,
    video_out: str,
    musica: str | None = None,
    volumen_musica: float = 0.18,
) -> tuple[bool, str]:
    """
    Superpone el PNG (RGBA) sobre el video y mezcla música opcional.
    Usa FFmpeg overlay filter (no requiere drawtext/freetype).
    """
    total      = round(_TOTAL_DUR, 2)
    fade_start = max(0, total - 1.5)

    cmd = [FFMPEG, "-y", "-i", video_in, "-loop", "1", "-i", overlay_png]

    if musica and Path(musica).exists():
        cmd += ["-stream_loop", "-1", "-i", musica]
        filter_complex = (
            "[0:v][1:v]overlay=0:0:format=auto[vout]"
        )
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "2:a",
            "-af", f"volume={volumen_musica},afade=t=out:st={fade_start}:d=1.5",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(total), "-pix_fmt", "yuv420p",
            video_out,
        ]
    else:
        filter_complex = "[0:v][1:v]overlay=0:0:format=auto[vout]"
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-t", str(total), "-pix_fmt", "yuv420p",
            video_out,
        ]

    return _run(cmd, timeout=300)


# ── Función principal ──────────────────────────────────────────────────────────

def crear_reel_imagenes(
    imagenes: list[str],
    pregunta: str,
    output: str,
    musica: str | None = None,
    volumen_musica: float = 0.18,
) -> tuple[bool, str]:
    if not imagenes:
        return False, "No hay imágenes para crear el reel."

    carpeta = Path(output).parent
    clips: list[str] = []

    # 1. Convertir cada imagen en clip
    for i, img in enumerate(imagenes[:_N_IMAGENES]):
        clip_path = str(carpeta / f"ir_clip_{i:02d}.mp4")
        ok, err = _imagen_a_clip(img, clip_path, _SECS_POR_IMG)
        if not ok:
            return False, f"Error convirtiendo imagen {i+1}:\n{err[-300:]}"
        clips.append(clip_path)

    # 2. Tarjeta final con pregunta grande (Pillow → PNG → clip)
    card_img_path = _crear_imagen_tarjeta(pregunta)
    card_clip = str(carpeta / "ir_card_final.mp4")
    ok, err = _imagen_a_clip(card_img_path, card_clip, _CARD_DUR)
    Path(card_img_path).unlink(missing_ok=True)
    if not ok:
        return False, f"Error creando tarjeta final:\n{err[-300:]}"
    clips.append(card_clip)

    # 3. Concatenar con xfade
    slideshow_path = str(carpeta / "ir_slideshow.mp4")
    ok, err = _concatenar_con_xfade(clips, slideshow_path)
    if not ok:
        return False, f"Error concatenando clips:\n{err[-300:]}"

    # 4. Overlay de pregunta pequeña en la parte superior + audio
    overlay_path = _crear_overlay_pregunta(pregunta)
    ok, err = _agregar_overlay_y_audio(
        slideshow_path, overlay_path, output, musica, volumen_musica
    )
    Path(overlay_path).unlink(missing_ok=True)
    if not ok:
        return False, f"Error agregando overlay/audio:\n{err[-300:]}"

    return True, ""


# ── Generación de contenido con Claude ─────────────────────────────────────────

def generar_pregunta_claude(tema: str = "") -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en .env")

    tema_str = f"Tema guía: {tema}.\n\n" if tema else ""
    prompt = (
        "Sos el creador de preguntas de fútbol más viral de YouTube Shorts en LATAM. "
        "Tu especialidad: preguntas que dividen a los hinchas en dos bandos y los obligan a defender su posición en comentarios.\n\n"
        + tema_str +
        "PSICOLOGÍA VIRAL — tu pregunta debe activar UNO de estos disparadores:\n"
        "• Debate eterno sin respuesta clara: Messi vs Ronaldo, Argentina vs Brasil, etc.\n"
        "• Orgullo de hincha: ¿tu club, tu jugador favorito, tu selección?\n"
        "• Momento histórico polémico: la mano de Dios, el gol anulado, el penal que no fue\n"
        "• Elección imposible entre dos ídolos o épocas\n"
        "• Predicción sobre el Mundial, la Champions, el Balón de Oro\n\n"
        "ANATOMÍA DE UNA PREGUNTA QUE FUNCIONA:\n"
        "✅ Específica: '¿Messi 2014 o Messi 2022?', no '¿quién fue mejor?'\n"
        "✅ Debate real: ambas opciones tienen defensores acérrimos\n"
        "✅ Sin respuesta obvia: que la gente se pelee en los comentarios\n"
        "✅ Divide por generación, país o equipo — que genere bandos apasionados\n\n"
        "EJEMPLOS DEL NIVEL OBJETIVO (referencia de estructura y tono):\n"
        "• '¿Messi o Ronaldo, sin importar los títulos?'\n"
        "• '¿El gol de Maradona a Inglaterra fue trampa?'\n"
        "• '¿Argentina puede ganar el Mundial 2026?'\n"
        "• '¿Quién fue mejor: Ronaldo R9 o Messi?'\n"
        "• '¿La Champions o el Mundial, cuál pesa más?'\n"
        "• '¿Boca o River, quién tiene más historia?'\n\n"
        "PROHIBIDO:\n"
        "❌ Respuesta obvia para el 90% de los hinchas\n"
        "❌ Preguntas abstractas sin situación concreta\n"
        "❌ Política partidaria, religión, racismo\n"
        "❌ Más de 42 caracteres en la pregunta\n\n"
        "TÍTULO DEL VIDEO (diferente a la pregunta):\n"
        "Genera curiosidad sin revelar la respuesta. Máx 60 caracteres.\n"
        "Formatos: 'La pregunta que divide a todos los hinchas 🤔' / 'El debate eterno del fútbol 👇' / '[Tema]: ¿cuál es tu respuesta? 🤯'\n\n"
        "TÉRMINOS DE IMAGEN: 6 frases en inglés para buscar fotos REALES de fútbol "
        "que ilustren el debate. Buscá imágenes de jugadores, estadios, hinchas o momentos concretos "
        "(messi argentina goal celebration, ronaldo portugal trophy, world cup final crowd) — no stock genérico.\n\n"
        "HASHTAGS YouTube Shorts LATAM (nicho medio — evitar tags con >500M posts):\n"
        "Fijos: #Shorts #fyp #parati\n"
        "2 de engagement: #futbol #debate o #teamimonoamo o #comentatu\n"
        "1-2 temáticos específicos al debate futbolero — en español\n"
        "Total 6-7 hashtags. Sin inglés excepto los fijos.\n\n"
        "Respondé SOLO con JSON válido (sin markdown):\n"
        '{"pregunta": "¿...?", '
        '"titulo": "titulo del video max 60 chars", '
        '"terminos": ["term1","term2","term3","term4","term5","term6"], '
        '"hashtags": ["#Shorts","#fyp","#parati","#tag4","#tag5","#tag6"]}'
    )

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
