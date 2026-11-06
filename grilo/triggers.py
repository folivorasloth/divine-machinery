"""Gatilhos de atividade: quando o grilo comenta por conta própria.

Cada gatilho tem um cooldown próprio, e há um limite global de falas
espontâneas por hora. A cada atualização dispara no máximo UM gatilho
(o primeiro da lista de prioridade que estiver liberado).
"""

import logging
import time
from collections import deque
from datetime import datetime
from datetime import time as dtime

from grilo.brain import TriggerEvent
from grilo.fallback import format_duration, normalize

log = logging.getLogger("triggers")


def _parse_hhmm(value: str) -> dtime:
    hours, minutes = str(value).split(":")
    return dtime(int(hours), int(minutes))


def in_time_window(now: dtime, start: dtime, end: dtime) -> bool:
    """Funciona também quando a faixa vira a meia-noite (ex.: 23:00 → 05:00)."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


class Triggers:
    def __init__(self, config: dict, clock=time.time):
        self.config = config
        self.clock = clock
        self.max_per_hour = config.get("global_max_per_hour", 6)
        self._last_fired: dict[str, float] = {}
        self._recent: deque[float] = deque()  # horários das últimas falas espontâneas
        self._afk_status: str | None = None

    # --- entradas ------------------------------------------------------------

    def on_activity(self, activity: dict) -> TriggerEvent | None:
        if activity.get("afk"):
            return None
        app = activity.get("app") or "?"
        title = activity.get("title") or ""
        seconds = activity.get("app_seconds") or 0

        candidates = []  # ordem = prioridade

        target = self._match_distraction(app, title)
        if target:
            candidates.append(TriggerEvent(
                "distraction",
                f"João abriu {target} ({app}: \"{title}\"), que está na lista de distrações dele",
                {"target": target},
            ))

        hours = self._cfg("long_session").get("hours")
        if hours is not None and seconds >= hours * 3600:
            candidates.append(TriggerEvent(
                "long_session",
                f"João está há {format_duration(seconds)} seguidos no {app}",
            ))

        if self._is_late_night():
            now = datetime.fromtimestamp(self.clock())
            candidates.append(TriggerEvent(
                "late_night",
                f"São {now:%H:%M} da madrugada e o João continua no computador ({app})",
            ))

        return self._first_allowed(candidates)

    def on_afk(self, afk: dict) -> TriggerEvent | None:
        status = afk.get("status")
        previous, self._afk_status = self._afk_status, status
        # Só interessa a transição ausente → presente.
        if not (previous == "afk" and status == "present"):
            return None
        away = afk.get("previous_seconds") or 0
        min_minutes = self._cfg("back_from_afk").get("min_afk_minutes")
        if min_minutes is None or away < min_minutes * 60:
            return None
        return self._first_allowed([TriggerEvent(
            "back_from_afk",
            f"João voltou depois de {format_duration(away)} longe do computador",
            {"afk": format_duration(away)},
        )])

    # --- regras --------------------------------------------------------------

    def _cfg(self, name: str) -> dict:
        return self.config.get(name) or {}

    def _match_distraction(self, app: str, title: str) -> str | None:
        haystack = normalize(f"{app} {title}")
        for item in self._cfg("distraction").get("match") or []:
            if normalize(item) in haystack:
                return item
        return None

    def _is_late_night(self) -> bool:
        cfg = self._cfg("late_night")
        if "start" not in cfg or "end" not in cfg:
            return False
        now = datetime.fromtimestamp(self.clock()).time()
        return in_time_window(now, _parse_hhmm(cfg["start"]), _parse_hhmm(cfg["end"]))

    def _first_allowed(self, candidates: list[TriggerEvent]) -> TriggerEvent | None:
        for event in candidates:
            if self._allowed(event.name):
                self._mark(event.name)
                log.info("gatilho %s disparou: %s", event.name, event.description)
                return event
        return None

    def _allowed(self, name: str) -> bool:
        now = self.clock()
        # debug, não info: a atividade chega a cada 30s e isso lotaria o log
        cooldown = self._cfg(name).get("cooldown_minutes", 30) * 60
        last = self._last_fired.get(name)
        if last is not None and now - last < cooldown:
            log.debug("gatilho %s em cooldown (%ds restantes)", name, cooldown - (now - last))
            return False

        while self._recent and now - self._recent[0] >= 3600:
            self._recent.popleft()
        if len(self._recent) >= self.max_per_hour:
            log.debug("gatilho %s bloqueado: limite global de %d/h", name, self.max_per_hour)
            return False
        return True

    def _mark(self, name: str) -> None:
        now = self.clock()
        self._last_fired[name] = now
        self._recent.append(now)
