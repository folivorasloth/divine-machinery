"""Tópicos MQTT e formato das mensagens do Divine Machinery.

Toda fala (de pessoa ou NPC) é um JSON com o mesmo formato:

    {"from": "grilo", "text": "...", "ts": "2026-11-06T22:10:00", "meta": {...}}

Assim qualquer NPC futuro consegue ouvir e entender qualquer outro.
"""

import json
from datetime import datetime

# --- Tópicos -----------------------------------------------------------------

USER_SPEECH = "joao/fala"   # o que o João digita
ACTIVITY = "pc/atividade"   # app atual, título, tempo contínuo (publicador do AW)
AFK = "pc/afk"              # ausente/presente (retido)
PC_STATUS = "pc/status"     # publicador online/offline

ALL_NPC_SPEECH = "npc/+/fala"     # '+' = qualquer NPC
ALL_NPC_STATUS = "npc/+/status"


def npc_topic(npc_id: str, kind: str) -> str:
    """Ex.: npc_topic("grilo", "fala") → "npc/grilo/fala". kind: fala, humor, status."""
    return f"npc/{npc_id}/{kind}"


# --- Payloads ----------------------------------------------------------------

def make_message(sender: str, text: str, **meta) -> str:
    return json.dumps(
        {
            "from": sender,
            "text": text,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "meta": meta,
        },
        ensure_ascii=False,
    )


def parse_json(payload: str) -> dict | None:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_message(payload: str) -> dict:
    """Aceita o JSON padrão ou texto puro.

    Texto puro permite testar direto do terminal:
        mosquitto_pub -t joao/fala -m "oi grilo"
    """
    data = parse_json(payload)
    if data and "text" in data:
        data.setdefault("meta", {})
        return data
    return {"from": "?", "text": payload, "meta": {}}
