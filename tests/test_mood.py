from grilo.mood import COMEBACK, IRRITATED, NORMAL, SULKING, Mood

BASE = {"irritacao": 20, "orgulho": 50, "energia": 60}


def make_mood(clock, **kwargs):
    params = dict(decay_per_minute=1.0, irritation_limit=80, pride_high=60, sulk_minutes=10)
    params.update(kwargs)
    return Mood(BASE, clock=clock, **params)


def test_starts_at_baseline(clock):
    mood = make_mood(clock)
    assert mood.rounded() == BASE
    assert mood.state() == NORMAL


def test_insult_raises_irritation(clock):
    mood = make_mood(clock)
    mood.apply({"irritacao": 15, "orgulho": -8})
    assert mood.rounded()["irritacao"] == 35
    assert mood.rounded()["orgulho"] == 42


def test_values_are_clamped(clock):
    mood = make_mood(clock)
    mood.apply({"irritacao": 500, "energia": -500})
    assert mood.values["irritacao"] == 100
    assert mood.values["energia"] == 0


def test_decay_moves_toward_baseline(clock):
    mood = make_mood(clock)
    mood.apply({"irritacao": 30, "energia": -30})  # 50 e 30
    clock.advance(minutes=10)
    mood.decay()
    assert mood.rounded()["irritacao"] == 40   # desceu 10
    assert mood.rounded()["energia"] == 40     # subiu 10


def test_decay_does_not_overshoot_baseline(clock):
    mood = make_mood(clock)
    mood.apply({"irritacao": 5, "orgulho": -5})
    clock.advance(minutes=60)
    mood.decay()
    assert mood.rounded() == BASE


def test_unknown_key_is_ignored(clock):
    mood = make_mood(clock)
    mood.apply({"felicidade": 50})
    assert mood.rounded() == BASE


def test_over_limit_with_low_pride_sulks(clock):
    mood = make_mood(clock)
    mood.apply({"irritacao": 70, "orgulho": -20})  # irritação 90, orgulho 30
    assert mood.is_sulking()
    assert mood.state() == SULKING


def test_over_limit_with_high_pride_comes_back(clock):
    mood = make_mood(clock)
    mood.apply({"orgulho": 30})                    # orgulho 80
    mood.apply({"irritacao": 70})                  # irritação 90
    assert not mood.is_sulking()
    assert mood.state() == COMEBACK


def test_at_limit_is_not_over(clock):
    mood = make_mood(clock)
    mood.apply({"irritacao": 60})                  # exatamente 80
    assert mood.state() == NORMAL


def test_sulk_ends_after_configured_minutes(clock):
    mood = make_mood(clock, sulk_minutes=10)
    mood.apply({"irritacao": 75, "orgulho": -20})  # 95
    clock.advance(minutes=9)
    assert mood.is_sulking()
    clock.advance(minutes=2)
    assert not mood.is_sulking()
    mood.decay()
    # birra acabou, mas ainda está acima do limite (95 - 11 = 84) com orgulho baixo
    assert mood.state() == IRRITATED


def test_snapshot_has_values_and_state(clock):
    mood = make_mood(clock)
    snap = mood.snapshot()
    assert snap["irritacao"] == 20
    assert snap["estado"] == NORMAL
    assert snap["emburrado_ate"] is None
