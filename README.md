# Divine Machinery

Um ecossistema de personagens (NPCs) que vivem no meu ambiente, conversam entre si
e reagem ao que eu faço. Todos se comunicam por um broker MQTT. Tudo local, nada de nuvem.

O primeiro personagem é o **Grilo Falante**: um grilo britânico esnobe, "educado em Oxford",
preso no seu computador e sussurrando comentários no seu ouvido. Sarcástico, filosófico e
impecavelmente condescendente: ele zoa, mas acerta na crítica. Fala português (com pitadas
de inglês) e troca para inglês britânico quando você fala em inglês.

```
 Notebook pessoal                      Homelab (VM Ubuntu + Docker)           Notebook do LLM
┌──────────────────────┐              ┌──────────────────────────┐          ┌───────────────┐
│ ActivityWatch :5600  │              │  Mosquitto :1883 / :9001 │          │ Ollama :11434 │
│   ↓                  │  pc/...      │        ↑    ↓            │  HTTP    │ (nem sempre   │
│ aw_publisher.py ─────┼─────────────►│      grilo (main.py) ────┼─────────►│  ligado)      │
│ cli/chat.py  ◄───────┼──────────────│        │                 │          └───────────────┘
│   (+ Piper TTS)      │ npc/grilo/.. │   sem Ollama → templates │
└──────────────────────┘              └──────────────────────────┘
```

## Tópicos MQTT

| Tópico             | Quem publica   | Conteúdo                                                                | Retido |
|--------------------|----------------|-------------------------------------------------------------------------|:------:|
| `joao/fala`        | `cli/chat.py`  | `{"from","text","ts","meta"}` (texto puro também é aceito)              |        |
| `npc/grilo/fala`   | grilo          | mesmo formato; `meta` traz `source`, `reason`, `trigger`, `mood`        |        |
| `npc/grilo/humor`  | grilo          | `{"irritacao","orgulho","energia","estado","emburrado_ate"}`            |   ✔    |
| `npc/grilo/status` | grilo          | `online` / `offline` (Last Will)                                        |   ✔    |
| `pc/atividade`     | publicador     | `{"app","title","app_seconds","afk","ts"}`                              |        |
| `pc/afk`           | publicador     | `{"status": "afk"/"present","seconds","previous_status","previous_seconds"}` |   ✔    |
| `pc/status`        | publicador     | `online` / `offline` (Last Will)                                        |   ✔    |

Todo NPC futuro segue o padrão `npc/<nome>/{fala,humor,status}`. Assim um NPC pode
ouvir todos os outros assinando `npc/+/fala`, e o `cli/chat.py` já faz isso.

## Estrutura

```
docker-compose.yml        Mosquitto + grilo (homelab)
Dockerfile                imagem do grilo
mosquitto/mosquitto.conf  listener 1883 + websocket 9001, anônimo (só rede local!)
config.example.yaml       modelo de configuração → copie para config.yaml
common/                   compartilhado por todos os NPCs
  config.py               carrega config.yaml
  language.py             detecta português x inglês
  text.py                 normalização de texto (acentos, minúsculas)
  bus.py                  MqttBus (paho 2.x) e FakeBus (em memória, p/ --offline)
  messages.py             tópicos e formato das mensagens
  tts.py                  Piper (opcional)
grilo/
  main.py                 loop principal + modo --offline
  mood.py                 humor e decaimento
  brain.py                decide: silêncio, LLM ou fallback
  llm.py                  cliente Ollama com timeout
  fallback.py             detecção de categoria + templates
  triggers.py             gatilhos de atividade + cooldowns
persona/grilo.yaml        personalidade, exemplos, palavras-chave, templates, efeitos no humor
publisher/aw_publisher.py ActivityWatch → MQTT (notebook pessoal)
cli/chat.py               terminal para conversar
tests/                    testes de humor, gatilhos e fallback
```

## Teste rápido (qualquer máquina, sem nada instalado além do Python)

```bash
python3 -m venv .venv
```
```bash
source .venv/bin/activate
```
```bash
pip install -r requirements-dev.txt
```
```bash
python -m pytest
```
```bash
python -m grilo.main --offline
```

O modo `--offline` não usa MQTT nem Ollama, só a personalidade e os templates. Sem
`config.yaml`, ele usa o `config.example.yaml`. Além de conversar, dá para simular atividade:

```
você> esse grilo só fala bosta
você> /humor
você> /app VS Code 150      ← "estou há 150 min no VS Code"
você> /app Firefox youtube  ← distração
você> /afk 40               ← "voltei depois de 40 min"
```

> Os comandos rodam a partir da raiz do repositório (`python -m pasta.arquivo`),
> para o Python achar a pasta `common/`.

---

## 1. Homelab: Mosquitto + grilo

Na VM Ubuntu com Docker:

```bash
git clone https://github.com/folivorasloth/divine-machinery.git
```
```bash
cd divine-machinery && cp config.example.yaml config.yaml
```

Edite o `config.yaml`. Aqui basta a seção `ollama` (IP do notebook do LLM) e, se quiser,
`mood`, `triggers` e `grilo`. O `mqtt.host` é ignorado no Docker, porque o compose usa o nome
do serviço `mosquitto`.

> Crie o `config.yaml` **antes** de subir. Se ele não existir, o Docker cria uma *pasta*
> com esse nome no lugar e o grilo não sobe.

```bash
docker compose up -d --build
```
```bash
docker compose logs -f grilo
```

O log mostra cada decisão, por exemplo:
```
[mood] fala do João (insulto): {'irritacao': 70, ...} → {'irritacao': 85, ...}
[mood] irritação 85 > 80 com orgulho baixo (22) → emburrado por 10 min
[grilo] decisão [emburrado]: emburrado → hmpf
[grilo] decisão [fallback]: LLM timeout de 8s → fallback categoria insulto
```

Comandos úteis:
- Mudou a persona? `docker compose restart grilo` (sem rebuild, a pasta é montada como volume).
- Mudou o código? `git pull && docker compose up -d --build`.
- Logs com cooldowns e outros detalhes: troque o `CMD` por `python -m grilo.main -v`.

Se o firewall da VM estiver ativo, libere as portas 1883 e 9001 só para a rede local:

```bash
sudo ufw allow from 192.168.0.0/24 to any port 1883,9001 proto tcp
```

## 2. Notebook do LLM: Ollama na rede

Instale o [Ollama](https://ollama.com) e baixe um modelo que fale bem português
(o padrão do config é `qwen2.5:7b`; `llama3.1:8b` também serve):

```bash
ollama pull qwen2.5:7b
```

Por padrão o Ollama só escuta em `localhost`. Para ele escutar na rede:

**Linux (systemd):**
```bash
sudo systemctl edit ollama
```
Adicione as linhas abaixo, salve e reinicie com `sudo systemctl restart ollama`:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```

**Windows:** em *Variáveis de Ambiente* do usuário, crie `OLLAMA_HOST` = `0.0.0.0` e reinicie o Ollama.

**macOS:** `launchctl setenv OLLAMA_HOST "0.0.0.0"` e reinicie o app do Ollama.

**Firewall: libere a porta 11434 só para o IP do homelab.** Troque `192.168.0.10` pelo IP real.

Linux (ufw):
```bash
sudo ufw allow from 192.168.0.10 to any port 11434 proto tcp
```

Windows (PowerShell como administrador):
```powershell
New-NetFirewallRule -DisplayName "Ollama (homelab)" -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress 192.168.0.10 -Action Allow
```

Para testar a partir do homelab:
```bash
curl http://IP_DO_NOTEBOOK_LLM:11434/api/tags
```

Quando esse notebook estiver desligado ou fora de casa, o grilo percebe pelo timeout,
passa a usar os templates e não tenta de novo por `offline_cooldown_seconds`.

> O primeiro carregamento do modelo na memória pode passar de 8s, e essa primeira
> resposta cai no fallback. O `keep_alive: 30m` mantém o modelo carregado depois disso.

## 3. Notebook pessoal: ActivityWatch, publicador e chat

1. Instale o [ActivityWatch](https://activitywatch.net/downloads/) e deixe-o rodando.
   Confira em http://localhost:5600 se aparecem os buckets `aw-watcher-window_...` e `aw-watcher-afk_...`.
2. Clone o repositório e prepare o ambiente:
   ```bash
   git clone https://github.com/folivorasloth/divine-machinery.git
   ```
   ```bash
   cd divine-machinery && python3 -m venv .venv && source .venv/bin/activate
   ```
   ```bash
   pip install -r requirements.txt && cp config.example.yaml config.yaml
   ```
3. No `config.yaml`, coloque o IP do homelab em `mqtt.host`.
4. Rode o publicador (deixe num terminal, ou configure para iniciar com o sistema):
   ```bash
   python -m publisher.aw_publisher
   ```
5. Em outro terminal, converse:
   ```bash
   python -m cli.chat
   ```

Para iniciar o publicador junto com o sistema: no Linux, use um serviço `systemd --user`
com `WorkingDirectory` apontando para o repo e `ExecStart=.venv/bin/python -m publisher.aw_publisher`.
No Windows, use o Agendador de Tarefas ("Ao fazer logon").

### Voz (Piper, opcional)

O TTS roda **no notebook**, que tem caixa de som, e não no homelab. O `cli/chat.py` fala tudo
que chega em `npc/+/fala`, inclusive os comentários espontâneos.

1. Baixe o binário do [Piper](https://github.com/rhasspy/piper/releases) (ou `pip install piper-tts`).
2. Baixe uma voz pt_BR, por exemplo `pt_BR-faber-medium.onnx` e o `.onnx.json` correspondente, em
   [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR).
3. Para o inglês, baixe uma voz `en_GB` (ex.: `en_GB-alan-medium`, em
   [en/en_GB](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_GB)). Ouça as opções em
   [piper-samples](https://rhasspy.github.io/piper-samples).
4. No `config.yaml`: `tts.enabled: true`, `tts.models.pt` e `tts.models.en` com os caminhos das vozes e,
   se precisar, ajuste `synth_command`/`play_command` (há exemplos comentados no arquivo).

Cada fala chega com o idioma em `meta.lang`, e o chat escolhe a voz certa. Não existe voz do Piper
de "britânico falando português"; para experimentar o efeito, aponte `tts.models.pt` para a voz
`en_GB` (o sotaque fica forte, e às vezes a fala fica difícil de entender).

Teste rápido sem MQTT: `python -m grilo.main --offline` com o TTS ligado.

## 4. Vendo tudo passar no MQTT

De qualquer máquina com `mosquitto-clients` (`sudo apt install mosquitto-clients`):

```bash
mosquitto_sub -h IP_DO_HOMELAB -t "#" -v
```

Para falar com o grilo sem o chat (texto puro também funciona):

```bash
mosquitto_pub -h IP_DO_HOMELAB -t joao/fala -m "esse grilo só fala bosta"
```

## Como o grilo decide

1. **Fala do João** → a categoria (insulto, elogio, pergunta ou genérico) é detectada por
   palavras-chave do `persona/grilo.yaml` → o humor é ajustado pelos `mood_effects`.
2. **Emburrado?** Responde `hmpf` ou nem responde.
3. **LLM:** o prompt junta a persona, os exemplos, o humor atual (e uma instrução extra se estiver
   irritado ou em modo retruque), a hora, a atividade atual e as últimas N mensagens.
4. **LLM falhou** (timeout ou offline) → um template da categoria. Em modo retruque, a categoria
   `retruque`. De vez em quando ele avisa que "o Oráculo saiu de casa".

**Humor:** `irritacao`, `orgulho` e `energia` vão de 0 a 100 e voltam para a base com o tempo
(`decay_per_minute`). Se a irritação passa de `irritation_limit`:
- com orgulho ≥ `pride_high`, ele **retruca** pesado;
- com orgulho baixo, fica **emburrado** por `sulk_minutes`.

**Gatilhos espontâneos:** sessão longa no mesmo app, volta de AFK, madrugada e distração.
Cada um tem um `cooldown_minutes`, e há um teto global `global_max_per_hour`. Dispara no
máximo um gatilho por atualização. Emburrado, ele ignora os gatilhos.

## Idioma

`common/language.py` conta palavras comuns de cada língua na sua fala ("the", "you", "what" x "que",
"você", "não", e acentos). Quem tiver mais vence; empate ("ok", "kkkk") mantém o idioma atual. O grilo
responde no idioma detectado, e os comentários espontâneos saem no último idioma que você usou.
- **Com LLM:** a última instrução do prompt diz o idioma (`language_instructions` na persona).
- **Sem LLM:** os templates usados são os do bloco daquele idioma (`templates.pt` / `templates.en`).

## Ajustando a personalidade

Tudo fica em `persona/grilo.yaml`, sem mexer em código: prompt de sistema, instruções de idioma,
exemplos, palavras-chave (dos dois idiomas), efeitos no humor e templates (um bloco por idioma). O teste `test_real_persona_file_is_valid` verifica se o YAML continua
válido depois da edição:

```bash
python -m pytest
```

## Próximos personagens

Ainda não implementado, mas a estrutura já está preparada:
- `common/` (bus, mensagens, config, TTS) é reaproveitado por qualquer NPC;
- um novo NPC vira uma pasta própria (ex.: `olho/`) com sua persona em `persona/olho.yaml`
  e o mesmo padrão de tópicos `npc/olho/...`;
- para os NPCs conversarem entre si, cada um assina `npc/+/fala`. Já existe a constante
  `ALL_NPC_SPEECH` e um comentário marcando o ponto de extensão em `grilo/main.py`;
- o listener WebSocket (porta 9001) já está pronto para telas no navegador.
