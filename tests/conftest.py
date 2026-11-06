from datetime import datetime

import pytest


class FakeClock:
    """Relógio controlável: clock() devolve o "agora", advance() pula o tempo."""

    def __init__(self, start: float):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, minutes: float = 0, seconds: float = 0) -> None:
        self.now += minutes * 60 + seconds


@pytest.fixture
def clock():
    # 14:00 no horário local: longe da faixa de madrugada
    return FakeClock(datetime(2026, 11, 6, 14, 0).timestamp())
