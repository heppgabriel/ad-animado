"""Whisper large-v3-turbo em CPU, com o contrato de timestamps do editor."""
import math
import os
import threading

_modelo = None
_trava = threading.Lock()


def modelo():
    global _modelo
    if _modelo is None:
        from faster_whisper import WhisperModel
        from comum import MODELOS
        _modelo = WhisperModel(
            os.environ.get("AD_ANIMADO_WHISPER_MODELO", "large-v3-turbo"),
            device="cpu", compute_type="int8",
            cpu_threads=max(1, int(os.environ.get("AD_ANIMADO_WHISPER_THREADS") or os.cpu_count() or 8)),
            download_root=os.path.join(MODELOS, "whisper"),
        )
    return _modelo


def converter_segmentos(segmentos):
    """Não tira espaços nem inventa alinhamento: sem tempo por palavra, para antes de cortar."""
    resultado = []
    for s in segmentos:
        palavras = []
        for w in s.words or []:
            if w.start is None or w.end is None:
                raise ValueError("Whisper retornou uma palavra sem timestamps; a edição foi interrompida.")
            a, b = float(w.start), float(w.end)
            if not (math.isfinite(a) and math.isfinite(b) and 0 <= a <= b):
                raise ValueError("Whisper retornou timestamps inválidos.")
            palavras.append(dict(word=w.word, start=a, end=b))
        if s.text.strip() and not palavras:
            raise ValueError("Whisper retornou fala sem alinhamento por palavra; a edição foi interrompida.")
        resultado.append(dict(start=float(s.start), end=float(s.end), text=s.text, words=palavras))
    return {"segments": resultado}


def _carregar(audio):
    """Áudio -> float32 mono 16 kHz pelo ffmpeg, sem passar pelo PyAV (versões novas do PyAV quebram o
    decode_audio do faster-whisper: open() got an unexpected keyword argument 'metadata_errors')."""
    import subprocess, numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", audio, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"], capture_output=True)
    if r.returncode: raise RuntimeError("ffmpeg não conseguiu ler o áudio: " + r.stderr.decode("utf-8", "replace")[-200:])
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def transcrever_arquivo(audio, prompt=""):
    # O gerador executa a inferência ao iterar; manter a trava até terminar evita picos de RAM.
    with _trava:
        segmentos, _ = modelo().transcribe(
            _carregar(audio), language="pt", word_timestamps=True, initial_prompt=prompt or None,
            condition_on_previous_text=False, vad_filter=False,
        )
        return converter_segmentos(segmentos)
