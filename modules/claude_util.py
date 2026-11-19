"""
Utilidades compartidas para leer respuestas de la API de Claude.

El motivo de que esto exista: hasta ahora todos los módulos sacaban el texto con
`respuesta.content[0].text`, que asume que el primer bloque de la respuesta es el
texto. Eso es cierto en los modelos que usamos hoy (opus-4-6, sonnet-4-6), pero
deja de serlo en cuanto el modelo razona: desde Opus 4.7 / Sonnet 5 en adelante el
thinking viene prendido por defecto y `content[0]` pasa a ser un `ThinkingBlock`,
que no tiene `.text`. El síntoma es exactamente este error:

    'ThinkingBlock' object has no attribute 'text'

Usar `texto_respuesta()` en vez del índice fijo deja los módulos listos para
cambiar de modelo sin tocar nada más.
"""


def texto_respuesta(respuesta) -> str:
    """
    Devuelve el texto del primer bloque de tipo "text" de una respuesta de Claude.

    Ignora los bloques de thinking y cualquier otro tipo que no sea texto. Si la
    respuesta no trae ningún bloque de texto devuelve "", para que el llamador
    falle por su propio camino de error (JSON inválido, int() que no parsea, etc.)
    en vez de romper acá con un AttributeError.
    """
    for bloque in getattr(respuesta, "content", None) or []:
        if getattr(bloque, "type", None) == "text":
            return bloque.text
    return ""
