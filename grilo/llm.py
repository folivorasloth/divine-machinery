"""Cliente do Ollama (o "Oráculo") com timeout curto.

Duas operações:
  chat()   → gera a fala do personagem
  embed()  → transforma textos em vetores (usado pela biblioteca de filosofia)

Se uma chamada falhar (desligado, fora de casa, lento demais), o Ollama é
marcado como offline por alguns segundos: assim o grilo não fica esperando
8s a cada mensagem enquanto o notebook do LLM está desligado.
"""

import logging
import time

import httpx

log = logging.getLogger("llm")


class OllamaClient:
    def __init__(self, url: str, model: str, timeout_seconds: float = 8,
                 offline_cooldown_seconds: float = 60, temperature: float = 0.9,
                 keep_alive: str = "30m", num_ctx: int = 8192,
                 embed_model: str = "bge-m3", clock=time.time):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.offline_cooldown = offline_cooldown_seconds
        self.temperature = temperature
        self.keep_alive = keep_alive
        # Tamanho do contexto em tokens. O padrão do Ollama é pequeno e corta
        # o começo do prompt sem avisar (persona, trechos de livros...).
        self.num_ctx = num_ctx
        self.embed_model = embed_model
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
            num_ctx=config.get("num_ctx", 8192),
            embed_model=config.get("embed_model", "bge-m3"),
        )

    def chat(self, messages: list[dict], timeout: float | None = None,
             max_tokens: int = 120) -> str | None:
        """Devolve o texto da resposta, ou None (e o motivo em self.last_error).
        max_tokens limita o tamanho: ele é comentarista, não palestrante."""
        data = self._post("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_predict": max_tokens,
                        "num_ctx": self.num_ctx},
        }, timeout or self.timeout)
        if data is None:
            return None
        try:
            text = data["message"]["content"].strip()
        except (KeyError, TypeError, AttributeError):
            return self._fail("resposta em formato inesperado")
        return text or self._fail("resposta vazia")

    def embed(self, texts: list[str], timeout: float | None = None) -> list[list[float]] | None:
        """Um vetor por texto, ou None se falhar."""
        data = self._post("/api/embed", {"model": self.embed_model, "input": texts,
                                         "keep_alive": self.keep_alive},
                          timeout or self.timeout)
        if data is None:
            return None
        vectors = data.get("embeddings") if isinstance(data, dict) else None
        if not vectors or len(vectors) != len(texts):
            return self._fail("embeddings em formato inesperado")
        return vectors

    def _post(self, path: str, body: dict, timeout: float) -> dict | None:
        self.last_error = None
        remaining = self._down_until - self.clock()
        if remaining > 0:
            self.last_error = f"offline (nova tentativa em {int(remaining)}s)"
            return None
        try:
            response = httpx.post(f"{self.url}{path}", json=body, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            return self._fail(f"timeout de {timeout:g}s")
        except httpx.HTTPError as e:
            return self._fail(f"{type(e).__name__}: {e}")
        except ValueError:
            return self._fail("resposta não é JSON")

    def _fail(self, error: str) -> None:
        self.last_error = error
        self._down_until = self.clock() + self.offline_cooldown
        return None


class NoLLM:
    """Usado no modo --offline: nunca responde, sempre cai no fallback."""

    last_error = "modo offline"

    def chat(self, messages: list[dict], timeout: float | None = None,
             max_tokens: int = 120) -> None:
        return None

    def embed(self, texts: list[str], timeout: float | None = None) -> None:
        return None
