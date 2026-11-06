# Imagem do grilo. O config.yaml e a pasta persona/ entram por volume
# (veja docker-compose.yml), então editar a persona não exige rebuild,
# só `docker compose restart grilo`.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
# tzdata: sem ele a variável TZ é ignorada e a "madrugada" vira horário UTC
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY grilo ./grilo

CMD ["python", "-m", "grilo.main"]
