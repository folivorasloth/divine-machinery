"""Voz via Piper TTS (opcional).

Roda na máquina que tem caixa de som (o notebook, pelo cli/chat.py), não no homelab.
As falas entram numa fila e são ditas uma de cada vez, numa thread separada,
para não travar o terminal enquanto o Piper sintetiza.
"""

import logging
import queue
import shlex
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("tts")


class Speaker:
    def __init__(self, config: dict):
        self.enabled = bool(config.get("enabled"))
        self.model = str(Path(config.get("model", "")).expanduser())
        self.synth_command = config.get("synth_command", "piper --model {model} --output_file {wav}")
        self.play_command = config.get("play_command", "aplay -q {wav}")
        self._queue: queue.Queue[str] = queue.Queue()
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True).start()
            log.info("TTS ligado (modelo %s)", self.model)

    def say(self, text: str) -> None:
        if self.enabled and text:
            self._queue.put(text)

    def _worker(self) -> None:
        while True:
            self._speak(self._queue.get())

    def _speak(self, text: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = str(Path(tmp) / "fala.wav")
            synth = self._command(self.synth_command, wav)
            play = self._command(self.play_command, wav)
            try:
                subprocess.run(synth, input=text.encode("utf-8"), check=True,
                               capture_output=True, timeout=30)
                subprocess.run(play, check=True, capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as e:
                log.warning("TTS falhou: %s", e)

    def _command(self, template: str, wav: str) -> list[str]:
        # Divide primeiro e substitui depois, para caminhos com espaço não quebrarem.
        return [part.format(model=self.model, wav=wav) for part in shlex.split(template)]
