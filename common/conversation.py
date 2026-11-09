"""Regras para um NPC responder a outro sem virar uma conversa infinita.

Cada fala publicada leva no meta:
  to     → com quem está falando ("joao" ou o id de um NPC)
  depth  → quantas falas de NPC já estão encadeadas (0 = começou com o João
           ou com um gatilho; cada resposta a um NPC soma 1)

Um NPC responde a outro quando:
  - a fala não é dele mesmo;
  - a cadeia ainda não chegou em max_depth (é isso que impede o loop infinito:
    grilo → hefesto → grilo → ... para em algum momento, garantido);
  - falaram com ele (to = ele, ou citaram o nome dele) → responde sempre;
  - senão, sorteia pela chance do humor atual (irritado → mais vontade de
    meter o bedelho), respeitando um cooldown por personagem.
"""

import random
import time

from common.text import normalize


class ConversationPolicy:
    def __init__(self, my_id: str, my_names: list[str], react_chance: dict,
                 max_depth: int = 3, cooldown_seconds: float = 60,
                 clock=time.time, rng: random.Random | None = None):
        self.my_id = my_id
        self.my_names = [normalize(n) for n in my_names]
        self.react_chance = react_chance
        self.max_depth = max_depth
        self.cooldown = cooldown_seconds
        self.clock = clock
        self.rng = rng or random.Random()
        self._last_reaction: dict[str, float] = {}

    def should_react(self, sender: str, meta: dict, text: str,
                     mood_state: str) -> tuple[bool, str]:
        """Devolve (responder?, motivo para o log)."""
        if sender == self.my_id:
            return False, "fala minha"
        depth = int(meta.get("depth") or 0)
        if depth >= self.max_depth:
            return False, f"conversa entre NPCs já tem {depth} falas encadeadas (máx. {self.max_depth})"

        if meta.get("to") == self.my_id or self._mentions_me(text):
            return True, "falaram comigo"

        last = self._last_reaction.get(sender)
        if last is not None and self.clock() - last < self.cooldown:
            return False, f"respondi ao {sender} há pouco (cooldown de {self.cooldown:g}s)"

        chance = self.react_chance.get(mood_state, 0)
        if self.rng.random() < chance:
            return True, f"sorteio ({chance:.0%} no estado {mood_state})"
        return False, f"não quis ({chance:.0%} no estado {mood_state})"

    def mark(self, sender: str) -> None:
        self._last_reaction[sender] = self.clock()

    def _mentions_me(self, text: str) -> bool:
        norm = normalize(text)
        return any(name and name in norm for name in self.my_names)
