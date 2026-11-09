"""Utilidades de texto compartilhadas."""

import unicodedata


def normalize(text: str) -> str:
    """Minúsculas e sem acento: "Só fala BOSTA" → "so fala bosta"."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))
