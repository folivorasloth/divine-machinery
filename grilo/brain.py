"""O cérebro do grilo: decide o que dizer e explica o porquê no log.

Ordem de decisão para cada fala:
  1. emburrado?        → "hmpf" ou silêncio
  2. sorteia um MODO   → debochado | direto | filosofico (pesos por humor, na persona)
  3. modo filosófico?  → busca trechos dos livros (biblioteca) para pôr no prompt
  4. tenta o LLM       → prompt = persona + humor + modo + memória + trechos + idioma
  5. LLM falhou?       → template do modo/categoria (ou uma citação curada, no filosófico)

Memória de fontes: quando uma fala vem de um livro ou de uma citação, a origem
fica registrada (Decision.citation). O prompt seguinte leva essa memória, então
"de quem é essa frase?" tem resposta sem você repetir a frase — e sem o LLM
precisar adivinhar.
"""

import random
import re
from dataclasses import dataclass, field
from datetime import datetime

from grilo.fallback import Fallback
from grilo.library import Library, Passage
from grilo.mood import COMEBACK, SULKING, Mood

DEBOCHADO = "debochado"
DIRETO = "direto"
FILOSOFICO = "filosofico"


@dataclass
class Decision:
    text: str | None   # None = ficar calado
    source: str        # "llm" | "fallback" | "emburrado" | "silencio"
    reason: str        # vai para o log, em português
    mode: str = ""
    citation: dict | None = None  # {"author", "work"} ou {"consulted": [...]}


@dataclass
class TriggerEvent:
    """Um gatilho de atividade que quer virar comentário espontâneo."""
    name: str          # ex.: "long_session" (também é a chave dos templates)
    description: str   # explicação para o LLM
    variables: dict = field(default_factory=dict)  # extras para os templates


@dataclass
class Context:
    """Tudo que o cérebro precisa saber sobre o momento para responder."""
    mood: Mood
    history: list[dict] = field(default_factory=list)   # memória curta (mensagens)
    activity: str = ""                                  # resumo da atividade no PC
    variables: dict = field(default_factory=dict)       # variáveis dos templates
    lang: str = "pt"
    sources: list[dict] = field(default_factory=list)   # memória de fontes
    cast: str = ""                                      # quem são os outros personagens


class Brain:
    def __init__(self, persona: dict, llm, fallback: Fallback, library: Library | None = None,
                 philosophy: dict | None = None, rng: random.Random | None = None):
        self.persona = persona
        self.llm = llm
        self.fallback = fallback
        self.library = library
        philosophy = philosophy or {}
        self.passages_k = philosophy.get("passages", 3)
        self.philo_timeout = philosophy.get("timeout_seconds", 20)
        self.philo_max_tokens = philosophy.get("max_tokens", 250)
        self.rng = rng or random.Random()
        self.sulk_silence_chance = persona.get("sulk_silence_chance", 0.5)

    # --- decisões ------------------------------------------------------------

    def choose_mode(self, category: str | None, state: str) -> tuple[str, str]:
        """Sorteia o modo da resposta. Devolve (modo, motivo para o log)."""
        if category == "filosofia":
            return FILOSOFICO, "assunto filosófico"
        if category == "fonte":
            return DIRETO, "pergunta sobre a fonte de uma fala"
        table = self.persona.get("response_modes") or {}
        weights = table.get(state) or table.get("normal") or {DEBOCHADO: 1}
        modes, values = zip(*weights.items())
        mode = self.rng.choices(modes, weights=values)[0]
        return mode, f"sorteio no estado {state} ({weights})"

    def reply(self, user_text: str, category: str, ctx: Context) -> Decision:
        """Resposta a algo que o João disse."""
        sulk = self._sulk(ctx)
        if sulk:
            return sulk
        state = ctx.mood.state()
        mode, why = self.choose_mode(category, state)
        query = " ".join([m["content"] for m in ctx.history[-2:]] + [user_text])
        text, citation, passages = self._generate(ctx, user_text, mode, query)
        if text:
            return Decision(text, "llm", self._why("LLM", mode, why, category, ctx, passages),
                            mode, citation)

        if category == "fonte":
            text, citation = self._source_answer(ctx)
        elif mode == FILOSOFICO:
            text, citation = self._quote(ctx)
        elif mode == DIRETO:
            text, citation = self.fallback.pick(DIRETO, ctx.variables, ctx.lang), None
        else:
            key = "retruque" if state == COMEBACK else category
            text, citation = self.fallback.reply(key, ctx.variables, ctx.lang), None
        text = text or self.fallback.reply("generico", ctx.variables, ctx.lang)
        return Decision(text, "fallback",
                        f"LLM {self.llm.last_error} → fallback, modo {mode} ({why}), "
                        f"categoria {category}, idioma {ctx.lang}", mode, citation)

    def spontaneous(self, event: TriggerEvent, ctx: Context) -> Decision:
        """Comentário por conta própria, disparado por um gatilho de atividade."""
        if ctx.mood.is_sulking():
            return Decision(None, "silencio", f"gatilho {event.name}, mas está emburrado")

        # Vai como mensagem de "user", mas marcada como evento, para o modelo
        # entender que não foi o João quem falou.
        instruction = (
            f"[EVENTO — não é fala do João] {event.description}. "
            "Faça um comentário espontâneo e curto sobre isso, direto para o João."
        )
        text, _, _ = self._generate(ctx, instruction, DEBOCHADO, "")
        if text:
            return Decision(text, "llm", f"gatilho {event.name} → LLM", DEBOCHADO)

        text = self.fallback.pick(event.name, ctx.variables, ctx.lang)
        if not text:
            return Decision(None, "silencio",
                            f"gatilho {event.name}: LLM {self.llm.last_error} e sem template")
        return Decision(text, "fallback",
                        f"gatilho {event.name}: LLM {self.llm.last_error} → template", DEBOCHADO)

    def react_to_npc(self, npc_name: str, npc_text: str, ctx: Context) -> Decision:
        """Resposta ao que outro personagem disse."""
        sulk = self._sulk(ctx)
        if sulk:
            return sulk
        mode, why = self.choose_mode(None, ctx.mood.state())
        instruction = (
            f"[{npc_name} acabou de dizer: \"{npc_text}\"] Reaja a isso agora. "
            f"Você pode falar diretamente com {npc_name} ou comentar com o João sobre ele."
        )
        text, citation, passages = self._generate(ctx, instruction, mode, npc_text)
        if text:
            return Decision(text, "llm", self._why(f"reação a {npc_name} → LLM", mode, why,
                                                   None, ctx, passages), mode, citation)

        variables = {**ctx.variables, "npc": npc_name}
        if mode == FILOSOFICO:
            text, citation = self._quote(ctx)
        else:
            text = self.fallback.pick("npc_reacao", variables, ctx.lang)
            citation = None
        return Decision(text, "fallback" if text else "silencio",
                        f"reação a {npc_name}: LLM {self.llm.last_error} → fallback modo {mode}",
                        mode, citation)

    # --- partes ----------------------------------------------------------------

    def _sulk(self, ctx: Context) -> Decision | None:
        if ctx.mood.state() != SULKING:
            return None
        if self.rng.random() < self.sulk_silence_chance:
            return Decision(None, "silencio", "emburrado → ignorou")
        text = self.fallback.pick("emburrado", ctx.variables, ctx.lang) or "hmpf."
        return Decision(text, "emburrado", "emburrado → hmpf")

    def _generate(self, ctx: Context, content: str, mode: str,
                  query: str) -> tuple[str | None, dict | None, list[Passage]]:
        """Chama o LLM. Devolve (texto, citação, trechos usados no prompt)."""
        passages = self._passages(query) if mode == FILOSOFICO else []
        messages = self._build_messages(ctx, content, mode, passages)
        if mode == FILOSOFICO:
            text = self.llm.chat(messages, timeout=self.philo_timeout,
                                 max_tokens=self.philo_max_tokens)
        else:
            text = self.llm.chat(messages)
        if not text:
            return None, None, passages
        text, citation = extract_citation(clean(text), passages)
        return text, citation, passages

    def _passages(self, query: str) -> list[Passage]:
        if not self.library or self.library.empty or not query.strip():
            return []
        vectors = self.llm.embed([query])
        if not vectors:
            return []
        return self.library.search(vectors[0], self.passages_k)

    def _quote(self, ctx: Context) -> tuple[str | None, dict | None]:
        """Sem LLM, o modo filosófico usa uma citação curada da persona."""
        quote = self.fallback.quote(ctx.lang)
        if not quote:
            return self.fallback.pick(DIRETO, ctx.variables, ctx.lang), None
        variables = {**ctx.variables, "quote": quote["texto"], "author": quote["autor"],
                     "work": quote.get("obra", "")}
        text = self.fallback.pick(FILOSOFICO, variables, ctx.lang) or f"\"{quote['texto']}\""
        return text, {"author": quote["autor"], "work": quote.get("obra", "")}

    def _source_answer(self, ctx: Context) -> tuple[str | None, None]:
        """Sem LLM: responde "de quem é essa frase?" direto da memória de fontes."""
        last = ctx.sources[-1] if ctx.sources else None
        if last and last.get("author"):
            variables = {**ctx.variables, "author": last["author"], "work": last.get("work") or "?"}
            return self.fallback.pick("fonte", variables, ctx.lang), None
        if last and last.get("consulted"):
            variables = {**ctx.variables, "author": " / ".join(last["consulted"])}
            return self.fallback.pick("fonte_provavel", variables, ctx.lang), None
        return self.fallback.pick("fonte_desconhecida", ctx.variables, ctx.lang), None

    def _why(self, prefix: str, mode: str, why: str, category: str | None,
             ctx: Context, passages: list[Passage]) -> str:
        parts = [prefix, f"modo {mode} ({why})"]
        if category:
            parts.append(f"categoria {category}")
        parts.append(f"idioma {ctx.lang}")
        if passages:
            parts.append("trechos: " + "; ".join(_unique_labels(passages)))
        return ", ".join(parts)

    # --- prompt ----------------------------------------------------------------

    def _build_messages(self, ctx: Context, user_content: str, mode: str = DEBOCHADO,
                        passages: list[Passage] | None = None) -> list[dict]:
        persona = self.persona
        mood = ctx.mood
        parts = [persona.get("system_prompt", "").strip()]

        examples = persona.get("examples") or []
        if examples:
            lines = "\n".join(f"- {e}" for e in examples)
            parts.append(f"Exemplos do seu tom (referência, NÃO copie):\n{lines}")

        if ctx.cast:
            parts.append(f"Os outros personagens desta casa:\n{ctx.cast}\n"
                         f"{persona.get('about_others', '')}\n"
                         "No histórico, mensagens que começam com [Nome] são desses "
                         "personagens, não do João.")

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
        parts.append((persona.get("mode_instructions") or {}).get(mode, ""))

        parts.append(f"Agora são {datetime.fromtimestamp(mood.clock()):%H:%M}.")
        if ctx.activity:
            parts.append(f"O que o João está fazendo no computador: {ctx.activity}")
        if ctx.sources:
            parts.append(render_sources(ctx.sources))
        if passages:
            parts.append(render_passages(passages))
        # Por último de propósito: a instrução final é a que o modelo mais respeita.
        parts.append((persona.get("language_instructions") or {}).get(ctx.lang, ""))

        system = "\n\n".join(p for p in parts if p)
        return [{"role": "system", "content": system}, *ctx.history,
                {"role": "user", "content": user_content}]


# --- funções auxiliares (sem estado, fáceis de testar) ------------------------------

def render_passages(passages: list[Passage]) -> str:
    lines = [
        "Trechos da sua biblioteca de filosofia que podem ajudar. Use só se fizer sentido. "
        "Se usar um deles, mencione o autor na resposta e termine com o número do trecho "
        "entre colchetes duplos, por exemplo [[2]]. Nunca atribua a um autor algo que não "
        "está nos trechos ou que você não tem certeza de que ele disse."
    ]
    for i, p in enumerate(passages, start=1):
        lines.append(f"[{i}] {p.label()}: \"{p.text}\"")
    return "\n".join(lines)


def render_sources(sources: list[dict]) -> str:
    lines = [
        "Memória das suas falas recentes e de onde vieram. Se o João perguntar de quem é "
        "uma frase ou de onde você tirou algo, responda com base nisto. Se a fala não estiver "
        "aqui, ela é sua; se não tiver certeza, diga que não sabe. Nunca invente autor ou livro."
    ]
    for s in sources:
        line = s["line"] if len(s["line"]) <= 140 else s["line"][:137] + "..."
        if s.get("author"):
            origin = f"{s['author']}, {s['work']}" if s.get("work") else s["author"]
        else:
            origin = "baseada em trechos de: " + "; ".join(s.get("consulted", []))
        lines.append(f"- \"{line}\" → {origin}")
    return "\n".join(lines)


_CITATION = re.compile(r"\s*\[{1,2}\s*(\d+)\s*\]{1,2}")


def extract_citation(text: str, passages: list[Passage]) -> tuple[str, dict | None]:
    """Tira as marcas [[N]] do texto e diz de qual trecho a fala veio.

    Se o modelo não marcou nada mas recebeu trechos, registra os trechos
    consultados: é o melhor que dá para afirmar sem inventar.
    """
    numbers = [int(n) for n in _CITATION.findall(text)]
    text = _CITATION.sub("", text).strip()
    for n in numbers:
        if 1 <= n <= len(passages):
            p = passages[n - 1]
            return text, {"author": p.author, "work": p.work}
    if passages:
        return text, {"consulted": _unique_labels(passages)}
    return text, None


def _unique_labels(passages: list[Passage]) -> list[str]:
    """Vários trechos do mesmo livro viram uma entrada só."""
    return list(dict.fromkeys(p.label() for p in passages))


def clean(text: str) -> str:
    """Modelos pequenos às vezes respondem '"Grilo: blá"'. Tira esses enfeites."""
    text = text.strip()
    text = re.sub(r"^(grilo( falante)?\s*:\s*)", "", text, flags=re.IGNORECASE)
    return text.strip().strip('"“”').strip()
