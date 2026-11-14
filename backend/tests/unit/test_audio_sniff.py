"""doc 02 §3: formats are identified by magic bytes only."""

import pytest

from app.modules.audio.media import sniff_format


@pytest.mark.parametrize(("head", "fmt"), [
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
    (b"ID3\x04\x00\x00\x00\x00\x00\x00", "mp3"),
    (b"\xff\xfb\x90\x00", "mp3"),          # MPEG-1 Layer III frame sync
    (b"\xff\xf1\x50\x80", "aac"),          # ADTS AAC (layer bits 00)
    (b"\x00\x00\x00\x20ftypM4A ", "m4a"),
    (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", "webm"),
    (b"OggS\x00\x02\x00\x00", "ogg"),
])
def test_accepted(head: bytes, fmt: str) -> None:
    assert sniff_format(head) == fmt


@pytest.mark.parametrize("head", [b"", b"MZ\x90\x00", b"%PDF-1.7", b"RIFF\x00\x00\x00\x00AVI ", b"\x89PNG\r\n"])
def test_rejected(head: bytes) -> None:
    assert sniff_format(head) is None
