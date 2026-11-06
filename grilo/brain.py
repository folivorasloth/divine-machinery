"""O cérebro do grilo: decide entre silêncio, LLM ou fallback, e explica o porquê.

Ordem de decisão:
  1. emburrado?            → "hmpf" ou silêncio
  2. tenta o LLM           → prompt com persona + humor + atividade + memória curta
  3. LLM falhou?           → template por categoria (ou "retruque", se for o caso)
"""

import random
import re
from dataclasses import dataclass, field
from datetime import datetime

from grilo.fallback import Fallback
from grilo.mood import COMEBACK, SULKING, Mood


@dataclass
class Decision:
    text: str | None   # None = ficar calado
    source: str        # "llm" | "fallback" | "emburrado" | "silencio"
    reason: str        # vai para o log, em português


@dataclass
class TriggerEvent:
    """Um gatilho de atividade que quer virar comentário espontâneo."""
    name: str          # ex.: "long_session" (também é a chave dos templates)
    description: str   # explicação para o LLM
    variables: dict = field(default_factory=dict)  # extras para os templates


class Brain:
    def __init__(self, persona: dict, llm, fallback: Fallback, rng: random.Random | None = None):
        self.persona = persona
        self.llm = llm
        self.fallback = fallback
        self.rng = rng or random.Random()
        self.sulk_silence_chance = persona.get("sulk_silence_chance", 0.5)

    def reply(self, user_text: str, category: str, mood: Mood, history: list[dict],
              activity_summary: str, variables: dict) -> Decision:
        """Resposta a algo que o João disse."""
        state = mood.state()
        if state == SULKING:
            if self.rng.random() < self.sulk_silence_chance:
                return Decision(None, "silencio", "emburrado → ignorou")
            text = self.fallback.pick("emburrado", variables) or "hmpf."
            return Decision(text, "emburrado", "emburrado → hmpf")

        messages = self._build_messages(mood, history, activity_summary, user_text)
        text = self.llm.chat(messages)
        if text:
            return Decision(_clean(text), "llm", f"LLM respondeu (categoria {category}, estado {state})")

        key = "retruque" if state == COMEBACK else category
        text = self.fallback.reply(key, variables, llm_down=True)
        return Decision(text, "fallback", f"LLM {self.llm.last_error} → fallback categoria {key}")

    def spontaneous(self, event: TriggerEvent, mood: Mood, history: list[dict],
                    activity_summary: str, variables: dict) -> Decision:
        """Comentário por conta própria, disparado por um gatilho de atividade."""
        if mood.is_sulking():
            return Decision(None, "silencio", f"gatilho {event.name}, mas está emburrado")

        # Vai como mensagem de "user", mas marcada como evento, para o modelo
        # entender que não foi o João quem falou.
        instruction = (
            f"[EVENTO — não é fala do João] {event.description}. "
            "Faça um comentário espontâneo e curto sobre isso, direto para o João."
        )
        text = self.llm.chat(self._build_messages(mood, history, activity_summary, instruction))
        if text:
            return Decision(_clean(text), "llm", f"gatilho {event.name} → LLM")

        text = self.fallback.pick(event.name, variables)
        if not text:
            return Decision(None, "silencio", f"gatilho {event.name}: LLM {self.llm.last_error} e sem template")
        return Decision(text, "fallback", f"gatilho {event.name}: LLM {self.llm.last_error} → template")

    def _build_messages(self, mood: Mood, history: list[dict], activity_summary: str,
                        user_content: str) -> list[dict]:
        persona = self.persona
        parts = [persona.get("system_prompt", "").strip()]

        examples = persona.get("examples") or []
        if examples:
            lines = "\n".join(f"- {e}" for e in examples)
            parts.append(f"Exemplos do seu tom (referência, NÃO copie):\n{lines}")

        values = mood.rounded()
        parts.append(
            f"Seu humor agora (0 a 100): irritação {values['irritacao']}, "
            f"orgulho {values['orgulho']}, energia {values['energia']}."
        )
        state_instruction = (persona.get("mood_instructions") or {}).get(mood.state())
        if state_instruction:
            parts.append(state_instruction)
        if values["energia"] < persona.get("low_energy_below", 25):
            parts.append(persona.get("low_energy_instruction", ""))

        parts.append(f"Agora são {datetime.fromtimestamp(mood.clock()):%H:%M}.")
        if activity_summary:
            parts.append(f"O que o João está fazendo no computador: {activity_summary}")

        system = "\n\n".join(p for p in parts if p)
        return [{"role": "system", "content": system}, *history,
                {"role": "user", "content": user_content}]


def _clean(text: str) -> str:
    """Modelos pequenos às vezes respondem '"Grilo: blá"'. Tira esses enfeites."""
    text = text.strip()
    text = re.sub(r"^(grilo( falante)?\s*:\s*)", "", text, flags=re.IGNORECASE)
    return text.strip().strip('"“”').strip()
