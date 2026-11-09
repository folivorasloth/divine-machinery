"""Biblioteca de filosofia: busca os trechos de livros mais parecidos com um texto.

O índice é gerado por `python -m grilo.ingest` e fica em library/index/:
  chunks.jsonl     um trecho por linha: {"author", "work", "text"}
  vectors.npy      um vetor por trecho (mesma ordem), já normalizado
  manifest.json    livros indexados e o modelo de embedding usado

A busca é "força bruta": compara o vetor da pergunta com todos os trechos
(produto escalar). Para algumas dezenas de livros isso leva milissegundos,
então não precisa de banco vetorial.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("library")


@dataclass
class Passage:
    author: str
    work: str
    text: str
    score: float = 0.0

    def label(self) -> str:
        return f"{self.author}, {self.work}" if self.work else self.author


class Library:
    def __init__(self, index_dir: str | Path):
        self.index_dir = Path(index_dir)
        self.passages: list[Passage] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.embed_model: str | None = None
        self._load()

    @property
    def empty(self) -> bool:
        return not self.passages

    def _load(self) -> None:
        chunks = self.index_dir / "chunks.jsonl"
        vectors = self.index_dir / "vectors.npy"
        if not (chunks.exists() and vectors.exists()):
            log.info("biblioteca vazia (rode `python -m grilo.ingest` para indexar livros)")
            return
        with open(chunks, encoding="utf-8") as f:
            self.passages = [Passage(**json.loads(line)) for line in f if line.strip()]
        self.vectors = np.load(vectors)
        manifest = self.index_dir / "manifest.json"
        if manifest.exists():
            self.embed_model = json.loads(manifest.read_text(encoding="utf-8")).get("embed_model")
        if len(self.passages) != len(self.vectors):
            log.error("índice inconsistente (%d trechos, %d vetores); rode o ingest com --rebuild",
                      len(self.passages), len(self.vectors))
            self.passages, self.vectors = [], np.zeros((0, 0), dtype=np.float32)
            return
        books = {p.label() for p in self.passages}
        log.info("biblioteca: %d trechos de %d livros", len(self.passages), len(books))

    def search(self, query_vector: list[float], k: int = 3) -> list[Passage]:
        if self.empty:
            return []
        query = normalize_vector(np.asarray(query_vector, dtype=np.float32))
        if query.shape[0] != self.vectors.shape[1]:
            log.error("vetor da pergunta tem %d dimensões e o índice %d: o modelo de embedding "
                      "mudou? Rode o ingest com --rebuild", query.shape[0], self.vectors.shape[1])
            return []
        # Vetores normalizados: produto escalar = similaridade de cosseno (1 = mesmo sentido)
        scores = self.vectors @ query
        best = np.argsort(scores)[::-1][:k]
        return [Passage(self.passages[i].author, self.passages[i].work,
                        self.passages[i].text, float(scores[i])) for i in best]


def normalize_vector(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)
