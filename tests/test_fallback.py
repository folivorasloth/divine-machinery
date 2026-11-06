import random

import pytest

from common.config import load_yaml
from grilo.fallback import Fallback, detect_category, fill, format_duration, normalize

KEYWORDS = {
    "insulto": ["bosta", "burro", "cala a boca"],
    "elogio": ["valeu", "genio"],
    "pergunta": ["por que", "como"],
}


@pytest.mark.parametrize("text, expected", [
    ("esse grilo só fala bosta", "insulto"),
    ("CALA A BOCA", "insulto"),
    ("valeu, grilo", "elogio"),
    ("você é um gênio", "elogio"),               # acento não atrapalha
    ("por que o código quebrou", "pergunta"),
    ("tá funcionando?", "pergunta"),              # "?" no final
    ("bom dia", "generico"),
    ("você é burro?", "insulto"),                 # insulto vence pergunta
    ("burrocracia", "generico"),                  # palavra inteira, não pedaço
])
def test_detect_category(text, expected):
    assert detect_category(text, KEYWORDS) == expected


def test_normalize_removes_accents_and_case():
    assert normalize("Só Fala BÓSTA, Inútil") == "so fala bosta, inutil"


def test_fill_variables_and_missing_ones():
    assert fill("{app} há {session}", {"app": "Code", "session": "2h"}) == "Code há 2h"
    assert fill("oi {nao_existe}", {}) == "oi ???"


@pytest.mark.parametrize("seconds, expected", [
    (0, "0min"), (45 * 60, "45min"), (2 * 3600, "2h"), (7800, "2h10min"), (None, "?"),
])
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_reply_uses_category_templates():
    persona = {"templates": {"insulto": ["Ofensa às {time}."], "generico": ["genérico"]},
               "oracle_absent_chance": 0}
    fb = Fallback(persona, random.Random(1))
    assert fb.reply("insulto", {"time": "22:00"}) == "Ofensa às 22:00."


def test_reply_falls_back_to_generic_when_category_has_no_templates():
    persona = {"templates": {"generico": ["genérico"]}, "oracle_absent_chance": 0}
    assert Fallback(persona).reply("elogio", {}) == "genérico"


def test_reply_can_mention_the_oracle():
    persona = {"templates": {"generico": ["oi."], "oraculo_ausente": ["(sem Oráculo)"]},
               "oracle_absent_chance": 1.0}
    assert Fallback(persona).reply("generico", {}) == "oi. (sem Oráculo)"


def test_real_persona_file_is_valid():
    """Pega erro de digitação no persona/grilo.yaml antes de subir o grilo."""
    persona = load_yaml("persona/grilo.yaml")
    fb = Fallback(persona)
    assert fb.category("esse grilo só fala bosta") == "insulto"
    for key in ("insulto", "elogio", "pergunta", "generico", "retruque", "emburrado",
                "long_session", "back_from_afk", "late_night", "distraction"):
        assert persona["templates"].get(key), f"faltam templates para {key}"
        for template in persona["templates"][key]:
            # fill não pode explodir com chave/format inválido
            fill(template, {"time": "1", "app": "a", "title": "t", "session": "s",
                            "afk": "x", "target": "y"})
