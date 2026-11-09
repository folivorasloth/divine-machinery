"""Processo principal do Grilo Falante.

    python -m grilo.main              # normal: MQTT + Ollama (no homelab, via Docker)
    python -m grilo.main --offline    # tudo local, sem MQTT nem Ollama, chat no terminal
    python -m grilo.main -v           # logs de debug (mostra cooldowns etc.)

Como funciona: o paho entrega as mensagens numa thread de rede própria.
Lá só enfileiramos (self.inbox); quem processa é o loop principal. Assim uma
chamada lenta ao Ollama (até 20s no modo filosófico) não trava a conexão com o broker.
"""

import argparse
import json
import logging
import queue
import random
import signal
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from common.bus import FakeBus, MqttBus
from common.config import load_config, load_yaml, resolve_path
from common.conversation import ConversationPolicy
from common.language import detect_language
from common.messages import (ACTIVITY, AFK, ALL_NPC_SPEECH, USER_SPEECH, make_message,
                             npc_topic, parse_json, parse_message)
from common.tts import Speaker
from grilo.brain import Brain, Context, Decision, TriggerEvent
from grilo.fallback import Fallback, format_duration
from grilo.library import Library
from grilo.llm import NoLLM, OllamaClient
from grilo.mood import Mood
from grilo.triggers import Triggers

NPC_ID = "grilo"
log = logging.getLogger(NPC_ID)


class Grilo:
    def __init__(self, config: dict, bus, llm, clock=time.time, rng: random.Random | None = None,
                 library: Library | None = None):
        settings = config.get("grilo", {})
        self.persona = load_yaml(settings.get("persona_file", "persona/grilo.yaml"))
        self.cast = _load_cast(settings.get("cast_file", "persona/cast.yaml"))
        self.bus = bus
        self.clock = clock
        self.mood = Mood.from_config(config.get("mood", {}), clock)
        self.triggers = Triggers(config.get("triggers", {}), clock)
        self.fallback = Fallback(self.persona, rng)
        self.brain = Brain(self.persona, llm, self.fallback, library,
                           config.get("philosophy", {}), rng)
        conversation = config.get("conversation", {})
        self.policy = ConversationPolicy(
            NPC_ID, ["grilo", self.persona.get("name", "")],
            self.persona.get("npc_reaction_chance", {}),
            max_depth=conversation.get("max_depth", 3),
            cooldown_seconds=conversation.get("cooldown_seconds", 60),
            clock=clock, rng=rng,
        )
        self.effects = self.persona.get("mood_effects", {})
        self.tick_seconds = settings.get("tick_seconds", 5)

        # Memória curta: as últimas mensagens da conversa (João, grilo e outros NPCs)
        self.history: deque[dict] = deque(maxlen=settings.get("short_memory", 20))
        # Memória de fontes: de onde vieram as últimas falas citadas
        self.sources: deque[dict] = deque(maxlen=settings.get("source_memory", 5))
        self.recent_apps: deque[str] = deque(maxlen=settings.get("recent_activity", 5))
        self.activity: dict | None = None
        self.afk: dict | None = None
        # Idioma da conversa: muda quando o João muda; os comentários
        # espontâneos usam o último idioma que ele falou.
        self.lang: str = self.persona.get("default_language", "pt")

        self.inbox: queue.Queue[tuple[str, str]] = queue.Queue()
        self._last_mood: dict | None = None

    # --- ciclo de vida -------------------------------------------------------

    def start(self) -> None:
        for topic in (USER_SPEECH, ACTIVITY, AFK, ALL_NPC_SPEECH):
            self.bus.subscribe(topic, self._enqueue)
        self.bus.start()
        self.publish_mood(force=True)

    def run_forever(self) -> None:
        log.info("grilo acordado")
        while True:
            try:
                topic, payload = self.inbox.get(timeout=self.tick_seconds)
            except queue.Empty:
                pass
            else:
                self.handle(topic, payload)
            self.tick()

    def process_pending(self) -> None:
        """Processa o que está na fila e volta (usado no modo --offline)."""
        while not self.inbox.empty():
            self.handle(*self.inbox.get_nowait())
        self.tick()

    def tick(self) -> None:
        self.mood.decay()
        self.publish_mood()

    # --- entrada -------------------------------------------------------------

    def _enqueue(self, topic: str, payload: str) -> None:
        # Roda na thread do MQTT: só enfileira, não processa nada aqui.
        self.inbox.put((topic, payload))

    def handle(self, topic: str, payload: str) -> None:
        if topic == USER_SPEECH:
            self._on_user_speech(payload)
        elif topic == ACTIVITY:
            self._on_activity(parse_json(payload))
        elif topic == AFK:
            self._on_afk(parse_json(payload))
        elif topic.startswith("npc/") and topic.endswith("/fala"):
            self._on_npc_speech(topic.split("/")[1], payload)

    def _on_user_speech(self, payload: str) -> None:
        text = parse_message(payload)["text"].strip()
        if not text:
            return
        log.info("João disse: %s", text)
        lang = detect_language(text, self.lang)
        if lang != self.lang:
            log.info("idioma: %s → %s", self.lang, lang)
            self.lang = lang
        category = self.fallback.category(text)
        self.mood.apply(self.effects.get(category, {}), f"fala do João ({category})")
        decision = self.brain.reply(text, category, self._context())
        self.history.append({"role": "user", "content": text})
        self._say(decision)

    def _on_npc_speech(self, sender: str, payload: str) -> None:
        if sender == NPC_ID:
            return  # é o eco da minha própria fala
        msg = parse_message(payload)
        text, meta = msg["text"].strip(), msg.get("meta") or {}
        if not text:
            return
        name = self._name_of(sender)
        target = self._name_of(meta.get("to") or "joao")
        log.info("%s disse (para %s): %s", name, target, text)

        react, why = self.policy.should_react(sender, meta, text, self.mood.state())
        decision = None
        if react:
            log.info("vou responder ao %s: %s", name, why)
            self.policy.mark(sender)
            self.mood.apply(self.effects.get("npc_fala", {}), f"fala do {name}")
            ctx = self._context()
            # Responde no idioma em que o outro NPC falou (sem mudar o idioma da conversa com o João)
            ctx.lang = detect_language(text, self.lang)
            decision = self.brain.react_to_npc(name, text, ctx)
        else:
            log.info("não vou responder ao %s: %s", name, why)

        # Entra na memória curta de qualquer jeito: o grilo "ouviu" e pode
        # comentar depois, quando o João perguntar.
        self.history.append({"role": "user", "content": f"[{name}, para {target}]: {text}"})
        if decision:
            self._say(decision, to=sender, depth=int(meta.get("depth") or 0) + 1)

    def _on_activity(self, data: dict | None) -> None:
        if not data:
            return
        previous_app = self.activity.get("app") if self.activity else None
        self.activity = data
        if data.get("app") != previous_app and not data.get("afk"):
            self.recent_apps.append(f"{datetime.fromtimestamp(self.clock()):%H:%M} {data.get('app')}")
        event = self.triggers.on_activity(data)
        if event:
            self._spontaneous(event)

    def _on_afk(self, data: dict | None) -> None:
        if not data:
            return
        self.afk = data
        event = self.triggers.on_afk(data)
        if event:
            self._spontaneous(event)

    # --- saída ---------------------------------------------------------------

    def _spontaneous(self, event: TriggerEvent) -> None:
        self.mood.apply(self.effects.get(event.name, {}), f"gatilho {event.name}")
        ctx = self._context()
        ctx.variables.update(event.variables)
        self._say(self.brain.spontaneous(event, ctx), trigger=event.name)

    def _say(self, decision: Decision, trigger: str | None = None,
             to: str = "joao", depth: int = 0) -> None:
        log.info("decisão [%s]: %s", decision.source, decision.reason)
        if decision.text is None:
            return
        self.history.append({"role": "assistant", "content": decision.text})
        if decision.citation:
            self.sources.append({"line": decision.text, **decision.citation})
            log.info("fonte registrada: %s", decision.citation)
        self.bus.publish(
            npc_topic(NPC_ID, "fala"),
            make_message(NPC_ID, decision.text, source=decision.source,
                         reason=decision.reason, trigger=trigger, mood=self.mood.state(),
                         mode=decision.mode, lang=self.lang, to=to, depth=depth,
                         citation=decision.citation),
        )
        self.publish_mood()

    def publish_mood(self, force: bool = False) -> None:
        # Retido: quem conectar depois (uma tela, outro NPC) já recebe o humor atual.
        snapshot = self.mood.snapshot()
        if force or snapshot != self._last_mood:
            self._last_mood = snapshot
            self.bus.publish(npc_topic(NPC_ID, "humor"),
                             json.dumps(snapshot, ensure_ascii=False), retain=True)

    # --- contexto ------------------------------------------------------------

    def _context(self) -> Context:
        return Context(
            mood=self.mood,
            history=list(self.history),
            activity=self._activity_summary(),
            variables=self._variables(),
            lang=self.lang,
            sources=list(self.sources),
            cast=self._cast_summary(),
        )

    def _variables(self) -> dict:
        """Variáveis dos templates do fallback."""
        activity = self.activity or {}
        return {
            "time": f"{datetime.fromtimestamp(self.clock()):%H:%M}",
            "app": activity.get("app") or "computador",
            "title": activity.get("title") or "",
            "session": format_duration(activity.get("app_seconds") or 0),
        }

    def _activity_summary(self) -> str:
        """Resumo em texto para o prompt do LLM."""
        if self.afk and self.afk.get("status") == "afk":
            return f"está longe do computador (AFK) há {format_duration(self.afk.get('seconds'))}."
        if not self.activity:
            return ""
        a = self.activity
        summary = (f"agora no {a.get('app')} (janela \"{a.get('title')}\") "
                   f"há {format_duration(a.get('app_seconds'))}.")
        if len(self.recent_apps) > 1:
            summary += " Apps recentes: " + ", ".join(self.recent_apps) + "."
        return summary

    def _name_of(self, npc_id: str) -> str:
        return (self.cast.get(npc_id) or {}).get("name") or npc_id.capitalize()

    def _cast_summary(self) -> str:
        """Os outros personagens (o grilo não precisa se descrever para si mesmo)."""
        return "\n".join(f"- {c.get('name', i)}: {c.get('description', '')}"
                         for i, c in self.cast.items() if i != NPC_ID)


def _load_cast(path: str) -> dict:
    """persona/cast.yaml: quem é quem no Divine Machinery."""
    if not resolve_path(path).exists():
        return {}
    return load_yaml(path) or {}


def _load_library(config: dict) -> Library:
    library_dir = config.get("philosophy", {}).get("library_dir", "library")
    return Library(Path(resolve_path(library_dir)) / "index")


# --- modos de execução ---------------------------------------------------------

def run_online(config: dict) -> None:
    mqtt_cfg = config.get("mqtt", {})
    bus = MqttBus(mqtt_cfg.get("host", "localhost"), mqtt_cfg.get("port", 1883),
                  client_id="dm-grilo", status_topic=npc_topic(NPC_ID, "status"))
    grilo = Grilo(config, bus, OllamaClient.from_config(config["ollama"]),
                  library=_load_library(config))
    # docker stop manda SIGTERM: viramos isso numa saída normal para cair no finally
    # e publicar "offline" na hora (sem esperar o Last Will).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    grilo.start()
    try:
        grilo.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()
        log.info("grilo dormindo")


OFFLINE_HELP = """\
Modo offline: sem MQTT, sem Ollama. Só personalidade e fallback.
Comandos:
  /humor                  mostra o humor atual
  /fontes                 mostra a memória de fontes
  /app <nome> [minutos]   simula estar há N minutos num app (testa gatilhos)
  /afk <minutos>          simula voltar depois de N minutos fora
  /npc <id> <texto>       simula outro personagem falando (ex.: /npc hefesto bom dia)
  /sair                   sai
"""


def run_offline(config: dict) -> None:
    bus = FakeBus(status_topic=npc_topic(NPC_ID, "status"))
    grilo = Grilo(config, bus, NoLLM())
    speaker = Speaker(config.get("tts", {}))

    def show(topic: str, payload: str) -> None:
        npc = topic.split("/")[1]
        msg = parse_message(payload)
        to = msg["meta"].get("to")
        arrow = f" (para {to})" if to and to != "joao" else ""
        print(f"{'🦗 ' if npc == NPC_ID else ''}{npc}{arrow}> {msg['text']}")
        if npc == NPC_ID:
            speaker.say(msg["text"], msg["meta"].get("lang"))

    bus.subscribe(ALL_NPC_SPEECH, show)
    grilo.start()
    print(OFFLINE_HELP)

    while True:
        try:
            line = input("você> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.startswith("/"):
            if not _offline_command(line, bus, grilo):
                break
        else:
            bus.publish(USER_SPEECH, make_message("joao", line))
        grilo.process_pending()
    bus.stop()


def _offline_command(line: str, bus: FakeBus, grilo: Grilo) -> bool:
    """Devolve False para sair."""
    parts = line.split()
    cmd, args = parts[0], parts[1:]
    if cmd == "/sair":
        return False
    if cmd == "/humor":
        print(grilo.mood.snapshot())
    elif cmd == "/fontes":
        print(list(grilo.sources) or "(nenhuma fonte registrada)")
    elif cmd == "/app" and args:
        # "/app VS Code 150" → app "VS Code" há 150 min; sem número = 0 min
        minutes = 0.0
        if len(args) > 1 and args[-1].isdigit():
            minutes = float(args.pop())
        name = " ".join(args)
        bus.publish(ACTIVITY, json.dumps({"app": name, "title": name,
                                          "app_seconds": minutes * 60, "afk": False}))
    elif cmd == "/afk" and args:
        bus.publish(AFK, json.dumps({"status": "afk", "seconds": 0}))
        bus.publish(AFK, json.dumps({"status": "present", "seconds": 0,
                                     "previous_status": "afk",
                                     "previous_seconds": float(args[0]) * 60}))
    elif cmd == "/npc" and len(args) >= 2:
        bus.publish(npc_topic(args[0], "fala"),
                    make_message(args[0], " ".join(args[1:]), to="joao", depth=0))
    else:
        print(OFFLINE_HELP)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Grilo Falante")
    parser.add_argument("--config", help="caminho do config.yaml")
    parser.add_argument("--offline", action="store_true",
                        help="roda sem MQTT nem Ollama, com chat no terminal")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs de debug")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # não loga cada request

    config = load_config(args.config)
    if args.offline:
        run_offline(config)
    else:
        run_online(config)


if __name__ == "__main__":
    main()
