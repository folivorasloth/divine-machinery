import json
import zipfile

import numpy as np

from grilo.ingest import author_and_work, chunk_text, ingest, read_epub
from grilo.library import Library


def test_chunk_text_overlaps_and_covers_everything():
    text = " ".join(f"w{i}" for i in range(700))
    chunks = chunk_text(text, words=300, overlap=50)
    assert len(chunks) == 3
    assert chunks[0].split()[-1] == "w299" and chunks[1].split()[0] == "w250"
    assert chunks[-1].split()[-1] == "w699"


def test_chunk_text_empty():
    assert chunk_text("   ") == []


def test_author_and_work_from_filename(tmp_path):
    path = tmp_path / "Nietzsche - A Gaia Ciência.epub"
    assert author_and_work(path, {}) == ("Nietzsche", "A Gaia Ciência")
    assert author_and_work(tmp_path / "livro.txt", {"author": "Camus"}) == ("Camus", "livro")


def make_epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<package xmlns="http://www.idpf.org/2007/opf" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata>'
                   '<dc:title>O Mito de Sísifo</dc:title><dc:creator>Albert Camus</dc:creator>'
                   '</metadata><manifest><item id="c2" href="c2.xhtml"/><item id="c1" href="c1.xhtml"/>'
                   '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/c1.xhtml", "<html><body><p>Primeiro capítulo.</p></body></html>")
        z.writestr("OEBPS/c2.xhtml", "<html><style>x{}</style><body><p>Segundo.</p></body></html>")


def test_read_epub_follows_spine_and_metadata(tmp_path):
    path = tmp_path / "mito.epub"
    make_epub(path)
    text, meta = read_epub(path)
    assert text.index("Primeiro") < text.index("Segundo")
    assert "x{}" not in text
    assert meta == {"work": "O Mito de Sísifo", "author": "Albert Camus"}


class WordVectorLLM:
    """Embedding de mentira: conta as palavras "absurdo" e "retorno"."""
    embed_model = "fake"
    last_error = None

    def embed(self, texts, timeout=None):
        return [[t.count("absurdo") + 0.01, t.count("retorno") + 0.01] for t in texts]


def test_ingest_then_search_finds_the_right_book(tmp_path):
    (tmp_path / "Camus - O Mito de Sísifo.txt").write_text("o absurdo " * 50, encoding="utf-8")
    (tmp_path / "Nietzsche - A Gaia Ciência.txt").write_text("o eterno retorno " * 50,
                                                           encoding="utf-8")
    llm = WordVectorLLM()
    assert ingest(tmp_path, llm, chunk_words=40) > 0
    assert ingest(tmp_path, llm, chunk_words=40) == 0  # já indexados: nada novo

    library = Library(tmp_path / "index")
    best = library.search(llm.embed(["fale do retorno"])[0], k=1)[0]
    assert best.author == "Nietzsche"
    manifest = json.loads((tmp_path / "index" / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["books"]) == {"Camus - O Mito de Sísifo.txt",
                                      "Nietzsche - A Gaia Ciência.txt"}
    assert np.load(tmp_path / "index" / "vectors.npy").shape[0] == len(library.passages)


def test_missing_index_means_empty_library(tmp_path):
    assert Library(tmp_path / "nada").empty
