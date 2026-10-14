import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SR = 16000


def voice_like(seconds: float, sr: int = SR, f0: float = 180.0) -> np.ndarray:
    """기본 주파수 + 배음 + 음절 같은 진폭 변조를 넣은 합성 '음성'."""
    t = np.arange(int(seconds * sr)) / sr
    sig = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)
    return (0.3 * sig / np.max(np.abs(sig)) * envelope).astype(np.float32)


def silence(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


@pytest.fixture
def speech_wav(tmp_path) -> Path:
    """음성 1초 - 무음 1.5초 - 음성 1초 (44.1 kHz 스테레오 → 변환 테스트용)."""
    sr = 44100
    mono = np.concatenate([voice_like(1.0, sr), silence(1.5, sr), voice_like(1.0, sr)])
    path = tmp_path / "input.wav"
    sf.write(path, np.stack([mono, mono], axis=1), sr, subtype="PCM_24")
    return path


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from app.config import Settings

    samples = tmp_path / "samples"
    samples.mkdir()
    mono = np.concatenate([voice_like(1.0), silence(1.2), voice_like(1.0)])
    sf.write(samples / "demo.wav", mono, SR, subtype="PCM_16")
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_MODEL", "ACCESS_CODE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STT_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("DATA_DIR", str(FIXTURES))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("SAMPLES_DIR", str(samples))
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "no-dist"))  # 테스트는 빌드 결과에 의존하지 않음
    return Settings.from_env()


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(settings)) as c:
        yield c
