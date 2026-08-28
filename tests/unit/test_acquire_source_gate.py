"""A file named source.mp4 is not the same thing as a usable source.

The incident: ffmpeg was not on the child PATH, so yt-dlp could not mux
the separate 1080p video and audio streams, fell down its format list to
a single-file `best`, and wrote 360p under the expected name. Every
artifact existed, the stage reported ok, and an hour of transcription
plus two LLM stages then ran against pixels a 9:16 crop cannot use.
"""

from __future__ import annotations

import pytest

from yvc.stages import s01_acquire


@pytest.fixture
def base(tmp_path):
    work = tmp_path / "work" / "vid"
    work.mkdir(parents=True)
    return work


def _stub_height(monkeypatch, height: int) -> None:
    monkeypatch.setattr(
        s01_acquire, "video_stream",
        lambda path: {
            "info": {"format": {"duration": "10.0", "size": "1"}},
            "video": {"width": height * 16 // 9, "height": height,
                      "r_frame_rate": "25/1"},
            "height": height,
        },
    )


def test_existing_low_resolution_source_is_set_aside_not_reused(monkeypatch, base):
    (base / "source.mp4").write_bytes(b"360p stand-in")
    _stub_height(monkeypatch, 360)
    monkeypatch.setattr(s01_acquire, "_ffmpeg_dir", lambda: "C:/ffmpeg/bin")

    seen: list[list[str]] = []

    def fake_run(cmd, timeout=5400):
        seen.append(cmd)  # deliberately does not create source.mp4
        return _completed()

    monkeypatch.setattr(s01_acquire, "_run", fake_run)

    with pytest.raises(RuntimeError, match="download failed"):
        s01_acquire.acquire("https://youtu.be/x", base, {})

    assert not (base / "source.mp4").exists()
    assert (base / "source.rejected-360p.mp4").exists(), (
        "the unusable file is kept as evidence, under a name nothing reads"
    )
    assert seen, "a rejected source must lead to a download, not a skip"
    assert "--ffmpeg-location" in seen[0], (
        "yt-dlp mixes the video and audio streams itself and needs to be "
        "told where ffmpeg is; without this the format list silently falls "
        "back to a low-resolution single file"
    )


def test_download_that_lands_below_the_minimum_stops_the_run(monkeypatch, base):
    _stub_height(monkeypatch, 360)
    monkeypatch.setattr(s01_acquire, "_ffmpeg_dir", lambda: "C:/ffmpeg/bin")

    def fake_run(cmd, timeout=5400):
        (base / "source.mp4").write_bytes(b"still 360p")
        return _completed()

    monkeypatch.setattr(s01_acquire, "_run", fake_run)

    with pytest.raises(RuntimeError, match="below the 720p minimum"):
        s01_acquire.acquire("https://youtu.be/x", base, {})

    assert not (base / "audio16k_raw.wav").exists(), (
        "nothing downstream should start on a source that was refused"
    )


def test_a_source_at_the_minimum_is_accepted(monkeypatch, base):
    (base / "source.mp4").write_bytes(b"720p stand-in")
    (base / "audio16k_raw.wav").write_bytes(b"already extracted")
    _stub_height(monkeypatch, 720)
    monkeypatch.setattr(s01_acquire, "_run", lambda cmd, timeout=5400: _completed())

    payload = s01_acquire.acquire("https://youtu.be/x", base, {})

    assert payload["height"] == 720
    assert (base / "source.mp4").exists()


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _completed() -> _Completed:
    return _Completed()
