"""
Configuración central del canal.
Modificá estos valores para ajustar el comportamiento del bot.
"""

# ── Estrategia de contenido ───────────────────────────────────────────────────
FORMATO_PRINCIPAL  = "texto"    # "texto" | "imagen" | "historia"
FORMATO_SECUNDARIO = "imagen"   # "texto" | "imagen" | "historia"

# ── Timing de video ───────────────────────────────────────────────────────────
SECS_POR_PAGINA_TEXTO: float = 3.0   # segundos por página en formato /texto

# ── ElevenLabs (voiceover) — completar cuando esté listo ──────────────────────
ELEVENLABS_API_KEY: str = ""          # se sobreescribe con la variable de entorno
ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"
