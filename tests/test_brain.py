import random

from grilo.brain import (DEBOCHADO, DIRETO, FILOSOFICO, Brain, Context, extract_citation,
                         render_sources)
from grilo.fallback import Fallback
from grilo.library import Passage
from grilo.mood import Mood

PERSONA = {
    "system_prompt": "Você é o grilo.",
    "response_modes": {"normal": {"debochado": 60, "direto": 25, "filosofico": 15},
                       "retruque": {"debochado": 100}},
    "mode_instructions": {"direto": "MODO DIRETO", "filosofico": "MODO FILOSOFICO"},
    "language_instructions": {"pt": "IDIOMA PT"},
    "oracle_absent_chance": 0,
    "templates": {"pt": {
        "generico": ["genérico"], "direto": ["Sim."],
        "filosofico": ["\"{quote}\", {author}."],
        "fonte": ["{author}, em {work}."], "fonte_desconhecida": ["Foi minha."],
    }},
    "citacoes": {"pt": [{"texto": "Penso, logo existo.", "autor": "Descartes",
                         "obra": "Discurso do Método"}]},
}


class FakeLLM:
    """Guarda o último prompt e devolve uma resposta fixa (ou None = offline)."""

    def __init__(self, answer=None, vector=None):
        self.answer = answer
        self.vector = vector
        self.last_messages = None
        self.last_error = "offline de mentira"

    def chat(self, messages, timeout=None, max_tokens=120):
        self.last_messages = messages
        return self.answer

    def embed(self, texts, timeout=None):
        return [self.vector] if self.vector else None


class FakeLibrary:
    empty = False

    def search(self, vector, k=3):
        return [Passage("Camus", "O Mito de Sísifo", "trecho sobre o absurdo"),
                Passage("Nietzsche", "A Gaia Ciência", "trecho sobre o eterno retorno")]


def brain(llm, library=None, seed=1):
    rng = random.Random(seed)
    return Brain(PERSONA, llm, Fallback(PERSONA, rng), library, {}, rng)


def ctx(clock, **kwargs):
    return Context(mood=Mood({"irritacao": 15, "orgulho": 50, "energia": 60}, clock=clock),
                   **kwargs)


def test_subject_forces_mode(clock):
    b = brain(FakeLLM())
    assert b.choose_mode("filosofia", "normal")[0] == FILOSOFICO
    assert b.choose_mode("fonte", "normal")[0] == DIRETO


def test_mode_weights_follow_mood_state():
    b = brain(FakeLLM())
    assert {b.choose_mode("pergunta", "retruque")[0] for _ in range(50)} == {DEBOCHADO}
    seen = {b.choose_mode("pergunta", "normal")[0] for _ in range(300)}
    assert seen == {DEBOCHADO, DIRETO, FILOSOFICO}


def test_direct_mode_without_llm_is_short_and_plain(clock):
    b = brain(FakeLLM())
    b.choose_mode = lambda category, state: (DIRETO, "teste")
    decision = b.reply("vai chover?", "pergunta", ctx(clock))
    assert decision.text == "Sim."
    assert decision.mode == DIRETO


def test_philosophical_fallback_records_the_quote_source(clock):
    decision = brain(FakeLLM()).reply("o que é a existência?", "filosofia", ctx(clock))
    assert "Penso, logo existo." in decision.text
    assert decision.citation == {"author": "Descartes", "work": "Discurso do Método"}


def test_source_question_without_llm_uses_source_memory(clock):
    sources = [{"line": "Penso, logo existo.", "author": "Descartes", "work": "Discurso do Método"}]
    decision = brain(FakeLLM()).reply("de quem é essa frase?", "fonte", ctx(clock, sources=sources))
    assert decision.text == "Descartes, em Discurso do Método."


def test_source_question_without_memory_admits_it_was_his(clock):
    decision = brain(FakeLLM()).reply("quem disse isso?", "fonte", ctx(clock))
    assert decision.text == "Foi minha."


def test_source_memory_goes_into_the_prompt(clock):
    llm = FakeLLM(answer="Descartes, darling.")
    sources = [{"line": "Penso, logo existo.", "author": "Descartes", "work": "Discurso do Método"}]
    brain(llm).reply("de quem é essa frase?", "fonte", ctx(clock, sources=sources))
    system = llm.last_messages[0]["content"]
    assert "\"Penso, logo existo.\" → Descartes, Discurso do Método" in system
    assert system.endswith("IDIOMA PT")  # idioma continua sendo a última instrução


def test_philosophical_llm_answer_cites_the_marked_passage(clock):
    llm = FakeLLM(answer="Como dizia Nietzsche, tudo volta. [[2]]", vector=[0.1, 0.2])
    decision = brain(llm, FakeLibrary()).reply("e o eterno retorno?", "filosofia", ctx(clock))
    assert decision.text == "Como dizia Nietzsche, tudo volta."
    assert decision.citation == {"author": "Nietzsche", "work": "A Gaia Ciência"}
    system = llm.last_messages[0]["content"]
    assert "[1] Camus, O Mito de Sísifo" in system and "MODO FILOSOFICO" in system


def test_extract_citation_without_marker_keeps_consulted_books():
    passages = [Passage("Camus", "O Mito de Sísifo", "...")]
    text, citation = extract_citation("O absurdo é isso aí.", passages)
    assert text == "O absurdo é isso aí."
    assert citation == {"consulted": ["Camus, O Mito de Sísifo"]}


def test_extract_citation_ignores_invalid_marker():
    text, citation = extract_citation("Algo [[7]]", [])
    assert (text, citation) == ("Algo", None)


def test_render_sources_lists_line_and_origin():
    out = render_sources([{"line": "x", "consulted": ["Camus, O Mito"]}])
    assert "\"x\" → baseada em trechos de: Camus, O Mito" in out


def test_npc_reaction_without_llm_uses_template(clock):
    persona = {**PERSONA, "templates": {"pt": {"npc_reacao": ["{npc}, ninguém perguntou."]}}}
    rng = random.Random(1)
    b = Brain(persona, FakeLLM(), Fallback(persona, rng), None, {}, rng)
    b.choose_mode = lambda category, state: (DEBOCHADO, "teste")
    decision = b.react_to_npc("Hefesto", "o grilo é inútil", ctx(clock))
    assert decision.text == "Hefesto, ninguém perguntou."
