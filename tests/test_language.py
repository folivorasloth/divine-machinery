import pytest

from common.language import detect_language


@pytest.mark.parametrize("text, expected", [
    ("what are you doing", "en"),
    ("shut up grilo", "en"),
    ("good morning, old chap", "en"),
    ("I think it's broken", "en"),
    ("o que você está fazendo", "pt"),
    ("esse grilo só fala bosta", "pt"),
    ("tô cansado", "pt"),                 # só o acento já decide
    ("bom dia grilo", "pt"),
])
def test_detects_language(text, expected):
    assert detect_language(text, current="pt" if expected == "en" else "en") == expected


@pytest.mark.parametrize("text", ["ok", "kkkk", "lol", "grilo", "???"])
def test_ambiguous_text_keeps_current_language(text):
    assert detect_language(text, current="en") == "en"
    assert detect_language(text, current="pt") == "pt"
