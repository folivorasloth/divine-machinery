from datetime import datetime
from datetime import time as dtime

from grilo.triggers import Triggers, in_time_window

CONFIG = {
    "global_max_per_hour": 3,
    "long_session": {"hours": 2, "cooldown_minutes": 60},
    "back_from_afk": {"min_afk_minutes": 15, "cooldown_minutes": 30},
    "late_night": {"start": "23:00", "end": "05:00", "cooldown_minutes": 45},
    "distraction": {"cooldown_minutes": 20, "match": ["youtube", "reddit"]},
}


def activity(app="Code", title="main.py", minutes=0, afk=False):
    return {"app": app, "title": title, "app_seconds": minutes * 60, "afk": afk}


def test_long_session_fires_after_threshold(clock):
    t = Triggers(CONFIG, clock)
    assert t.on_activity(activity(minutes=119)) is None
    event = t.on_activity(activity(minutes=120))
    assert event.name == "long_session"


def test_cooldown_blocks_then_releases(clock):
    t = Triggers(CONFIG, clock)
    assert t.on_activity(activity(minutes=150)).name == "long_session"
    clock.advance(minutes=30)
    assert t.on_activity(activity(minutes=180)) is None    # em cooldown
    clock.advance(minutes=31)
    assert t.on_activity(activity(minutes=211)).name == "long_session"


def test_global_limit_per_hour(clock):
    config = {**CONFIG, "global_max_per_hour": 2,
              "distraction": {"cooldown_minutes": 1, "match": ["youtube"]}}
    t = Triggers(config, clock)
    assert t.on_activity(activity(title="YouTube")) is not None
    clock.advance(minutes=2)
    assert t.on_activity(activity(title="YouTube")) is not None
    clock.advance(minutes=2)
    assert t.on_activity(activity(title="YouTube")) is None   # 2/h esgotado
    clock.advance(minutes=60)
    assert t.on_activity(activity(title="YouTube")) is not None  # janela andou


def test_distraction_matches_title_case_insensitive(clock):
    t = Triggers(CONFIG, clock)
    event = t.on_activity(activity(app="Firefox", title="r/python - REDDIT"))
    assert event.name == "distraction"
    assert event.variables["target"] == "reddit"


def test_only_one_trigger_per_update_in_priority_order(clock):
    t = Triggers(CONFIG, clock)
    # Distração E sessão longa ao mesmo tempo: só a distração fala agora.
    event = t.on_activity(activity(title="YouTube", minutes=180))
    assert event.name == "distraction"
    # Na próxima atualização, a distração está em cooldown e a sessão longa passa.
    assert t.on_activity(activity(title="YouTube", minutes=181)).name == "long_session"


def test_no_activity_triggers_while_afk(clock):
    t = Triggers(CONFIG, clock)
    assert t.on_activity(activity(title="YouTube", minutes=500, afk=True)) is None


def test_late_night(clock):
    clock.now = datetime(2026, 11, 7, 2, 30).timestamp()
    t = Triggers(CONFIG, clock)
    assert t.on_activity(activity()).name == "late_night"


def test_not_late_night_during_day(clock):
    t = Triggers(CONFIG, clock)  # 14:00
    assert t.on_activity(activity()) is None


def test_time_window_crossing_midnight():
    start, end = dtime(23, 0), dtime(5, 0)
    assert in_time_window(dtime(23, 30), start, end)
    assert in_time_window(dtime(0, 0), start, end)
    assert in_time_window(dtime(4, 59), start, end)
    assert not in_time_window(dtime(5, 0), start, end)
    assert not in_time_window(dtime(12, 0), start, end)


def test_time_window_same_day():
    assert in_time_window(dtime(1, 0), dtime(0, 30), dtime(5, 0))
    assert not in_time_window(dtime(0, 10), dtime(0, 30), dtime(5, 0))


def test_back_from_afk_after_long_absence(clock):
    t = Triggers(CONFIG, clock)
    assert t.on_afk({"status": "afk", "seconds": 60}) is None
    event = t.on_afk({"status": "present", "seconds": 5, "previous_seconds": 20 * 60})
    assert event.name == "back_from_afk"
    assert event.variables["afk"] == "20min"


def test_short_afk_is_ignored(clock):
    t = Triggers(CONFIG, clock)
    t.on_afk({"status": "afk"})
    assert t.on_afk({"status": "present", "previous_seconds": 5 * 60}) is None


def test_present_without_transition_is_ignored(clock):
    # Ex.: grilo reiniciou e recebeu o pc/afk retido já como "present".
    t = Triggers(CONFIG, clock)
    assert t.on_afk({"status": "present", "previous_seconds": 3600}) is None
