"""Audio media helpers for T1.1 (doc 02 §3, §5): magic-byte detection, ffprobe, ffmpeg normalisation, silence check,
snippet cutting. All ffmpeg/ffprobe calls run as subprocesses with timeouts. A timeout is transient (retried by the
job policy); an undecodable file is an input error (never retried)."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.retry import InputError, TransientError

# Formats accepted by doc 02 §3. Identified only by magic bytes, never by extension or client MIME type.
FORMATS = ("wav", "mp3", "aac", "m4a", "webm", "ogg")
CONTENT_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "aac": "audio/aac", "m4a": "audio/mp4",
                 "webm": "audio/webm", "ogg": "audio/ogg"}

# Silence check (doc 02 §6): "RMS below threshold for > 95% of the file" -> failed(no_speech_detected).
SILENCE_NOISE_DB = -50
SILENCE_MIN_S = 0.5
SILENT_FRACTION = 0.95


def sniff_format(head: bytes) -> str | None:
    """Return the container/codec family from the first bytes of a file, or None if not an accepted format."""
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[:4] == b"OggS":
        return "ogg"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        # 11-bit frame sync. Layer bits 00 = ADTS AAC; otherwise an MPEG audio (MP3) frame.
        return "aac" if (head[1] & 0x06) == 0 else "mp3"
    return None


async def _run(args: list[str], timeout_s: float) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise TransientError(f"{args[0]} timed out after {timeout_s}s") from exc
    return proc.returncode or 0, out, err


@dataclass
class Probe:
    codec: str
    channels: int
    duration_ms: int | None


async def probe(path: Path, timeout_s: float) -> Probe:
    code, out, _ = await _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                               str(path)], timeout_s)
    if code != 0:
        raise InputError("file could not be decoded", "unsupported_media")
    data = json.loads(out or b"{}")
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        raise InputError("no audio stream", "unsupported_media")
    a = audio[0]
    dur = a.get("duration") or data.get("format", {}).get("duration")
    return Probe(codec=a.get("codec_name", "unknown"), channels=int(a.get("channels") or 0),
                 duration_ms=round(float(dur) * 1000) if dur not in (None, "N/A") else None)


async def _ffmpeg(args: list[str], timeout_s: float) -> None:
    code, _, err = await _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args], timeout_s)
    if code != 0:
        raise InputError(f"ffmpeg failed: {err.decode(errors='replace')[-300:]}", "unsupported_media")


async def normalise(src: Path, out_dir: Path, *, stereo_split: bool, timeout_s: float) -> dict[str, Path]:
    """doc 02 §5 step 4. STT copy: mono 16 kHz s16 WAV with loudnorm. Playback copy: mono 16 kHz FLAC, no loudnorm.
    stereo_split also writes L.wav / R.wav (one speaker per side)."""
    base = ["-i", str(src), "-map", "0:a:0", "-vn"]
    pcm = ["-ar", "16000", "-sample_fmt", "s16"]
    outs = {"normalized.wav": out_dir / "normalized.wav", "normalized.flac": out_dir / "normalized.flac"}
    await _ffmpeg([*base, "-af", "loudnorm", "-ac", "1", *pcm, str(outs["normalized.wav"])], timeout_s)
    await _ffmpeg([*base, "-ac", "1", *pcm, str(outs["normalized.flac"])], timeout_s)
    if stereo_split:
        for side, ch in (("L", "c0"), ("R", "c1")):
            outs[f"{side}.wav"] = out_dir / f"{side}.wav"
            await _ffmpeg([*base, "-af", f"pan=mono|c0={ch}", *pcm, str(outs[f"{side}.wav"])], timeout_s)
    return outs


async def silent_fraction(path: Path, duration_ms: int, timeout_s: float) -> float:
    code, _, err = await _run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af",
                               f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_S}", "-f", "null", "-"],
                              timeout_s)
    if code != 0:
        raise InputError("file could not be decoded", "unsupported_media")
    log = err.decode(errors="replace")
    total = sum(float(x) for x in re.findall(r"silence_duration: ([0-9.]+)", log))
    starts = re.findall(r"silence_start: ([0-9.]+)", log)
    ends = re.findall(r"silence_end: ([0-9.]+)", log)
    if len(starts) > len(ends):  # silence runs to the end of the file
        total += max(0.0, duration_ms / 1000 - float(starts[-1]))
    return min(1.0, total * 1000 / duration_ms) if duration_ms else 1.0


async def cut_snippet(src: Path, dst: Path, from_ms: int, to_ms: int, timeout_s: float) -> None:
    """Accurate seek (decode-based) on the normalised FLAC, so the range is within ±0.25 s (doc 02 §8)."""
    await _ffmpeg(["-ss", f"{from_ms / 1000:.3f}", "-i", str(src), "-t", f"{(to_ms - from_ms) / 1000:.3f}",
                   "-c:a", "flac", str(dst)], timeout_s)
