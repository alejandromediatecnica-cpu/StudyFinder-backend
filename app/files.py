"""Validación de archivos por contenido real, no solo por extensión."""

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

DOCUMENT_MAGIC = {
    ".pdf": (PDF_MAGIC,),
    ".doc": (OLE_MAGIC,),
    ".docx": (ZIP_MAGIC,),
    ".ppt": (OLE_MAGIC,),
    ".pptx": (ZIP_MAGIC,),
    ".xls": (OLE_MAGIC,),
    ".xlsx": (ZIP_MAGIC,),
}

IMAGE_MAGIC = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
    ".gif": (b"GIF87a", b"GIF89a"),
}


def extension_matches_content(extension: str, content: bytes, table: dict) -> bool:
    signatures = table.get(extension.lower())
    if not signatures or not content:
        return False
    if extension.lower() == ".webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return any(content.startswith(signature) for signature in signatures)


def document_content_is_valid(extension: str, content: bytes) -> bool:
    return extension_matches_content(extension, content, DOCUMENT_MAGIC)


def message_content_is_valid(extension: str, content: bytes) -> bool:
    return extension_matches_content(
        extension, content, {**DOCUMENT_MAGIC, **IMAGE_MAGIC}
    )


def image_extension(content: bytes) -> str | None:
    for extension, signatures in IMAGE_MAGIC.items():
        if extension == ".webp":
            if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
                return extension
            continue
        if any(content.startswith(signature) for signature in signatures):
            return extension
    return None
