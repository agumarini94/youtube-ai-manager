"""
Módulo — Reel de Elección Visual ("¿Cuál elegís?")
Pregunta tipo "¿Qué te regalaría tu novia?" + 6 imágenes numeradas.
Reutiliza toda la infraestructura de imagen_reel sin modificarla.
"""

import json
import os
import tempfile
from pathlib import Path

import anthropic
from PIL import Image, ImageDraw, ImageFont

from modules.imagen_reel import (
    descargar_imagenes,
    crear_reel_imagenes,
    _load_font,
    _IMG_W,
    _IMG_H,
)

_HISTORIAL_FILE = Path(__file__).parent.parent / "eleccion_historial.json"


def _cargar_historial() -> dict:
    if _HISTORIAL_FILE.exists():
        try:
            return json.loads(_HISTORIAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def guardar_en_historial(categoria: str, nombres: list[str]) -> None:
    """Guarda los nombres usados para evitar repetirlos en la próxima generación."""
    historial = _cargar_historial()
    prev = historial.get(categoria, [])
    combined = nombres + [n for n in prev if n not in nombres]
    historial[categoria] = combined[:18]  # guarda las últimas 3 rondas (6×3)
    try:
        _HISTORIAL_FILE.write_text(
            json.dumps(historial, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ── Categorías preset ──────────────────────────────────────────────────────────

CATEGORIAS: dict[str, dict] = {
    "🚗 Coches":        {
        "hint": "coches (Ferrari, Range Rover, pickup, deportivo, clásico, eléctrico)",
        "terminos_base": ["ferrari sports car", "range rover suv", "dodge pickup truck",
                          "porsche 911 red", "classic mustang car", "tesla electric car"],
    },
    "🏠 Casas":         {
        "hint": "casas (cabaña, penthouse, villa moderna, casa colonial, loft, chalet)",
        "terminos_base": ["modern luxury villa house", "cozy mountain cabin", "city penthouse apartment",
                          "colonial house exterior", "industrial loft interior", "beach house tropical"],
    },
    "🏖️ Vacaciones":    {
        "hint": "destinos (Caribe, montañas, ciudad europea, desierto, Asia, patagonia)",
        "terminos_base": ["caribbean beach turquoise water", "snowy mountain ski resort",
                          "paris eiffel tower night", "sahara desert camels",
                          "tokyo japan street lights", "patagonia mountains lake"],
    },
    "⌚ Relojes":        {
        "hint": "relojes (Rolex, deportivo, vintage, smartwatch, elegante, militar)",
        "terminos_base": ["rolex gold watch wrist", "sport chronograph watch", "vintage leather watch",
                          "apple watch smartwatch", "dress watch elegant silver", "military tactical watch"],
    },
    "👟 Zapatillas":    {
        "hint": "zapatillas (Air Jordan, Yeezy, Nike clásico, Converse, botín, running)",
        "terminos_base": ["air jordan sneakers red", "yeezy boost white", "nike air force white",
                          "converse chuck taylor", "adidas ultraboost running", "high top boots street"],
    },
    "🍕 Comida":        {
        "hint": "comidas (sushi, pizza, asado, hamburguesa, pasta, tacos)",
        "terminos_base": ["sushi platter japanese food", "neapolitan pizza wood oven",
                          "argentinian bbq asado", "gourmet burger juicy",
                          "italian pasta carbonara", "mexican tacos street food"],
    },
    "💻 Tech":          {
        "hint": "gadgets (MacBook, gaming PC, consola, cámara, auriculares, drone)",
        "terminos_base": ["macbook pro laptop desk", "gaming pc rgb setup", "playstation 5 console",
                          "sony camera photography", "bose headphones music", "dji drone flying"],
    },
    "🐕 Mascotas":      {
        "hint": "razas de perros (Golden, Husky, French Bulldog, Pastor Alemán, Labrador, Pomerania)",
        "terminos_base": ["golden retriever puppy cute", "siberian husky blue eyes",
                          "french bulldog adorable", "german shepherd dog",
                          "labrador retriever outdoor", "pomeranian fluffy puppy"],
    },
    "✈️ Aerolíneas":    {
        "hint": "clases de vuelo y aerolíneas (primera clase, business, low cost, privado, helicóptero, ejecutivo)",
        "terminos_base": ["first class airplane seat luxury", "business class flat bed",
                          "private jet interior", "economy flight window seat",
                          "helicopter cockpit flying", "concorde supersonic plane"],
    },
    "🍺 Salidas":       {
        "hint": "planes de salida (bar de copas, boliche, cena romántica, after, terraza, bar de barrio)",
        "terminos_base": ["rooftop bar city night", "nightclub dance party",
                          "romantic candlelight dinner", "after party sunrise",
                          "cozy neighborhood bar", "wine terrace outdoor"],
    },
}


# ── Generación con Claude ──────────────────────────────────────────────────────

def generar_eleccion_claude(categoria_key: str) -> dict:
    """
    Genera pregunta tipo '¿cuál elegís?' + 6 términos de búsqueda + nombres cortos para mostrar.
    Retorna: {pregunta, terminos, nombres, titulo, hashtags}
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en .env")

    cfg   = CATEGORIAS[categoria_key]
    hint  = cfg["hint"]

    historial    = _cargar_historial()
    usados_prev  = historial.get(categoria_key, [])
    bloque_hist  = (
        f"\nOPCIONES YA USADAS EN VIDEOS ANTERIORES (no repetir ninguna):\n"
        f"{', '.join(usados_prev)}\n"
        f"Generá 6 opciones DISTINTAS a todas las anteriores.\n"
        if usados_prev else ""
    )

    prompt = f"""Generá contenido viral para YouTube Shorts. Formato: "¿cuál elegís?" con 6 opciones visuales.
Categoría: {hint}{bloque_hist}

PREGUNTA VIRAL (el objetivo es que el espectador reenvíe el video a alguien):
- Formato relacional: la pregunta involucra a un tercero que "tiene que adivinar" o "tiene que elegir bien".
- Ejemplos del tono buscado:
    "Si tu pareja te conoce bien, ¿qué auto te regalaría?"
    "Mandáselo a tu mejor amigo a ver si te conoce"
    "¿Tu crush sabe qué destino elegirías?"
    "Si tu mejor amigo falla esto, corta la amistad 💀"
    "¿Tu novio/a sabe cuál elegirías sin pensarlo?"
- Formato: SIN signos de pregunta opcionales, directo, coloquial, máx 42 caracteres. Cuanto más corta y directa, más impacto visual.
- Tiene que generar comentarios como "le mandé a mi novio y eligió el 4 💀" o "mi amigo me conoce re bien".
- En español latinoamericano, con emojis si suma.

6 TÉRMINOS DE BÚSQUEDA DE IMÁGENES (en inglés, para Pexels):
- Cada uno debe buscar UNA opción específica y visualmente diferente a las otras 5.
- Muy descriptivos: "red ferrari sports car track", no solo "car".
- Las 6 opciones deben ser variadas (no 6 ferraris).

6 NOMBRES CORTOS para mostrar sobre cada foto (en español o nombre propio):
- Máx 18 caracteres cada uno.
- Que identifiquen claramente la opción: "Ferrari F8", "Range Rover", "Maldivas", "París".
- Deben coincidir con lo que va a mostrar la imagen buscada con el término correspondiente.

TÍTULO YouTube (máx 60 chars, emoji al final, genera curiosidad):

HASHTAGS (estrategia nicho medio — evitar tags con >500M posts porque tienen demasiada competencia):
- Fijos: #Shorts #fyp #parati
- 2 de engagement directo: #eligeuno #queleegirias #teconozco #mandaseloatupare
- 1-2 temáticos al tema específico (autos, viajes, etc.) — en español
- Total 6-7 hashtags, sin inglés excepto los fijos:

Respondé SOLO con JSON válido:
{{
  "pregunta": "¿...?",
  "terminos": ["término 1", "término 2", "término 3", "término 4", "término 5", "término 6"],
  "nombres": ["Nombre 1", "Nombre 2", "Nombre 3", "Nombre 4", "Nombre 5", "Nombre 6"],
  "titulo": "titulo especifico con emoji",
  "hashtags": ["#Shorts","#fyp","#parati","#tag4","#tag5","#tag6"]
}}"""

    client = anthropic.Anthropic(api_key=api_key)
    resp   = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ── Numeración de imágenes ─────────────────────────────────────────────────────

def _numerar_imagen(img_path: str, numero: int, nombre: str = "") -> str:
    """
    Agrega número (1-6) en la esquina inferior izquierda y, si se pasa nombre,
    una etiqueta con el nombre de la opción a la derecha del círculo.
    """
    img  = Image.open(img_path).convert("RGB")
    img  = img.resize((_IMG_W, _IMG_H), Image.LANCZOS)

    radio  = 90
    margen = 40
    cx     = margen + radio           # 130
    cy     = _IMG_H - margen - radio  # 1790

    # ── Alpha overlay para fondos semitransparentes ────────────────────────────
    img_rgba = img.convert("RGBA")
    overlay  = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov       = ImageDraw.Draw(overlay)

    # Círculo de fondo del número
    ov.ellipse([cx - radio, cy - radio, cx + radio, cy + radio], fill=(0, 0, 0, 210))
    ov.ellipse([cx - radio, cy - radio, cx + radio, cy + radio], outline=(255, 255, 255), width=5)

    # Etiqueta del nombre a la derecha del círculo
    nombre_clean = nombre.strip()[:20] if nombre.strip() else ""
    if nombre_clean:
        nfont    = _load_font(54)
        pad_x, pad_y = 22, 12
        nb       = ov.textbbox((0, 0), nombre_clean, font=nfont)
        nw, nh   = nb[2] - nb[0], nb[3] - nb[1]
        nx       = cx + radio + 18
        ny       = cy - nh // 2 - pad_y
        rx0, ry0 = nx - pad_x, ny - 4
        rx1, ry1 = min(nx + nw + pad_x, _IMG_W - 30), ny + nh + pad_y + 4
        ov.rounded_rectangle([rx0, ry0, rx1, ry1], radius=20, fill=(0, 0, 0, 195))

    # Componer
    img_rgba = Image.alpha_composite(img_rgba, overlay)
    img      = img_rgba.convert("RGB")
    draw     = ImageDraw.Draw(img)

    # ── Número ────────────────────────────────────────────────────────────────
    font  = _load_font(88)
    texto = str(numero)
    bbox  = draw.textbbox((0, 0), texto, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx    = cx - tw // 2
    ty    = cy - th // 2 - 4
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            if dx != 0 or dy != 0:
                draw.text((tx + dx, ty + dy), texto, font=font, fill=(0, 0, 0))
    draw.text((tx, ty), texto, font=font, fill=(255, 255, 255))

    # ── Nombre ────────────────────────────────────────────────────────────────
    if nombre_clean:
        nfont  = _load_font(54)
        nb     = draw.textbbox((0, 0), nombre_clean, font=nfont)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        nx     = cx + radio + 18
        ny     = cy - nh // 2 - 4
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx != 0 or dy != 0:
                    draw.text((nx + dx, ny + dy), nombre_clean, font=nfont, fill=(0, 0, 0))
        draw.text((nx, ny), nombre_clean, font=nfont, fill=(255, 255, 255))

    out = tempfile.mktemp(suffix=f"_elc_{numero}.jpg")
    img.save(out, "JPEG", quality=92)
    return out


# ── Pipeline principal ─────────────────────────────────────────────────────────

def crear_reel_eleccion(
    imagenes: list[str],
    pregunta: str,
    output: str,
    nombres: list[str] | None = None,
    musica: str | None = None,
    volumen_musica: float = 0.18,
) -> tuple[bool, str]:
    """
    Agrega número + nombre a cada imagen y delega en crear_reel_imagenes.
    """
    if len(imagenes) < 3:
        return False, "Se necesitan al menos 3 imágenes."

    imagenes_numeradas: list[str] = []
    for i, img in enumerate(imagenes[:6]):
        nombre = (nombres[i] if nombres and i < len(nombres) else "")
        numerada = _numerar_imagen(img, i + 1, nombre)
        imagenes_numeradas.append(numerada)

    ok, err = crear_reel_imagenes(
        imagenes_numeradas, pregunta, output, musica, volumen_musica
    )

    for p in imagenes_numeradas:
        Path(p).unlink(missing_ok=True)

    return ok, err
