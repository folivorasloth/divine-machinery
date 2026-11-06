"""Cliente do Ollama (o "Oráculo") com timeout curto.

Se falhar (desligado, fora de casa, lento demais), marca como offline por
alguns segundos: assim o grilo não fica esperando 8s a cada mensagem
enquanto o notebook do LLM está desligado.
"""

import logging
import time

import httpx

log = logging.getLogger("llm")


class OllamaClient:
    def __init__(self, url: str, model: str, timeout_seconds: float = 8,
                 offline_cooldown_seconds: float = 60, temperature: float = 0.9,
                 keep_alive: str = "30m", clock=time.time):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.offline_cooldown = offline_cooldown_seconds
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.clock = clock
        self.last_error: str | None = None
        self._down_until = 0.0

    @classmethod
    def from_config(cls, config: dict) -> "OllamaClient":
        return cls(
            url=config["url"],
            model=config["model"],
            timeout_seconds=config.get("timeout_seconds", 8),
            offline_cooldown_seconds=config.get("offline_cooldown_seconds", 60),
            temperature=config.get("temperature", 0.9),
            keep_alive=config.get("keep_alive", "30m"),
        )

    def chat(self, messages: list[dict]) -> str | None:
        """Devolve o texto da resposta, ou None (e o motivo em self.last_error)."""
        self.last_error = None
        remaining = self._down_until - self.clock()
        if remaining > 0:
            self.last_error = f"offline (nova tentativa em {int(remaining)}s)"
            return None

        try:
            response = httpx.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": self.keep_alive,
                    # num_predict limita o tamanho: ele é comentarista, não palestrante
                    "options": {"temperature": self.temperature, "num_predict": 120},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = response.json()["message"]["content"].strip()
        except httpx.TimeoutException:
            error = f"timeout de {self.timeout}s"
        except httpx.HTTPError as e:
            error = f"{type(e).__name__}: {e}"
        except (KeyError, ValueError):
            error = "resposta em formato inesperado"
        else:
            if text:
                return text
            error = "resposta vazia"

        self.last_error = error
        self._down_until = self.clock() + self.offline_cooldown
        return None


class NoLLM:
    """Usado no modo --offline: nunca responde, sempre cai no fallback."""

    last_error = "modo offline"

    def chat(self, messages: list[dict]) -> None:
        return None
