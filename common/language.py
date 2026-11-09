"""Detecção simples de idioma: português ou inglês.

Conta palavras muito comuns de cada língua (sem dicionário, sem biblioteca).
Empate — "ok", "lol", "kkkk" — mantém o idioma atual, então a conversa
só muda de língua quando você realmente muda.
"""

import re

from common.text import normalize

PT = "pt"
EN = "en"

# Palavras que existem nas duas línguas ("a", "do", "no", "me", "so", "as", "to")
# ficaram de fora de propósito: elas não ajudam a decidir.
EN_WORDS = {
    "the", "you", "your", "you're", "i", "i'm", "im", "is", "are", "was", "were",
    "what", "why", "how", "this", "that", "it", "it's", "its", "my", "and", "of",
    "not", "just", "can", "with", "have", "has", "be", "don't", "dont", "at", "for",
    "an", "who", "will", "would", "about", "really", "please", "thanks", "thank",
    "cheers", "hello", "hi", "yes", "yeah", "sorry", "good", "morning", "night",
    "shut", "up", "does", "did", "know", "think", "want", "like", "going", "doing", "get",
}
PT_WORDS = {
    "o", "os", "que", "de", "da", "dos", "das", "nao", "voce", "vc", "e", "um", "uma",
    "eu", "por", "isso", "isto", "com", "para", "pra", "ta", "meu", "minha", "esse",
    "essa", "este", "esta", "mas", "muito", "tudo", "oi", "sim", "porque", "ja", "ele",
    "ela", "sou", "estou", "tem", "foi", "vai", "agora", "aqui", "valeu", "obrigado",
    "obrigada", "bom", "boa", "dia", "noite", "bem", "fala", "como", "quando", "onde",
    "qual", "quem", "seu", "sua", "nem", "mais", "entao", "tambem", "hoje",
}
# Letras que praticamente só aparecem em português
PT_LETTERS = set("ãõçáéíóúâêôà")


def detect_language(text: str, current: str = PT) -> str:
    words = re.findall(r"[a-z']+", normalize(text))
    en = sum(word in EN_WORDS for word in words)
    pt = sum(word in PT_WORDS for word in words)
    if any(c in PT_LETTERS for c in text.lower()):
        pt += 2
    if en > pt:
        return EN
    if pt > en:
        return PT
    return current
