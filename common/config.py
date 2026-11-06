"""Carrega o config.yaml e outros YAMLs do projeto."""

import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger("config")

# Raiz do repositório: caminhos relativos do config são resolvidos a partir daqui,
# então tanto faz de qual pasta você roda os scripts.
ROOT = Path(__file__).resolve().parent.parent


def resolve_path(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_yaml(path: str | Path) -> dict:
    with open(resolve_path(path), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str | None = None) -> dict:
    """Lê o config.yaml. Sem ele, cai no config.example.yaml (bom para o --offline)."""
    if path:
        candidates = [resolve_path(path)]
    else:
        candidates = [ROOT / "config.yaml", ROOT / "config.example.yaml"]

    for candidate in candidates:
        if candidate.exists():
            if candidate.name == "config.example.yaml":
                log.warning("config.yaml não encontrado, usando config.example.yaml")
            config = load_yaml(candidate)
            _apply_env_overrides(config)
            return config

    raise FileNotFoundError(f"nenhum arquivo de configuração encontrado: {candidates}")


def _apply_env_overrides(config: dict) -> None:
    # No Docker, o broker é achado pelo nome do serviço ("mosquitto"),
    # então o compose sobrescreve só o host, sem precisar de outro config.yaml.
    host = os.environ.get("MQTT_HOST")
    if host:
        config.setdefault("mqtt", {})["host"] = host
