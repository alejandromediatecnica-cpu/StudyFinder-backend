"""
Validación de archivos por su contenido real (firmas mágicas), no por extensión.

Motivación: un usuario puede renombrar `virus.exe` a `informe.pdf`. Aquí se
compara el comienzo de los bytes con la firma esperada del formato.

CÓMO AÑADIR UN FORMATO NUEVO:
  - Añade su extensión y firma a `DOCUMENT_MAGIC` (o `IMAGE_MAGIC` si es imagen).
CÓMO RELAJAR LA VALIDACIÓN:
  - No lo recomendamos; si hiciera falta, cambia la función que llama a
    `extension_matches_content` en los routers correspondientes.
"""

# Firmas mágicas (bytes iniciales) de los formatos soportados.
PDF_MAGIC = b"%PDF-"
# Los documentos Office modernos (.docx/.pptx/.xlsx) son ZIP.
ZIP_MAGIC = b"PK\x03\x04"
# Los Office antiguos (.doc/.ppt/.xls) son contenedores OLE.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Extensión -> firmas válidas para documentos de biblioteca.
DOCUMENT_MAGIC = {
    ".pdf": (PDF_MAGIC,),
    ".doc": (OLE_MAGIC,),
    ".docx": (ZIP_MAGIC,),
    ".ppt": (OLE_MAGIC,),
    ".pptx": (ZIP_MAGIC,),
    ".xls": (OLE_MAGIC,),
    ".xlsx": (ZIP_MAGIC,),
}

# Extensión -> firmas válidas para adjuntos de mensajes (documentos + imágenes).
IMAGE_MAGIC = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    # WebP necesita comprobación extra (ver `extension_matches_content`).
    ".webp": (b"RIFF",),
    ".gif": (b"GIF87a", b"GIF89a"),
}


def extension_matches_content(extension: str, content: bytes, table: dict) -> bool:
    """Comprueba si `content` empieza con una firma válida para `extension`.

    - `.webp` es especial: además de `RIFF` debe tener `WEBP` en los bytes 8..12.
    - Si la extensión no está en la tabla o el contenido está vacío, devuelve False.

    RETORNO: True si el contenido coincide con alguna firma permitida.
    """
    signatures = table.get(extension.lower())
    if not signatures or not content:
        return False
    if extension.lower() == ".webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return any(content.startswith(signature) for signature in signatures)


def document_content_is_valid(extension: str, content: bytes) -> bool:
    """Valida un documento de biblioteca contra `DOCUMENT_MAGIC`.

    Usado por `app/api/documents.py:upload_document` antes de guardar el archivo.
    """
    return extension_matches_content(extension, content, DOCUMENT_MAGIC)


def message_content_is_valid(extension: str, content: bytes) -> bool:
    """Valida un adjunto de mensaje contra documentos + imágenes.

    Usado por `app/api/messages.py:send_message`.
    """
    return extension_matches_content(
        extension, content, {**DOCUMENT_MAGIC, **IMAGE_MAGIC}
    )


def image_extension(content: bytes) -> str | None:
    """Detecta la extensión de una imagen válida a partir de sus bytes.

    Se usa para los avatares: no confiamos en el nombre que envía el cliente.
    RETORNO: la extensión detectada (`.jpg`, `.png`, `.webp`, `.gif`) o None.
    """
    for extension, signatures in IMAGE_MAGIC.items():
        if extension == ".webp":
            if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
                return extension
            continue
        if any(content.startswith(signature) for signature in signatures):
            return extension
    return None
