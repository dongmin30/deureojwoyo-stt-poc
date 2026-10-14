import numpy as np
import pytest
import soundfile as sf

from app import audio
from tests.conftest import SR, silence, voice_like


def test_ffmpeg_available():
    assert audio.ffmpeg_exe()


def test_convert_to_16k_mono_16bit(speech_wav, tmp_path):
    out = tmp_path / "out.wav"
    audio.convert_to_wav(speech_wav, out)
    info = sf.info(str(out))
    assert info.samplerate == 16000
    assert info.channels == 1
    assert info.subtype == "PCM_16"
    assert audio.duration_s(out) == pytest.approx(3.5, abs=0.05)


def test_convert_rejects_non_audio(tmp_path):
    bad = tmp_path / "bad.webm"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(audio.AudioError):
        audio.convert_to_wav(bad, tmp_path / "out.wav")


@pytest.mark.parametrize("backend", ["webrtc", "energy"])
def test_vad_shortens_long_pause(backend):
    rng = np.random.default_rng(0)
    pause = silence(1.5) + rng.normal(0, 0.0005, int(1.5 * SR)).astype(np.float32)  # 약한 배경 잡음
    x = np.concatenate([voice_like(1.0), pause, voice_like(1.0)])
    y = audio.shorten_pauses(x, SR, backend=backend)
    # 1.5초 멈춤 → 약 0.3초 (VAD 프레임 경계·행오버 오차 허용)
    pause_left = len(y) / SR - 2.0
    assert 0.25 <= pause_left <= 0.5


@pytest.mark.parametrize("backend", ["webrtc", "energy"])
def test_vad_keeps_short_pause(backend):
    x = np.concatenate([voice_like(1.0), silence(0.5), voice_like(1.0)])
    y = audio.shorten_pauses(x, SR, backend=backend)
    assert len(y) == len(x)


def test_vad_all_silence_unchanged():
    x = silence(2.0)
    assert len(audio.shorten_pauses(x, SR)) == len(x)


def test_preprocess_all_steps(tmp_path, speech_wav):
    raw = tmp_path / "raw.wav"
    pre = tmp_path / "pre.wav"
    audio.convert_to_wav(speech_wav, raw)
    audio.preprocess(raw, pre, audio.PreprocessOptions(loudnorm=True, vad=True, denoise=True, tempo=True))
    info = sf.info(str(pre))
    assert (info.samplerate, info.channels, info.subtype) == (16000, 1, "PCM_16")
    # 3.5초 → 무음 단축(약 2.3초) → 1.15배 압축(약 2.0초)
    assert audio.duration_s(pre) == pytest.approx(2.3 / 1.15, abs=0.15)


def test_preprocess_all_off_copies(tmp_path, speech_wav):
    raw = tmp_path / "raw.wav"
    pre = tmp_path / "pre.wav"
    audio.convert_to_wav(speech_wav, raw)
    audio.preprocess(raw, pre, audio.PreprocessOptions(loudnorm=False, vad=False, denoise=False, tempo=False))
    assert pre.read_bytes() == raw.read_bytes()
