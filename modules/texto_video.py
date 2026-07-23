"""
Módulo — Generador de Videos de Texto (Text-on-Video Shorts)
Crea Shorts con texto generado por IA superpuesto sobre un video de fondo neutro.
"""

import json
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

import anthropic
from PIL import Image, ImageDraw, ImageFont
from modules.config import SECS_POR_PAGINA_TEXTO
from modules.marca_agua import aplicar_marca_de_agua

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# Tipos de contenido que Claude puede generar
TIPOS_TEXTO: dict[str, str] = {
    "📖 Historia con giro":  "historia",
    "🤯 Dato curioso":        "curiosidad",
    "📋 Lista Top 5":         "lista",
    "😳 Situación incómoda":  "confesion",
    "🧠 ¿Sabías que...?":     "sabiasque",
}

# Fondos locales — generados con FFmpeg, sin descargas
FONDOS: dict[str, dict] = {
    "⬛ Negro":          {"color": "0x080808", "vignette": False},
    "🌌 Azul noche":    {"color": "0x05050f", "vignette": True},
    "🔴 Rojo oscuro":   {"color": "0x120303", "vignette": True},
    "🟢 Verde bosque":  {"color": "0x030f05", "vignette": True},
    "🟤 Café cálido":   {"color": "0x100804", "vignette": True},
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_font() -> str | None:
    candidates = [
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _escape_drawtext(text: str) -> str:
    """Escapa caracteres especiales para el filtro drawtext de FFmpeg."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "’")   # comilla tipográfica — evita romper el filtro
    text = text.replace(":", "\\:")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("%", "\\%")
    return text


def _paginate(texto: str, chars_per_line: int = 26, lines_per_page: int = 5) -> list[str]:
    """Divide el texto en páginas que caben en una pantalla de 1080x1920."""
    paragraphs = [p.strip() for p in texto.split("\n") if p.strip()]
    all_lines: list[str] = []
    for para in paragraphs:
        all_lines.extend(textwrap.wrap(para, width=chars_per_line))
        all_lines.append("")  # separador entre párrafos

    # Quitar blancos del final
    while all_lines and not all_lines[-1]:
        all_lines.pop()

    pages: list[str] = []
    current: list[str] = []
    content_count = 0
    for line in all_lines:
        current.append(line)
        if line:
            content_count += 1
        if content_count >= lines_per_page:
            pages.append("\n".join(current).strip())
            current = []
            content_count = 0

    if any(l for l in current):
        pages.append("\n".join(current).strip())

    return [p for p in pages if p.strip()]


# ── Renderizado PIL (sin drawtext — funciona con cualquier FFmpeg) ──────────────

_IMG_W = 1080
_IMG_H = 1920
_FPS   = 30


def _load_font_texto(size: int):
    path = _find_font()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _texto_stroke(draw, pos, text, font, fill, stroke_fill, stroke=4, spacing=14):
    """Dibuja texto con stroke (contorno) usando PIL."""
    x, y = pos
    for dx in range(-stroke, stroke + 1):
        for dy in range(-stroke, stroke + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font,
                          fill=stroke_fill, spacing=spacing)
    draw.text((x, y), text, font=font, fill=fill, spacing=spacing)


def _crear_frame_pagina(
    estilo_key: str,
    texto_pagina: str,
    titulo: str,
    num_pagina: int,
    total_paginas: int,
) -> str:
    """Crea un PNG con fondo de color + texto de la página."""
    cfg  = FONDOS.get(estilo_key, list(FONDOS.values())[0])
    # Convertir hex "0xRRGGBB" a tuple RGB
    hex_color = cfg["color"].replace("0x", "")
    bg_color  = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    img  = Image.new("RGB", (_IMG_W, _IMG_H), bg_color)
    draw = ImageDraw.Draw(img)

    # Vignette: degradado oscuro en los bordes si está habilitado
    if cfg["vignette"]:
        overlay = Image.new("RGBA", (_IMG_W, _IMG_H), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        steps   = 80
        for i in range(steps):
            alpha = int(160 * (1 - i / steps) ** 2)
            m     = i * 6
            ov_draw.rectangle([m, m, _IMG_W - m, _IMG_H - m],
                               outline=(0, 0, 0, alpha), width=6)
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

    # Título arriba (solo en la primera página)
    if titulo and num_pagina == 1:
        font_t  = _load_font_texto(44)
        lineas  = textwrap.wrap(titulo[:60], width=22)
        texto_t = "\n".join(lineas)
        bbox    = draw.textbbox((0, 0), texto_t, font=font_t, spacing=10)
        tw, th  = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx      = (_IMG_W - tw) // 2
        ty      = 80
        pad_t   = 18
        draw.rounded_rectangle(
            [tx - pad_t, ty - pad_t, tx + tw + pad_t, ty + th + pad_t],
            radius=14, fill=(0, 0, 0, 180),
        )
        _texto_stroke(draw, (tx, ty), texto_t, font_t,
                      fill=(255, 220, 0), stroke_fill=(0, 0, 0), stroke=3, spacing=10)

    # Texto principal centrado
    font_m  = _load_font_texto(68)
    lineas  = [l for l in texto_pagina.split("\n") if l.strip()]
    texto_c = "\n".join(lineas)
    bbox    = draw.textbbox((0, 0), texto_c, font=font_m, spacing=16)
    tw, th  = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx      = (_IMG_W - tw) // 2
    cy      = (_IMG_H - th) // 2
    pad     = 36
    draw.rounded_rectangle(
        [cx - pad, cy - pad, cx + tw + pad, cy + th + pad],
        radius=22, fill=(0, 0, 0, 200),
    )
    _texto_stroke(draw, (cx, cy), texto_c, font_m,
                  fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke=5, spacing=16)

    # Contador de páginas abajo
    if total_paginas > 1:
        font_s  = _load_font_texto(34)
        counter = f"{num_pagina} / {total_paginas}"
        bbox_s  = draw.textbbox((0, 0), counter, font=font_s)
        sw      = bbox_s[2] - bbox_s[0]
        sx      = (_IMG_W - sw) // 2
        sy      = _IMG_H - 100
        draw.text((sx, sy), counter, font=font_s, fill=(160, 160, 160))

    path = tempfile.mktemp(suffix=f"_txt_p{num_pagina}.jpg")
    img.save(path, "JPEG", quality=92)
    return path


def _imagen_a_clip_texto(img_path: str, salida: str, duracion: float) -> tuple[bool, str]:
    """Imagen estática → clip H264 1080×1920 a 30fps."""
    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-framerate", str(_FPS), "-i", img_path,
        "-vf", f"scale={_IMG_W}:{_IMG_H}:force_original_aspect_ratio=decrease,"
               f"pad={_IMG_W}:{_IMG_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
        "-t", str(duracion), "-r", str(_FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", "-pix_fmt", "yuv420p",
        salida,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0:
        return False, r.stderr.decode(errors="ignore")[-400:]
    return True, ""


def _concat_clips_texto(clips: list[str], salida: str) -> tuple[bool, str]:
    """Concatena clips con FFmpeg concat demuxer."""
    lista = tempfile.mktemp(suffix="_concat.txt")
    with open(lista, "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", lista,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-an", "-pix_fmt", "yuv420p",
        salida,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    Path(lista).unlink(missing_ok=True)
    if r.returncode != 0:
        return False, r.stderr.decode(errors="ignore")[-400:]
    return True, ""


def _agregar_audio_texto(
    video_in: str, video_out: str,
    musica: str | None, volumen: float, total_dur: float,
) -> tuple[bool, str]:
    fade_start = max(0, total_dur - 1.5)
    if musica and Path(musica).exists():
        cmd = [
            FFMPEG, "-y", "-i", video_in,
            "-stream_loop", "-1", "-i", musica,
            "-map", "0:v", "-map", "1:a",
            "-af", f"volume={volumen},afade=t=out:st={fade_start}:d=1.5",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(total_dur), "-pix_fmt", "yuv420p",
            video_out,
        ]
    else:
        cmd = [
            FFMPEG, "-y", "-i", video_in,
            "-c:v", "copy", "-an",
            "-t", str(total_dur),
            video_out,
        ]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        return False, r.stderr.decode(errors="ignore")[-400:]
    return True, ""


# ── Función pública: generación de fondo (devuelve estilo_key para usar en crear_video_texto) ──

def generar_fondo_local(estilo_key: str, salida: str = "", duracion: float = 0) -> bool:
    """
    Valida que el estilo existe. El fondo real se genera frame a frame en crear_video_texto.
    Retorna True si el estilo es válido.
    """
    return estilo_key in FONDOS


# ── Pipeline principal ─────────────────────────────────────────────────────────

def crear_video_texto(
    texto: str,
    estilo_key: str,           # clave de FONDOS (color/estilo de fondo)
    output: str,
    titulo_display: str = "",
    musica: str | None = None,
    volumen_musica: float = 0.18,
    secs_por_pagina: float = SECS_POR_PAGINA_TEXTO,
) -> tuple[bool, str]:
    """
    Crea un Short con texto paginado sobre fondo de color (PIL, sin drawtext).
    Retorna (ok, mensaje_error).
    """
    pages = _paginate(texto)
    if not pages:
        return False, "El texto está vacío o no se pudo procesar."

    total_dur = round(len(pages) * secs_por_pagina, 2)
    carpeta   = Path(output).parent
    tmp_imgs: list[str] = []
    clips:    list[str] = []

    for i, page in enumerate(pages):
        img_path  = _crear_frame_pagina(estilo_key, page, titulo_display, i + 1, len(pages))
        tmp_imgs.append(img_path)
        clip_path = str(carpeta / f"txt_clip_{i:02d}.mp4")
        ok, err   = _imagen_a_clip_texto(img_path, clip_path, secs_por_pagina)
        if not ok:
            for p in tmp_imgs:
                Path(p).unlink(missing_ok=True)
            return False, f"Error clip {i+1}: {err}"
        clips.append(clip_path)

    for p in tmp_imgs:
        Path(p).unlink(missing_ok=True)

    # Concatenar clips
    slideshow = str(carpeta / "txt_slideshow.mp4")
    if len(clips) == 1:
        import shutil
        shutil.copy(clips[0], slideshow)
    else:
        ok, err = _concat_clips_texto(clips, slideshow)
        if not ok:
            return False, f"Error concat: {err}"

    # Agregar audio
    ok, err = _agregar_audio_texto(slideshow, output, musica, volumen_musica, total_dur)
    Path(slideshow).unlink(missing_ok=True)
    if not ok:
        return False, f"Error audio: {err}"

    if not Path(output).exists():
        return False, "El video no se generó."
    aplicar_marca_de_agua(output)
    return True, ""


# ── Generación de texto con Claude ─────────────────────────────────────────────

_PROMPTS: dict[str, str] = {
    "historia": (
        "Escribí una historia corta para YouTube Shorts. Tema: {tema}\n\n"
        "ESTRUCTURA VIRAL OBLIGATORIA:\n"
        "• Primera oración: el momento MÁS intenso de la historia, sin setup previo. "
        "El lector tiene que necesitar saber qué pasó antes.\n"
        "• Desarrollo: 3-4 oraciones que revelan el contexto y suben la tensión\n"
        "• Giro final: las últimas 2 oraciones cambian cómo se lee todo lo anterior\n\n"
        "TONO: primera persona, coloquial latinoamericano. Como contándoselo a un amigo.\n"
        "El giro tiene que ser genuino — que el lector quiera comentar '¿qué?' o 'no me lo esperaba'.\n"
        "LONGITUD: 240-280 palabras. Sin títulos ni subtítulos."
    ),
    "curiosidad": (
        "Escribí un dato curioso impactante para YouTube Shorts. Tema: {tema}\n\n"
        "ESTRUCTURA:\n"
        "• Primera oración: el dato más impactante como revelación. "
        "Algo que genere la reacción 'esto no puede ser verdad'.\n"
        "• Medio: explicá POR QUÉ es así con analogías cotidianas. Nada académico.\n"
        "• Cierre: conectá con la vida diaria. Que terminen con ganas de contárselo a alguien.\n\n"
        "TONO: asombro genuino. 'Esto no te lo enseñan en el colegio.'\n"
        "Incluí un número o estadística concreta si es posible.\n"
        "LONGITUD: 240-280 palabras. Sin títulos."
    ),
    "lista": (
        "Escribí una lista Top 5 para YouTube Shorts. Tema: {tema}\n\n"
        "REGLAS:\n"
        "• Exactamente 5 ítems numerados\n"
        "• Orden ASCENDENTE de impacto: el 1 interesa, el 5 incomoda o impacta\n"
        "• Cada ítem: 1-2 oraciones directas, sin relleno\n"
        "• El ítem 5 tiene que ser el que haga decir 'espera, eso es verdad' o 'no sabía esto'\n"
        "• Sin ítem de intro. Arrancá directamente con '1.'\n\n"
        "TONO: un amigo que sabe cosas que vos no sabés. Coloquial LATAM.\n"
        "LONGITUD: 240-280 palabras."
    ),
    "confesion": (
        "Escribí una situación incómoda y relatable para YouTube Shorts. Tema: {tema}\n\n"
        "OBJETIVO: que quien lee comente 'yo igual 💀' o 'me pasó exactamente eso'.\n\n"
        "ESTRUCTURA:\n"
        "• Arrancá in medias res en la situación más incómoda. "
        "Sin setup: no 'resulta que estaba en...', sí 'Estaba en medio de una reunión cuando me di cuenta...'\n"
        "• Desarrollá el momento con detalles específicos que lo hagan universal\n"
        "• Cierre: reflexión corta o remate con humor negro. Que invite a comentar.\n\n"
        "TONO: primera persona, humor seco y autoconsciente. Ni dramático ni trivial.\n"
        "LONGITUD: 240-280 palabras."
    ),
    "sabiasque": (
        "Escribí un '¿Sabías que...?' para YouTube Shorts. Tema: {tema}\n\n"
        "REGLAS:\n"
        "• Primera oración: '¿Sabías que [hecho impactante y real]?'\n"
        "• El hecho tiene que ser verificable — nada inventado\n"
        "• Explicá el mecanismo con una analogía simple ('es como cuando...')\n"
        "• Incluí un número o estadística concreta\n"
        "• Cerrá con algo que el lector pueda aplicar hoy o que quiera compartir\n\n"
        "TONO: curioso, no académico. Un profesor que explica bien.\n"
        "LONGITUD: 240-280 palabras."
    ),
}


def generar_texto_claude(tipo: str, tema: str) -> dict:
    """
    Genera el texto + título + hashtags para el video usando Claude.
    Retorna dict: {texto, titulo, hashtags}
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en .env")

    base = _PROMPTS.get(tipo, _PROMPTS["curiosidad"]).format(tema=tema or "cualquier tema interesante")
    prompt = (
        base + "\n\n"
        "TÍTULO DEL VIDEO: máx 60 caracteres. Crea curiosidad sin revelar el giro ni la respuesta. "
        "Un emoji al final. Evitá títulos genéricos como '5 datos increíbles' — sé específico.\n\n"
        "HASHTAGS YouTube Shorts LATAM: siempre #Shorts #fyp #parati + 3-4 en español "
        "específicos al contenido + #argentina si el tema es local. Total 6-8.\n\n"
        "Respondé SOLO con JSON válido (sin markdown ni texto extra):\n"
        '{"texto": "...el texto completo...", '
        '"titulo": "titulo especifico max 60 chars con emoji", '
        '"hashtags": ["#Shorts","#fyp","#parati","#tag4","#tag5","#tag6"]}'
    )

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
