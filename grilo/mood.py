"""Humor do grilo: três valores de 0 a 100 que sobem e descem com eventos
e voltam devagar para uma base (decaimento).

Estados derivados dos valores:
  normal     → tudo tranquilo
  irritado   → irritação acima do limite, orgulho baixo, mas a birra já passou
  retruque   → irritação acima do limite com orgulho alto: ele revida pesado
  emburrado  → passou do limite com orgulho baixo: fica X minutos no "hmpf"
"""

import logging
import time
from datetime import datetime

log = logging.getLogger("mood")

KEYS = ("irritacao", "orgulho", "energia")

NORMAL = "normal"
IRRITATED = "irritado"
COMEBACK = "retruque"
SULKING = "emburrado"


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


class Mood:
    def __init__(self, baseline: dict, decay_per_minute: float = 1.0,
                 irritation_limit: float = 80, pride_high: float = 60,
                 sulk_minutes: float = 10, clock=time.time):
        # clock é injetável: nos testes passamos um relógio falso e "avançamos o tempo".
        self.clock = clock
        self.baseline = {k: float(baseline.get(k, 50)) for k in KEYS}
        self.values = dict(self.baseline)
        self.decay_per_minute = decay_per_minute
        self.irritation_limit = irritation_limit
        self.pride_high = pride_high
        self.sulk_minutes = sulk_minutes
        self.sulking_until = 0.0
        self._last_decay = clock()

    @classmethod
    def from_config(cls, config: dict, clock=time.time) -> "Mood":
        return cls(
            baseline=config.get("baseline", {}),
            decay_per_minute=config.get("decay_per_minute", 1.0),
            irritation_limit=config.get("irritation_limit", 80),
            pride_high=config.get("pride_high", 60),
            sulk_minutes=config.get("sulk_minutes", 10),
            clock=clock,
        )

    def apply(self, deltas: dict, reason: str = "") -> None:
        """Soma os deltas (ex.: {"irritacao": 15}) e verifica se passou do limite."""
        if not deltas:
            return
        self.decay()  # primeiro desconta o tempo que passou, depois aplica o evento
        before = self.rounded()
        for key, delta in deltas.items():
            if key not in KEYS:
                log.warning("chave de humor desconhecida: %s", key)
                continue
            self.values[key] = _clamp(self.values[key] + delta)
        log.info("%s: %s → %s", reason or "evento", before, self.rounded())
        self._check_limit()

    def decay(self) -> None:
        """Aproxima cada valor da base, linearmente, sem passar dela."""
        now = self.clock()
        step = self.decay_per_minute * (now - self._last_decay) / 60
        self._last_decay = now
        for key in KEYS:
            base, value = self.baseline[key], self.values[key]
            if value > base:
                self.values[key] = max(base, value - step)
            else:
                self.values[key] = min(base, value + step)

    def _check_limit(self) -> None:
        irritation, pride = self.values["irritacao"], self.values["orgulho"]
        if irritation <= self.irritation_limit or self.is_sulking():
            return
        if pride >= self.pride_high:
            log.info("irritação %.0f > %g com orgulho alto (%.0f) → modo retruque",
                     irritation, self.irritation_limit, pride)
        else:
            self.sulking_until = self.clock() + self.sulk_minutes * 60
            log.info("irritação %.0f > %g com orgulho baixo (%.0f) → emburrado por %g min",
                     irritation, self.irritation_limit, pride, self.sulk_minutes)

    def is_sulking(self) -> bool:
        return self.clock() < self.sulking_until

    def state(self) -> str:
        if self.is_sulking():
            return SULKING
        if self.values["irritacao"] > self.irritation_limit:
            return COMEBACK if self.values["orgulho"] >= self.pride_high else IRRITATED
        return NORMAL

    def rounded(self) -> dict:
        return {k: round(v) for k, v in self.values.items()}

    def snapshot(self) -> dict:
        """O que vai para npc/grilo/humor."""
        sulking_until = None
        if self.is_sulking():
            sulking_until = datetime.fromtimestamp(self.sulking_until).isoformat(timespec="seconds")
        return {**self.rounded(), "estado": self.state(), "emburrado_ate": sulking_until}
