"""Respostas sem LLM: detecção de categoria por palavras-chave + templates.

Tudo (palavras-chave e frases) vem do persona/grilo.yaml. Os templates ficam
separados por idioma: templates.pt.insulto, templates.en.insulto...
"""

import random
import re

from common.text import normalize

DEFAULT_CATEGORY = "generico"


def detect_category(text: str, keywords: dict[str, list[str]]) -> str:
    """Devolve a primeira categoria cujas palavras-chave aparecem no texto.

    A ordem das categorias no YAML é a prioridade: "você é burro?" tem
    "burro" (insulto) e "?" (pergunta), e insulto vem primeiro.
    """
    norm = normalize(text)
    for category, words in keywords.items():
        for word in words or []:
            # \b = fronteira de palavra: "chato" não casa dentro de "chatoxyz"
            if re.search(rf"\b{re.escape(normalize(word))}\b", norm):
                return category
    if norm.rstrip().endswith("?"):
        return "pergunta"
    return DEFAULT_CATEGORY


def format_duration(seconds: float | None) -> str:
    """7800 → "2h10min", 2700 → "45min"."""
    if seconds is None:
        return "?"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}h{minutes:02d}min"
    if hours:
        return f"{hours}h"
    return f"{minutes}min"


class _SafeVars(dict):
    # Variável que não existe vira "???" em vez de quebrar com KeyError.
    def __missing__(self, key):
        return "???"


def fill(template: str, variables: dict) -> str:
    return template.format_map(_SafeVars(variables))


class Fallback:
    def __init__(self, persona: dict, rng: random.Random | None = None):
        self.keywords = persona.get("categories", {})
        self.templates = persona.get("templates", {})  # {idioma: {chave: [frases]}}
        self.default_language = persona.get("default_language", "pt")
        self.oracle_chance = persona.get("oracle_absent_chance", 0.2)
        self.quotes = persona.get("citacoes", {})  # {idioma: [{texto, autor, obra}]}
        self.rng = rng or random.Random()

    def category(self, text: str) -> str:
        return detect_category(text, self.keywords)

    def pick(self, key: str, variables: dict, lang: str | None = None) -> str | None:
        """Sorteia um template da lista `key` no idioma pedido e preenche as variáveis.
        Se faltar a lista naquele idioma, usa a do idioma padrão."""
        options = (self._templates(lang).get(key)
                   or self._templates(self.default_language).get(key) or [])
        if not options:
            return None
        return fill(self.rng.choice(options), variables)

    def _templates(self, lang: str | None) -> dict:
        return self.templates.get(lang or self.default_language) or {}

    def reply(self, key: str, variables: dict, lang: str | None = None,
              llm_down: bool = True) -> str:
        text = (self.pick(key, variables, lang)
                or self.pick(DEFAULT_CATEGORY, variables, lang) or "...")
        # De vez em quando ele comenta que está sem o LLM.
        if llm_down and self.rng.random() < self.oracle_chance:
            note = self.pick("oraculo_ausente", variables, lang)
            if note:
                text = f"{text} {note}"
        return text

    def quote(self, lang: str | None = None) -> dict | None:
        """Uma citação curada ({texto, autor, obra}) no idioma pedido."""
        options = (self.quotes.get(lang or self.default_language)
                   or self.quotes.get(self.default_language) or [])
        return self.rng.choice(options) if options else None
