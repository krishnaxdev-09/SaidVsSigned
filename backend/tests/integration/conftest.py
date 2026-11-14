"""Shared fixtures for integration tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def tone_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("stt-audio")
    out = {}
    for name, freq in (("a", 440), ("b", 550)):
        out[name] = d / f"{name}.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency={freq}:duration=2", "-ac", "1", "-c:a", "pcm_s16le", str(out[name])], check=True)
    out["stereo"] = d / "stereo.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=2", "-filter_complex",
                    "[0:a][1:a]join=inputs=2:channel_layout=stereo", "-c:a", "pcm_s16le", str(out["stereo"])], check=True)
    return out
