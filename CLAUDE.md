# Divine Machinery — contexto para o Claude

Este arquivo é lido automaticamente pelo Claude Code ao abrir o repositório. Ele também serve
para colar no começo de qualquer conversa nova com o Claude sobre o projeto.

## O que é

Divine Machinery é um projeto pessoal do João: um **ecossistema de personagens (NPCs) que vivem
no ambiente dele**, conversam entre si e reagem ao que ele faz no computador. Cada personagem tem
personalidade, humor e memória próprios. Todos se comunicam por um broker MQTT (Mosquitto).
**Tudo roda localmente**, sem APIs de nuvem.

O João quer **entender o código**. Prefira soluções simples e legíveis, comente o que não for
óbvio e não superdimensione. **Antes de escrever código, apresente o plano e espere a aprovação.**

## Máquinas

| Máquina | Roda | Sempre ligada? |
|---|---|---|
| Homelab (Proxmox → VM Ubuntu + Docker) | Mosquitto (1883, WebSocket 9001) e os NPCs, via `docker-compose.yml` | Sim |
| Notebook pessoal | ActivityWatch, `publisher/aw_publisher.py`, `cli/chat.py` (conversa + voz Piper) | Quando usado |
| Notebook do LLM | Ollama (`OLLAMA_HOST=0.0.0.0`, porta 11434), modelo de chat + `bge-m3` (embeddings) | **Não**: os NPCs precisam funcionar sem ele |

Nenhum IP no código: tudo vem do `config.yaml`, que fica fora do git (o repositório é público).

## Convenções

- Python 3.11+, `paho-mqtt` 2.x (`CallbackAPIVersion.VERSION2`), `httpx`, `pyyaml`, `numpy`, `pypdf`.
- **Código (nomes de variáveis, funções e classes) sempre em inglês.** Comentários, logs, README
  e dados visíveis (chaves de humor, templates, persona) em português.
- Personalidade fica em YAML (`persona/<npc>.yaml`), nunca no código.
- Configuração ajustável fica no `config.yaml`. Chave nova entra no `config.example.yaml` com
  comentário.
- Handlers do MQTT só enfileiram; o trabalho pesado fica no loop principal.
- Toda decisão de um NPC vira uma linha de log com o motivo (`Decision.reason`).
- Tempo sempre pelo `clock` injetado, para os testes não precisarem de `sleep`.
- Testes com `python -m pytest`. Teste offline com `python -m grilo.main --offline`.
- Commits em português, direto na `main`.

## Estrutura

```
common/          compartilhado por todos os NPCs
  bus.py           MqttBus (paho) e FakeBus (memória, para --offline e testes)
  messages.py      tópicos e formato JSON das falas
  conversation.py  regras de NPC respondendo a NPC (profundidade máxima, cooldown, chance)
  language.py      detecção português x inglês
  config.py, text.py, tts.py (Piper, uma voz por idioma)
grilo/           o Grilo Falante
  main.py          loop, roteamento por tópico, memórias, modo --offline
  brain.py         decide: silêncio / LLM / fallback; modos de resposta; memória de fontes
  mood.py          humor (irritacao, orgulho, energia) e decaimento
  triggers.py      gatilhos de atividade do PC + cooldowns
  fallback.py      categorias por palavra-chave e templates
  llm.py           cliente Ollama (chat + embed) com timeout e cooldown de falha
  library.py       busca de trechos nos livros (numpy, similaridade de cosseno)
  ingest.py        indexa os livros de library/
persona/         grilo.yaml (personalidade) e cast.yaml (elenco: quem é quem)
publisher/       ActivityWatch → MQTT (roda no notebook pessoal)
cli/chat.py      terminal para conversar com os NPCs
library/         livros + índice (fora do git)
tests/           pytest
```

## Protocolo MQTT (o "idioma comum" entre os personagens)

| Tópico | Conteúdo | Retido |
|---|---|---|
| `joao/fala` | `{"from","text","ts","meta"}` (texto puro também é aceito) | Não |
| `npc/<id>/fala` | mesmo formato; `meta`: `to`, `depth`, `lang`, `mode`, `citation`, `source`, `reason` | Não |
| `npc/<id>/humor` | `{"irritacao","orgulho","energia","estado","emburrado_ate"}` | Sim |
| `npc/<id>/status` | `online` / `offline` (Last Will) | Sim |
| `pc/atividade` | `{"app","title","app_seconds","afk","ts"}` | Não |
| `pc/afk` | `{"status","seconds","previous_status","previous_seconds"}` | Sim |
| `pc/status` | `online` / `offline` do publicador | Sim |

Conversa entre NPCs: `meta.to` diz com quem se fala; `meta.depth` conta as falas de NPC
encadeadas. Cada NPC ignora as próprias falas e mensagens com `depth >= max_depth`, o que impede
loop infinito. Ele responde sempre quando falam com ele e, nos outros casos, por sorteio
dependente do humor.

## Personagens

### Grilo Falante (`grilo`): pronto

- **Quem é:** um grilo britânico de alta classe, "educado em Oxford", preso no computador do
  João, sussurrando comentários no ouvido dele. Esnobe, condescendente, impecavelmente educado,
  sarcástico e filosófico. Zoa, mas acerta na crítica. É chato como um mordomo crítico, **não**
  uma IA fria ou ameaçadora (nada de GLaDOS).
- **Idioma:** responde no idioma em que o João falou. Em português usa pitadas de inglês
  ("my dear", "darling", "rather"); em inglês usa inglês britânico "posh".
- **Modos de resposta** (sorteados por humor): debochado (padrão), direto (curto e sem piada) e
  filosófico (reflexão com trechos dos livros da biblioteca).
- **Humor:** irritação acima de 80 com orgulho alto → retruca pesado; com orgulho baixo → fica
  emburrado ("hmpf") por 10 min.
- **Memória:** últimas 20 mensagens (inclusive falas de outros NPCs) e a origem das últimas
  citações. Isso permite responder "de quem é essa frase?" sem inventar.
- **Comenta sozinho:** sessão longa num app, volta de AFK, madrugada e sites de distração, com
  cooldowns e limite por hora.
- **Com outros NPCs:** acha todos intelectualmente inferiores. Irritado, fala mal deles.
- **Sem LLM:** templates nas duas línguas e citações curadas (o Oráculo "saiu de casa").

### Planejados (ainda não definidos)

- **Hefesto:** mencionado pelo João como futuro personagem. Personalidade a definir.
- **Olho:** mencionado no plano original.
- **NPCs do cenário:** personagens ambientes.

Para criar um personagem novo: siga o "Novo personagem" do guia (`docs` no claude.ai) ou o
README. Em resumo: pasta `<id>/` com `main.py` no molde do `grilo/main.py`, `persona/<id>.yaml`,
uma entrada em `persona/cast.yaml` e um serviço no `docker-compose.yml`. Quando dois NPCs
precisarem do mesmo `mood.py`, `brain.py` ou `llm.py`, mova o arquivo para `common/`.

## Limites conhecidos

- Os modelos locais de 7B são limitados em humor e em contexto longo. Testar modelos maiores
  (14B) se o hardware permitir.
- A memória só existe enquanto o processo roda: reiniciar o grilo zera o humor e as memórias.
- A detecção de idioma é por palavras comuns. Frases muito curtas mantêm o idioma atual.
- O broker é anônimo (só rede local).
