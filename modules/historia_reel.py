"""
Módulo — Reel de Historia de Atención al Público
Video con pregunta abierta + historia en lunfardo en la descripción de YouTube.

Flujo:
  1. Claude genera historia (lunfardo, primera persona) + pregunta abierta + términos imagen.
  2. Imágenes de Pexels/Pixabay del setting laboral elegido.
  3. Video: imágenes con pregunta abierta superpuesta + tarjeta final CTA.
  4. Descripción YouTube: historia completa (el contenido real).
"""

import json
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

import anthropic
from PIL import Image, ImageDraw

# Reutilizamos todo lo que ya funciona de imagen_reel
from modules.imagen_reel import (
    descargar_imagenes,
    _imagen_a_clip,
    _concatenar_con_xfade,
    _load_font,
    _texto_con_stroke,
    _CARD_DUR,
    _SECS_POR_IMG,
    _N_IMAGENES,
    _IMG_W,
    _IMG_H,
    FFMPEG,
)

_XFADE_DUR = 0.8  # debe coincidir con imagen_reel

# ── Settings disponibles ───────────────────────────────────────────────────────

SETTINGS: dict[str, dict] = {
    "🛒 Supermercado":  {
        "label_es": "supermercado",
        "terminos": ["supermarket cashier angry customer", "grocery store checkout line",
                     "retail worker frustrated", "shopping cart supermarket queue",
                     "cashier register people", "supermarket customer complaint"],
    },
    "💊 Farmacia": {
        "label_es": "farmacia",
        "terminos": ["pharmacy counter customer", "drugstore worker service",
                     "pharmacist customer interaction", "pharmacy waiting line",
                     "medicine store counter", "pharmacy retail worker"],
    },
    "🏦 Banco": {
        "label_es": "banco",
        "terminos": ["bank teller customer angry", "bank queue waiting people",
                     "bank counter service", "bank employee frustrated",
                     "banking customer complaint", "bank office interior"],
    },
    "🍔 Fast food / Resto": {
        "label_es": "fast food o restaurante",
        "terminos": ["fast food worker customer", "restaurant angry customer",
                     "food service employee", "restaurant complaint scene",
                     "burger joint counter", "food court customer service"],
    },
    "📞 Call center": {
        "label_es": "call center",
        "terminos": ["call center worker headset", "customer service phone operator",
                     "office worker stressed phone", "call center employees",
                     "helpdesk support agent", "phone customer service"],
    },
    "✈️ Aeropuerto / Hotel": {
        "label_es": "aeropuerto u hotel",
        "terminos": ["airport check-in counter", "hotel reception desk customer",
                     "airline worker passenger", "hotel lobby customer complaint",
                     "reception desk service", "airport gate agent"],
    },
    "🏥 Salud / Consultorio": {
        "label_es": "consultorio o clínica",
        "terminos": ["medical receptionist patient", "clinic waiting room",
                     "hospital reception desk", "doctor office angry patient",
                     "healthcare worker service", "medical office counter"],
    },
    "🏪 Otro comercio": {
        "label_es": "un comercio",
        "terminos": ["retail store customer complaint", "shop worker service",
                     "store counter customer angry", "retail employee frustrated",
                     "shopping customer interaction", "store clerk counter"],
    },
}


# ── Generación de contenido ────────────────────────────────────────────────────

def generar_historia_claude(setting_label: str) -> dict:
    """
    Claude genera historia en lunfardo + pregunta para video + SEO.
    Retorna dict: {historia, pregunta_video, titulo, hashtags}
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en .env")

    prompt = f"""Laburaste años en {setting_label}. Estás contando UNA historia específica en los comentarios de YouTube — no una reflexión general, un episodio concreto que te pasó y que todavía te da algo de bronca o de risa.

HOOK OBLIGATORIO (primera oración):
Arrancá IN MEDIAS RES, en el momento más tenso o absurdo. Sin setup, sin "resulta que". Que quien lee no pueda parar.
Hooks que funcionan:
- "Ese día le dije 'sí señor' mientras pensaba en cómo renunciar."
- "La clienta quería devolver algo que había usado tres meses."
- "Me pidió hablar con el gerente. Yo era el gerente."

ESTRUCTURA (4 párrafos cortos):
1. GANCHO: el momento cumbre, sin preámbulo. In medias res.
2. CONTEXTO: quién era este cliente (tipo específico, no "una persona"), qué día, qué ambiente
3. ESCALADA: cómo empeoró. Qué dijiste en voz alta vs qué pensabas. Lo que no podías decir.
4. CIERRE: remate con ironía, resignación digna o justicia poética discreta. Sin moraleja.

Terminá con: "Si laburaste en {setting_label} sabés exactamente de qué hablo. Contame la tuya 👇"

TONO:
- Adulto, cínico con humor seco, resignado pero con chispa
- Como contándoselo a un amigo en un bar a las 23hs
- Detalles específicos: "el tipo con ropa cara que quería que le cambiemos la política", no "el cliente difícil"
- Lunfardo adulto natural: "tipo raro", "la mina", "se puso en personaje", "a esta altura ya nada te sorprende", "qué bajón de existencia"
- 2-3 errores ortográficos naturales (q, sin tilde) — ni más, ni en cada oración

PROHIBIDO:
❌ MAYÚSCULAS para énfasis
❌ "!!!!" — más de un signo
❌ "me quería morir" / "no lo podía creer" / "la verdad que sí"
❌ Estructura de nene: "Sí, X. Con Y." / "Fue entonces cuando..."
❌ Moraleja final
❌ Más de 270 palabras

PREGUNTA PARA EL VIDEO:
Tiene que surgir de la historia — no genérica. Si la historia es sobre un cliente que pedía excepciones, no "¿Cuál fue tu peor cliente?" sino "¿Hasta dónde aguantarías antes de responder mal?"
Máx 42 caracteres. Cuanto más corta y directa, más impacto. Que quien laburó en ese lugar NECESITE responder.

TÍTULO DEL VIDEO:
Específico a esta historia. Crea curiosidad sin spoilear el remate.
Formatos: "Lo que le respondí me costó casi el trabajo" / "3 años en caja y esto todavía me molesta" / "El cliente más [adjetivo] que atendí en mi vida"
Máx 60 caracteres.

Respondé SOLO con JSON válido (sin markdown):
{{
  "historia": "...texto completo...",
  "pregunta_video": "¿...?",
  "titulo": "titulo especifico de esta historia",
  "hashtags": ["#Shorts", "#fyp", "#parati", "#trabajo", "#atencionalpublico", "#argentina", "#storytime"]
}}"""

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


# ── Helpers de texto por página ───────────────────────────────────────────────

def _paginar_historia(texto: str, chars_per_line: int = 21, lines_per_page: int = 5) -> list[str]:
    """Divide la historia en páginas que caben en un clip de 4 segundos."""
    paragraphs = [p.strip() for p in texto.split("\n") if p.strip()]
    all_lines: list[str] = []
    for para in paragraphs:
        all_lines.extend(textwrap.wrap(para, width=chars_per_line))
        all_lines.append("")
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


def _dibujar_bloque_texto(draw: ImageDraw.ImageDraw, texto: str, font, y_center: int,
                           fill=(255, 255, 255, 255), stroke_fill=(0, 0, 0, 255),
                           stroke: int = 4, spacing: int = 16, pad: int = 30) -> None:
    """Dibuja un bloque de texto centrado con fondo redondeado."""
    bbox = draw.textbbox((0, 0), texto, font=font, spacing=spacing)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (_IMG_W - tw) // 2
    y = y_center - th // 2
    draw.rounded_rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad],
        radius=20, fill=(0, 0, 0, 195),
    )
    _texto_con_stroke(draw, (x, y), texto, font,
                      fill=fill, stroke_fill=stroke_fill,
                      stroke=stroke, spacing=spacing)


def _componer_clip_pregunta(img_path: str, pregunta_video: str) -> str:
    """Imagen 1: pregunta grande + flecha 'Seguí leyendo'."""
    base = Image.open(img_path).convert("RGBA")
    base = base.resize((_IMG_W, _IMG_H), Image.LANCZOS)
    overlay = Image.new("RGBA", (_IMG_W, _IMG_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Pregunta — tercio superior
    font_q = _load_font(82)
    lineas_q = textwrap.wrap(pregunta_video.upper(), width=13)
    texto_q = "\n".join(lineas_q)
    _dibujar_bloque_texto(draw, texto_q, font_q, y_center=int(_IMG_H * 0.28),
                          stroke=7, spacing=20, pad=38)

    # Flechas + CTA — parte inferior
    font_arrow = _load_font(62)
    _dibujar_bloque_texto(draw, "▼  SEGUÍ LEYENDO  ▼", font_arrow,
                          y_center=int(_IMG_H * 0.82),
                          fill=(255, 230, 0, 255), stroke_fill=(0, 0, 0, 255),
                          stroke=4, spacing=0, pad=24)

    composited = Image.alpha_composite(base, overlay).convert("RGB")
    path = tempfile.mktemp(suffix="_his_p0.jpg")
    composited.save(path, "JPEG", quality=92)
    return path


def _componer_clip_historia(img_path: str, texto_pagina: str,
                             num_pagina: int, total_paginas: int) -> str:
    """Imagen N: página de la historia + contador + flechas."""
    base = Image.open(img_path).convert("RGBA")
    base = base.resize((_IMG_W, _IMG_H), Image.LANCZOS)
    overlay = Image.new("RGBA", (_IMG_W, _IMG_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Contador pequeño arriba
    font_num = _load_font(36)
    counter = f"📖  {num_pagina} / {total_paginas}"
    bbox_n = draw.textbbox((0, 0), counter, font=font_num)
    cw = bbox_n[2] - bbox_n[0]
    draw.rounded_rectangle(
        [(_IMG_W - cw) // 2 - 16, 60, (_IMG_W + cw) // 2 + 16, 60 + (bbox_n[3] - bbox_n[1]) + 16],
        radius=12, fill=(0, 0, 0, 170),
    )
    draw.text(((_IMG_W - cw) // 2, 68), counter, font=font_num, fill=(200, 200, 200, 220))

    # Texto de la historia — centro
    font_h = _load_font(54)
    _dibujar_bloque_texto(draw, texto_pagina, font_h, y_center=int(_IMG_H * 0.50),
                          stroke=4, spacing=18, pad=34)

    # Flechas abajo — solo si no es la última página
    if num_pagina < total_paginas:
        font_arrow = _load_font(52)
        _dibujar_bloque_texto(draw, "▼  ▼  ▼", font_arrow,
                              y_center=int(_IMG_H * 0.88),
                              fill=(255, 230, 0, 255), stroke_fill=(0, 0, 0, 255),
                              stroke=3, spacing=0, pad=20)

    composited = Image.alpha_composite(base, overlay).convert("RGB")
    path = tempfile.mktemp(suffix=f"_his_p{num_pagina}.jpg")
    composited.save(path, "JPEG", quality=92)
    return path


def _crear_tarjeta_historia() -> str:
    """Tarjeta final oscura con CTA para comentar."""
    img  = Image.new("RGB", (_IMG_W, _IMG_H), (8, 8, 22))
    draw = ImageDraw.Draw(img)

    font_big  = _load_font(88)
    font_sub  = _load_font(58)
    font_arr  = _load_font(70)
    font_small = _load_font(40)

    cy = _IMG_H // 2

    # "¿Y A VOS TE PASÓ?"
    t1 = "¿Y A VOS TE PASÓ?"
    b1 = draw.textbbox((0, 0), t1, font=font_big)
    _texto_con_stroke(draw, ((_IMG_W - (b1[2]-b1[0])) // 2, cy - 280),
                      t1, font_big, fill=(255, 255, 255, 255),
                      stroke_fill=(0, 0, 0, 255), stroke=6)

    # Flechas amarillas
    t2 = "▼  ▼  ▼"
    b2 = draw.textbbox((0, 0), t2, font=font_arr)
    draw.text(((_IMG_W - (b2[2]-b2[0])) // 2, cy - 120),
              t2, font=font_arr, fill=(255, 220, 0))

    # CTA
    t3 = "Contame la tuya"
    b3 = draw.textbbox((0, 0), t3, font=font_sub)
    _texto_con_stroke(draw, ((_IMG_W - (b3[2]-b3[0])) // 2, cy + 20),
                      t3, font_sub, fill=(255, 255, 255, 255),
                      stroke_fill=(0, 0, 0, 255), stroke=4)

    # "en los comentarios 👇"
    t4 = "en los comentarios 👇"
    b4 = draw.textbbox((0, 0), t4, font=font_sub)
    _texto_con_stroke(draw, ((_IMG_W - (b4[2]-b4[0])) // 2, cy + 110),
                      t4, font_sub, fill=(255, 220, 0, 255),
                      stroke_fill=(0, 0, 0, 255), stroke=3)

    # Nota pequeña
    t5 = "Historia completa en la descripcion"
    b5 = draw.textbbox((0, 0), t5, font=font_small)
    draw.text(((_IMG_W - (b5[2]-b5[0])) // 2, cy + 240),
              t5, font=font_small, fill=(140, 140, 140))

    path = tempfile.mktemp(suffix="_his_card.jpg")
    img.save(path, "JPEG", quality=92)
    return path


def _agregar_audio_historia(
    video_in: str,
    video_out: str,
    musica: str | None,
    volumen_musica: float,
    total_dur: float,
) -> tuple[bool, str]:
    """Agrega música opcional al slideshow ya compuesto."""
    fade_start = max(0, total_dur - 1.5)
    if musica and Path(musica).exists():
        cmd = [
            FFMPEG, "-y", "-i", video_in,
            "-stream_loop", "-1", "-i", musica,
            "-map", "0:v", "-map", "1:a",
            "-af", f"volume={volumen_musica},afade=t=out:st={fade_start}:d=1.5",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
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
        return False, r.stderr.decode(errors="ignore")[-600:]
    return True, ""


# ── Pipeline principal ─────────────────────────────────────────────────────────

def crear_reel_historia(
    imagenes: list[str],
    pregunta_video: str,
    historia: str,
    output: str,
    musica: str | None = None,
    volumen_musica: float = 0.18,
) -> tuple[bool, str]:
    """
    Crea el reel con:
    - Clip 1: pregunta + 'Seguí leyendo ▼'
    - Clips 2-N: historia completa paginada + contador + flechas
    - Tarjeta final: CTA para comentar
    """
    if not imagenes:
        return False, "No hay imágenes."

    carpeta = Path(output).parent
    paginas = _paginar_historia(historia)
    n_imgs  = min(_N_IMAGENES, len(imagenes))

    # Distribuir: clip 0 → pregunta, clips 1..N-1 → páginas de historia
    # Si hay más páginas que imágenes disponibles, las últimas páginas se acumulan
    slots_historia = n_imgs - 1  # imágenes 1 en adelante
    if slots_historia < 1:
        slots_historia = 1

    # Agrupar páginas en slots disponibles
    if len(paginas) <= slots_historia:
        paginas_por_slot = [[p] for p in paginas]
        # Rellenar slots vacíos con la última página
        while len(paginas_por_slot) < slots_historia:
            paginas_por_slot.append([paginas[-1]] if paginas else [""])
    else:
        # Distribuir páginas sobrantes en los últimos slots
        paginas_por_slot = []
        base, extra = divmod(len(paginas), slots_historia)
        idx = 0
        for i in range(slots_historia):
            n = base + (1 if i < extra else 0)
            paginas_por_slot.append(paginas[idx:idx+n])
            idx += n

    total_paginas = len(paginas)
    pag_idx = 0  # índice global de página para el contador
    clips: list[str] = []
    tmp_imgs: list[str] = []

    # Clip 0 — pregunta
    img0_compuesto = _componer_clip_pregunta(imagenes[0], pregunta_video)
    tmp_imgs.append(img0_compuesto)
    clip0 = str(carpeta / "his_clip_00.mp4")
    ok, err = _imagen_a_clip(img0_compuesto, clip0, _SECS_POR_IMG)
    if not ok:
        return False, f"Error clip pregunta:\n{err[-300:]}"
    clips.append(clip0)

    # Clips 1..N — historia paginada
    for slot_i, grupo in enumerate(paginas_por_slot):
        img_idx = slot_i + 1
        if img_idx >= len(imagenes):
            img_idx = len(imagenes) - 1
        texto_slot = "\n".join(grupo)
        pag_idx += len(grupo)

        img_compuesto = _componer_clip_historia(
            imagenes[img_idx], texto_slot,
            num_pagina=pag_idx,
            total_paginas=total_paginas,
        )
        tmp_imgs.append(img_compuesto)
        clip_path = str(carpeta / f"his_clip_{img_idx:02d}.mp4")
        ok, err = _imagen_a_clip(img_compuesto, clip_path, _SECS_POR_IMG)
        if not ok:
            return False, f"Error clip historia {slot_i+1}:\n{err[-300:]}"
        clips.append(clip_path)

    # Tarjeta final CTA
    card_img = _crear_tarjeta_historia()
    tmp_imgs.append(card_img)
    card_clip = str(carpeta / "his_card.mp4")
    ok, err = _imagen_a_clip(card_img, card_clip, _CARD_DUR)
    if not ok:
        return False, f"Error tarjeta:\n{err[-300:]}"
    clips.append(card_clip)

    # Limpiar imágenes temporales
    for p in tmp_imgs:
        Path(p).unlink(missing_ok=True)

    # Xfade
    slideshow = str(carpeta / "his_slideshow.mp4")
    ok, err = _concatenar_con_xfade(clips, slideshow)
    if not ok:
        return False, f"Error xfade:\n{err[-300:]}"

    # Calcular duración real
    n_clips = len(clips)
    total_dur = round(n_clips * _SECS_POR_IMG - (n_clips - 1) * _XFADE_DUR, 2)

    # Agregar audio
    ok, err = _agregar_audio_historia(slideshow, output, musica, volumen_musica, total_dur)
    Path(slideshow).unlink(missing_ok=True)
    if not ok:
        return False, f"Error audio:\n{err[-300:]}"

    return True, ""


def construir_descripcion(historia: str, pregunta_video: str, hashtags: list[str]) -> str:
    """Arma la descripción completa para YouTube."""
    tags = " ".join(hashtags)
    return f"{historia}\n\n{pregunta_video}\n\n{tags}"
