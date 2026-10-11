"""Explicit, short PCM microphone chunks using an owner-selected local transcriber."""
import base64
import io
import json
from pathlib import Path
import tempfile
import wave
from pydantic import BaseModel, Field
from .providers import RouteError
from .isolation import command


class VoiceConfig(BaseModel):
    argv: list[str] = Field(default_factory=list, max_length=30)
    trusted: bool = False


class VoiceChunk(BaseModel):
    wav: str = Field(min_length=1, max_length=500000)


async def transcribe(store, root, form):
    config = store.get("settings", "voice", {})
    if not config.get("trusted") or not config.get("argv"): raise RouteError("Configure and trust a local transcription command first")
    try:
        data = base64.b64decode(form.wav, validate=True)
        with wave.open(io.BytesIO(data)) as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000 or not 1 <= audio.getnframes() <= 160000 or audio.getcomptype() != "NONE": raise ValueError()
            if len(audio.readframes(audio.getnframes())) != audio.getnframes()*2: raise ValueError()
    except Exception as e: raise RouteError("Use a complete 16 kHz mono PCM16 WAV chunk of at most ten seconds") from e
    if len(data) > 320100: raise RouteError("Audio chunk exceeds the limit")
    with tempfile.TemporaryDirectory(prefix="voice-", dir=root) as folder:
        source = Path(folder)/"input.wav"; output = Path(folder)/"transcript"
        source.write_bytes(data)
        try: argv = [v.format_map(dict(input=str(source), output=str(output))) for v in config["argv"]]
        except (ValueError, KeyError) as e: raise RouteError("Voice commands support {input} and {output} placeholders") from e
        text = await command(argv, cwd=folder, timeout=45, limit=100000)
        file = output.with_suffix(".txt")
        if file.exists():
            if file.is_symlink() or file.stat().st_size > 100000: raise RouteError("Invalid transcription output")
            text = file.read_text(encoding="utf-8")
        return dict(text=text.strip()[:12000], local=True, note="Audio chunks are deleted after transcription; edit the transcript before sending it")


def validate_voice_config(form):
    if form.trusted:
        if not form.argv or not Path(form.argv[0]).is_absolute() or not Path(form.argv[0]).is_file(): raise RouteError("Select an existing absolute executable path")
        if len(json.dumps(form.argv)) > 12000 or any(not v or len(v) > 4000 for v in form.argv): raise RouteError("Invalid voice command")
        try:
            for v in form.argv: v.format_map(dict(input="input.wav", output="transcript"))
        except (KeyError, ValueError) as e: raise RouteError("Only {input} and {output} placeholders are supported") from e
    return form.model_dump()
