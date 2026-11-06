"""Barramento de mensagens: MQTT de verdade (MqttBus) ou um falso em memória (FakeBus).

Os dois têm a mesma interface, então o grilo não sabe (nem precisa saber)
se está falando com o Mosquitto ou rodando no modo --offline.

    bus.subscribe("joao/fala", handler)   # handler(topic: str, payload: str)
    bus.start()
    bus.publish("npc/grilo/fala", "...", retain=False)
    bus.stop()
"""

import logging
from typing import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.client import topic_matches_sub

log = logging.getLogger("bus")

Handler = Callable[[str, str], None]


class MqttBus:
    def __init__(self, host: str, port: int = 1883, client_id: str = "dm",
                 status_topic: str | None = None):
        """status_topic: se informado, publica "online" (retido) ao conectar e
        registra um Last Will "offline" — o broker publica por nós se o processo morrer."""
        self.host = host
        self.port = port
        self._status_topic = status_topic
        self._handlers: list[tuple[str, Handler]] = []

        # paho-mqtt 2.x exige escolher a versão da API de callbacks.
        # VERSION2 = assinaturas novas, com reason_code e properties.
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        if status_topic:
            self._client.will_set(status_topic, "offline", qos=1, retain=True)

    def start(self) -> None:
        # connect_async + loop_start: não trava se o broker estiver fora do ar;
        # o paho fica tentando reconectar sozinho numa thread própria.
        log.info("conectando ao broker %s:%s", self.host, self.port)
        self._client.connect_async(self.host, self.port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        if self._status_topic and self._client.is_connected():
            info = self._client.publish(self._status_topic, "offline", qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                pass
        self._client.disconnect()
        self._client.loop_stop()

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._handlers.append((pattern, handler))
        if self._client.is_connected():
            self._client.subscribe(pattern, qos=1)

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        # Com qos=1 o paho guarda a mensagem e envia quando reconectar.
        self._client.publish(topic, payload, qos=1, retain=retain)

    # --- callbacks do paho (rodam na thread de rede dele) --------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("broker recusou a conexão: %s", reason_code)
            return
        log.info("conectado ao broker")
        # Assina de novo a cada conexão: após uma queda, as assinaturas se perdem.
        for pattern, _ in self._handlers:
            client.subscribe(pattern, qos=1)
        if self._status_topic:
            client.publish(self._status_topic, "online", qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.warning("desconectado do broker (%s), tentando reconectar…", reason_code)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")
        _dispatch(self._handlers, msg.topic, payload)


class FakeBus:
    """Broker de mentira, em memória: entrega as mensagens na hora, na mesma thread.
    Usado pelo modo --offline e pelos testes."""

    def __init__(self, status_topic: str | None = None):
        self._status_topic = status_topic
        self._handlers: list[tuple[str, Handler]] = []
        self.retained: dict[str, str] = {}

    def start(self) -> None:
        if self._status_topic:
            self.publish(self._status_topic, "online", retain=True)

    def stop(self) -> None:
        if self._status_topic:
            self.publish(self._status_topic, "offline", retain=True)

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._handlers.append((pattern, handler))
        # Igual ao broker real: quem assina recebe as mensagens retidas na hora.
        for topic, payload in self.retained.items():
            if topic_matches_sub(pattern, topic):
                handler(topic, payload)

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if retain:
            self.retained[topic] = payload
        _dispatch(self._handlers, topic, payload)


def _dispatch(handlers: list[tuple[str, Handler]], topic: str, payload: str) -> None:
    for pattern, handler in handlers:
        if topic_matches_sub(pattern, topic):
            try:
                handler(topic, payload)
            except Exception:
                # Um handler com bug não pode derrubar a conexão inteira.
                log.exception("erro tratando mensagem de %s", topic)
