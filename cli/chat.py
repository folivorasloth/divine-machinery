"""Terminal para conversar com os NPCs pelo MQTT.

    python -m cli.chat

O que você digita vai para joao/fala. Mostra (e fala, se o TTS estiver ligado)
tudo que chega em npc/+/fala — ou seja, já vai ouvir os NPCs futuros também.
"""

import argparse
import logging
import os

from common.bus import MqttBus
from common.config import load_config
from common.messages import ALL_NPC_SPEECH, ALL_NPC_STATUS, USER_SPEECH, make_message, parse_message
from common.tts import Speaker

PROMPT = "você> "


def _print_above_prompt(line: str) -> None:
    # As mensagens chegam numa thread separada, no meio da digitação:
    # volta pro começo da linha, imprime e redesenha o prompt.
    print(f"\r{line}\n{PROMPT}", end="", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat com os NPCs do Divine Machinery")
    parser.add_argument("--config", help="caminho do config.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="[%(name)s] %(message)s")

    config = load_config(args.config)
    mqtt_cfg = config.get("mqtt", {})
    speaker = Speaker(config.get("tts", {}))
    # client_id único: dá para abrir mais de um terminal ao mesmo tempo
    bus = MqttBus(mqtt_cfg.get("host", "localhost"), mqtt_cfg.get("port", 1883),
                  client_id=f"dm-cli-{os.getpid()}")

    def on_speech(topic: str, payload: str) -> None:
        npc = topic.split("/")[1]
        msg = parse_message(payload)
        text = msg["text"]
        to = msg["meta"].get("to")
        arrow = f" (para {to})" if to and to != "joao" else ""  # conversa entre NPCs
        _print_above_prompt(f"{'🦗 ' if npc == 'grilo' else ''}{npc}{arrow}> {text}")
        speaker.say(text, msg["meta"].get("lang"))

    def on_status(topic: str, payload: str) -> None:
        _print_above_prompt(f"[{topic.split('/')[1]} está {payload}]")

    bus.subscribe(ALL_NPC_SPEECH, on_speech)
    bus.subscribe(ALL_NPC_STATUS, on_status)
    bus.start()
    print(f"Conectando em {bus.host}:{bus.port}. Ctrl+C para sair.")

    try:
        while True:
            text = input(PROMPT).strip()
            if text:
                bus.publish(USER_SPEECH, make_message("joao", text))
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        bus.stop()


if __name__ == "__main__":
    main()
