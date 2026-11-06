"""Publicador do ActivityWatch: roda no notebook pessoal.

    python -m publisher.aw_publisher

A cada intervalo, lê a API REST local do ActivityWatch e publica:
  pc/atividade  {"app", "title", "app_seconds", "afk", "ts"}
  pc/afk        {"status": "afk"|"present", "seconds", "previous_status", "previous_seconds"}  (retido)
"""

import argparse
import json
import logging
import socket
import time
from datetime import datetime, timezone

import httpx

from common.bus import MqttBus
from common.config import load_config
from common.messages import ACTIVITY, AFK, PC_STATUS

log = logging.getLogger("publisher")


def parse_ts(value: str) -> datetime:
    # O AW manda UTC, às vezes com "Z" no final
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ActivityWatch:
    """Cliente mínimo da API REST do ActivityWatch."""

    def __init__(self, url: str, hostname: str | None = None):
        self.url = url.rstrip("/")
        self.hostname = hostname or socket.gethostname()
        self.window_bucket: str | None = None
        self.afk_bucket: str | None = None

    def find_buckets(self) -> None:
        buckets = httpx.get(f"{self.url}/api/0/buckets/", timeout=5).json()

        def pick(bucket_type: str) -> str | None:
            matches = [b for b in buckets.values() if b.get("type") == bucket_type]
            # Prefere o bucket desta máquina; se não achar, usa o primeiro.
            mine = [b for b in matches if b.get("hostname") == self.hostname]
            chosen = (mine or matches or [None])[0]
            return chosen["id"] if chosen else None

        self.window_bucket = pick("currentwindow")   # aw-watcher-window_<host>
        self.afk_bucket = pick("afkstatus")          # aw-watcher-afk_<host>
        log.info("buckets: janela=%s afk=%s", self.window_bucket, self.afk_bucket)

    def latest(self, bucket: str, limit: int = 1) -> list[dict]:
        """Eventos mais recentes primeiro."""
        response = httpx.get(f"{self.url}/api/0/buckets/{bucket}/events",
                             params={"limit": limit}, timeout=5)
        response.raise_for_status()
        return response.json()


class ActivityTracker:
    """Calcula o tempo contínuo no mesmo app.

    O AW cria um evento novo a cada troca de título de janela, então o "tempo
    no app" é calculado aqui: guardamos quando o app mudou pela última vez.
    """

    def __init__(self):
        self.app: str | None = None
        self.since: float = 0.0

    def update(self, app: str, is_afk: bool, now: float) -> float:
        if is_afk:
            self.app = None          # ao voltar do AFK, a contagem recomeça
            return 0.0
        if app != self.app:
            self.app, self.since = app, now
        return now - self.since


def afk_payload(events: list[dict], now: datetime) -> dict | None:
    if not events:
        return None
    current = events[0]
    # O AW usa "afk" e "not-afk"; aqui viram "afk" e "present"
    status = "afk" if current["data"].get("status") == "afk" else "present"
    payload = {"status": status, "seconds": int((now - parse_ts(current["timestamp"])).total_seconds())}
    if len(events) > 1:
        previous = events[1]
        payload["previous_status"] = "afk" if previous["data"].get("status") == "afk" else "present"
        payload["previous_seconds"] = int(previous.get("duration", 0))
    return payload


def publish_once(aw: ActivityWatch, tracker: ActivityTracker, bus: MqttBus) -> None:
    if not aw.window_bucket or not aw.afk_bucket:
        aw.find_buckets()
        if not aw.window_bucket or not aw.afk_bucket:
            log.warning("buckets do ActivityWatch não encontrados (os watchers estão rodando?)")
            return

    now = datetime.now(timezone.utc)
    afk = afk_payload(aw.latest(aw.afk_bucket, limit=2), now)
    is_afk = bool(afk and afk["status"] == "afk")
    if afk:
        bus.publish(AFK, json.dumps(afk), retain=True)

    window = aw.latest(aw.window_bucket)
    if window:
        data = window[0]["data"]
        app = data.get("app", "?")
        seconds = tracker.update(app, is_afk, time.time())
        activity = {
            "app": app,
            "title": data.get("title", ""),
            "app_seconds": int(seconds),
            "afk": is_afk,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        bus.publish(ACTIVITY, json.dumps(activity, ensure_ascii=False))
        log.info("%s | %s | %ds%s", app, activity["title"][:60], seconds, " (AFK)" if is_afk else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Publica o ActivityWatch no MQTT")
    parser.add_argument("--config", help="caminho do config.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config(args.config)
    pub_cfg = config.get("publisher", {})
    mqtt_cfg = config.get("mqtt", {})
    interval = pub_cfg.get("interval_seconds", 30)

    aw = ActivityWatch(pub_cfg.get("activitywatch_url", "http://localhost:5600"), pub_cfg.get("hostname"))
    tracker = ActivityTracker()
    bus = MqttBus(mqtt_cfg.get("host", "localhost"), mqtt_cfg.get("port", 1883),
                  client_id="dm-publisher", status_topic=PC_STATUS)
    bus.start()

    try:
        while True:
            try:
                publish_once(aw, tracker, bus)
            except httpx.HTTPError as e:
                log.warning("ActivityWatch não respondeu (%s); tento de novo em %ss", e, interval)
                aw.window_bucket = aw.afk_bucket = None  # redescobre na próxima
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        bus.stop()


if __name__ == "__main__":
    main()
