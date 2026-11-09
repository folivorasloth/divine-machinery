import random

from common.conversation import ConversationPolicy

CHANCES = {"normal": 0.0, "irritado": 1.0}


def policy(clock, **kwargs):
    params = dict(max_depth=3, cooldown_seconds=60, clock=clock, rng=random.Random(1))
    params.update(kwargs)
    return ConversationPolicy("grilo", ["grilo", "Grilo Falante"], CHANCES, **params)


def test_ignores_own_messages(clock):
    assert not policy(clock).should_react("grilo", {}, "oi", "irritado")[0]


def test_always_answers_when_addressed(clock):
    p = policy(clock)
    assert p.should_react("hefesto", {"to": "grilo"}, "oi", "normal")[0]
    assert p.should_react("hefesto", {}, "o Grilo é um inseto", "normal")[0]


def test_depth_limit_stops_endless_conversation(clock):
    p = policy(clock)
    react, why = p.should_react("hefesto", {"to": "grilo", "depth": 3}, "oi", "irritado")
    assert not react and "encadeadas" in why


def test_unaddressed_reaction_depends_on_mood(clock):
    p = policy(clock)
    assert not p.should_react("hefesto", {}, "bom dia, João", "normal")[0]
    assert p.should_react("hefesto", {}, "bom dia, João", "irritado")[0]


def test_cooldown_per_npc(clock):
    p = policy(clock)
    p.mark("hefesto")
    assert not p.should_react("hefesto", {}, "bom dia", "irritado")[0]
    assert p.should_react("olho", {}, "bom dia", "irritado")[0]   # outro NPC: liberado
    clock.advance(seconds=61)
    assert p.should_react("hefesto", {}, "bom dia", "irritado")[0]
