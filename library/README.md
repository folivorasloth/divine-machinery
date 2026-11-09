# Biblioteca do grilo

Coloque aqui os livros de filosofia que o grilo deve conhecer. Nada desta pasta vai
para o git além deste arquivo.

- Formatos: `.txt`, `.md`, `.epub`, `.pdf` (PDF escaneado, sem texto, não funciona).
- Nome do arquivo: `Autor - Título.ext`, ex.: `Nietzsche - A Gaia Ciência.epub`.
  É assim que o grilo sabe quem escreveu o quê. Em epub, sem esse padrão, ele usa os metadados.
- Depois de adicionar livros, com o Ollama ligado e o modelo `bge-m3` baixado:

```bash
docker compose run --rm grilo python -m grilo.ingest
```
```bash
docker compose restart grilo
```

Fontes de livros em domínio público: [Domínio Público](http://www.dominiopublico.gov.br),
[Project Gutenberg](https://www.gutenberg.org).
