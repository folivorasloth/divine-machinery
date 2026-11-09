"""Indexa os livros de filosofia da pasta library/ para o grilo poder citá-los.

    python -m grilo.ingest              # indexa só os livros novos
    python -m grilo.ingest --rebuild    # apaga o índice e refaz tudo

No Docker (homelab):
    docker compose run --rm grilo python -m grilo.ingest

Precisa do Ollama ligado, com o modelo de embedding baixado (ollama pull bge-m3).

Formatos: .txt, .md, .epub e .pdf. Para o grilo saber quem escreveu, nomeie o
arquivo como "Autor - Título.ext" (ex.: "Nietzsche - A Gaia Ciência.epub").
No epub, se o nome não seguir o padrão, autor e título vêm dos metadados.
"""

import argparse
import hashlib
import json
import logging
import posixpath
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import numpy as np

from common.config import load_config, resolve_path
from grilo.library import normalize_vector
from grilo.llm import OllamaClient

log = logging.getLogger("ingest")

SUPPORTED = {".txt", ".md", ".epub", ".pdf"}
BATCH = 16  # trechos por chamada de embedding


# --- leitura dos arquivos ---------------------------------------------------------

class _HtmlText(HTMLParser):
    """Extrai só o texto de um XHTML de epub, com quebra de linha entre parágrafos."""

    BLOCKS = {"p", "div", "br", "h1", "h2", "h3", "h4", "li", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HtmlText()
    parser.feed(html)
    return "".join(parser.parts)


def read_epub(path: Path) -> tuple[str, dict]:
    """Um epub é um zip de páginas XHTML. A ordem de leitura está no "spine" do arquivo .opf."""
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container",
          "opf": "http://www.idpf.org/2007/opf",
          "dc": "http://purl.org/dc/elements/1.1/"}
    with zipfile.ZipFile(path) as z:
        container = ElementTree.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//c:rootfile", ns).get("full-path")
        opf = ElementTree.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)

        meta = {}
        title = opf.find(".//dc:title", ns)
        creator = opf.find(".//dc:creator", ns)
        if title is not None and title.text:
            meta["work"] = title.text.strip()
        if creator is not None and creator.text:
            meta["author"] = creator.text.strip()

        manifest = {item.get("id"): item.get("href") for item in opf.find("opf:manifest", ns)}
        pages = []
        for itemref in opf.find("opf:spine", ns):
            href = manifest.get(itemref.get("idref"))
            if href:
                html = z.read(posixpath.join(base, href)).decode("utf-8", errors="replace")
                pages.append(html_to_text(html))
    return "\n".join(pages), meta


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def read_book(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return read_epub(path)
    if suffix == ".pdf":
        return read_pdf(path), {}
    return path.read_text(encoding="utf-8", errors="replace"), {}


def author_and_work(path: Path, meta: dict) -> tuple[str, str]:
    """"Nietzsche - A Gaia Ciência.epub" → ("Nietzsche", "A Gaia Ciência")."""
    if " - " in path.stem:
        author, work = path.stem.split(" - ", 1)
        return author.strip(), work.strip()
    return meta.get("author", "autor desconhecido"), meta.get("work", path.stem)


def chunk_text(text: str, words: int = 300, overlap: int = 50) -> list[str]:
    """Corta em janelas de ~`words` palavras. A sobreposição evita que uma ideia
    fique partida ao meio entre dois trechos."""
    tokens = re.sub(r"\s+", " ", text).strip().split(" ")
    if tokens == [""]:
        return []
    step = max(1, words - overlap)
    chunks = []
    for start in range(0, len(tokens), step):
        chunks.append(" ".join(tokens[start:start + words]))
        if start + words >= len(tokens):
            break
    return chunks


def file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


# --- indexação --------------------------------------------------------------------

def embed_all(pieces: list[str], llm) -> np.ndarray | None:
    """Vetores normalizados de todos os trechos, em lotes; None se o Ollama falhar."""
    vectors = []
    for start in range(0, len(pieces), BATCH):
        result = llm.embed(pieces[start:start + BATCH], timeout=120)
        if result is None:
            return None
        vectors.extend(result)
    return normalize_vector(np.asarray(vectors, dtype=np.float32))


def ingest(library_dir: Path, llm, chunk_words: int = 300, rebuild: bool = False) -> int:
    """Indexa os livros novos. Devolve quantos trechos foram adicionados."""
    index_dir = library_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    chunks_file = index_dir / "chunks.jsonl"
    vectors_file = index_dir / "vectors.npy"
    manifest_file = index_dir / "manifest.json"

    if rebuild:
        for f in (chunks_file, vectors_file, manifest_file):
            f.unlink(missing_ok=True)

    manifest = {"embed_model": llm.embed_model, "books": {}}
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest.get("embed_model") != llm.embed_model:
            log.error("o índice foi feito com %s e o config pede %s: rode com --rebuild",
                      manifest.get("embed_model"), llm.embed_model)
            return 0
    vectors = np.load(vectors_file) if vectors_file.exists() else None

    books = sorted(p for p in library_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in SUPPORTED)
    if not books:
        log.warning("nenhum livro em %s (formatos: %s)", library_dir, ", ".join(sorted(SUPPORTED)))
        return 0

    added = 0
    for path in books:
        digest = file_hash(path)
        known = manifest["books"].get(path.name)
        if known == digest:
            continue
        if known:
            log.warning("%s mudou desde a indexação; rode com --rebuild para atualizar", path.name)
            continue

        try:
            text, meta = read_book(path)
        except Exception as e:  # arquivo corrompido, pdf protegido etc.
            log.error("não consegui ler %s: %s", path.name, e)
            continue
        author, work = author_and_work(path, meta)
        pieces = chunk_text(text, chunk_words)
        if not pieces:
            log.warning("%s: nenhum texto extraído (pdf escaneado?)", path.name)
            continue

        log.info("%s — %s: %d trechos", author, work, len(pieces))
        new_vectors = embed_all(pieces, llm)
        if new_vectors is None:
            # O Ollama caiu: não adianta tentar os próximos livros.
            log.error("embedding falhou (%s); %s não foi indexado", llm.last_error, path.name)
            break

        # Só grava o livro quando todos os trechos deram certo.
        vectors = new_vectors if vectors is None else np.vstack([vectors, new_vectors])
        with open(chunks_file, "a", encoding="utf-8") as f:
            for piece in pieces:
                f.write(json.dumps({"author": author, "work": work, "text": piece},
                                   ensure_ascii=False) + "\n")
        np.save(vectors_file, vectors)
        manifest["books"][path.name] = digest
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        added += len(pieces)

    log.info("pronto: %d trechos novos", added)
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa os livros da biblioteca do grilo")
    parser.add_argument("--config", help="caminho do config.yaml")
    parser.add_argument("--rebuild", action="store_true", help="apaga o índice e refaz tudo")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config(args.config)
    philosophy = config.get("philosophy", {})
    library_dir = resolve_path(philosophy.get("library_dir", "library"))
    if not library_dir.is_dir():
        sys.exit(f"pasta da biblioteca não existe: {library_dir}")
    llm = OllamaClient.from_config(config["ollama"])
    ingest(library_dir, llm, philosophy.get("chunk_words", 300), args.rebuild)


if __name__ == "__main__":
    main()
