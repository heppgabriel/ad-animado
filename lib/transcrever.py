"""Transcreve a locução palavra por palavra (faster-whisper, em CPU) para gerar o SRT do ad.
  python lib/transcrever.py <audio-ou-video> <saida.json>      -> {"texto": ..., "whisper": {...}}"""
import os, sys, json, tempfile, subprocess
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum                                               # põe o ffmpeg do Homebrew no PATH

def transcrever(video, saida):
    from transcricao import transcrever_arquivo
    fd, wav = tempfile.mkstemp(suffix=".wav"); os.close(fd)
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
        r = transcrever_arquivo(wav)
    finally: os.remove(wav)
    texto = " ".join(w["word"].strip() for s in r["segments"] for w in s.get("words", []) if w["word"].strip())
    json.dump(dict(texto=texto, whisper=r), open(saida, "w"), ensure_ascii=False)
    return texto

if __name__ == "__main__":
    print(transcrever(sys.argv[1], sys.argv[2])[:300])
