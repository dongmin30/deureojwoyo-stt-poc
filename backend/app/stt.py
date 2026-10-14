"""STT 공급자: openai(Whisper API) / local(faster-whisper) / mock."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .config import Settings

log = logging.getLogger("poc.stt")


class STTError(RuntimeError):
    pass


class STTProvider:
    name = "base"

    def transcribe(self, wav_path: Path, prompt: str | None = None) -> str:
        raise NotImplementedError


class OpenAISTT(STTProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def transcribe(self, wav_path: Path, prompt: str | None = None) -> str:
        kwargs = {"model": self.model, "language": "ko"}
        if prompt:
            kwargs["prompt"] = prompt
        try:
            with open(wav_path, "rb") as f:
                resp = self.client.audio.transcriptions.create(file=f, **kwargs)
        except Exception as e:  # noqa: BLE001
            raise STTError(f"OpenAI STT 호출 실패: {e}") from e
        return (getattr(resp, "text", "") or "").strip()


def register_pip_cuda_dlls() -> list[str]:
    """pip 로 설치한 NVIDIA 라이브러리(nvidia-cublas-cu12, nvidia-cudnn-cu12)의 DLL 폴더를 Windows 검색 경로에 넣는다.

    CTranslate2 는 cublas64_12.dll / cudnn*.dll 을 실행 중에 찾는데, pip 휠은 site-packages/nvidia/*/bin 에 두므로
    따로 알려 주지 않으면 'cublas64_12.dll is not found' 로 실패한다.
    """
    if os.name != "nt":
        return []
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    if not spec or not spec.submodule_search_locations:
        return []
    added = []
    for base in spec.submodule_search_locations:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            d = Path(base) / sub / "bin"
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(d))
    return added


class LocalWhisperSTT(STTProvider):
    """faster-whisper. 모델은 첫 호출 때 한 번만 불러온다."""

    name = "local"

    def __init__(self, model_size: str, device: str = "cpu"):
        self.model_size = model_size
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                if self.device != "cpu":
                    register_pip_cuda_dlls()
                # GPU 는 float16 이 표준(빠르고 정확), CPU 는 int8 이 가장 빠르다
                compute_type = "int8" if self.device == "cpu" else "float16"
                self._model = WhisperModel(self.model_size, device=self.device, compute_type=compute_type)
            return self._model

    def transcribe(self, wav_path: Path, prompt: str | None = None) -> str:
        try:
            import soundfile as sf

            # 파이프라인이 이미 16kHz 모노 wav 로 변환해 두므로 직접 읽어 넘긴다.
            # (경로를 넘기면 faster-whisper 가 PyAV 로 디코딩하는데, av 19 와 호환되지 않음)
            audio, _ = sf.read(str(wav_path), dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            try:
                return self._run(audio, prompt)
            except RuntimeError as e:
                # 시연 중 GPU 라이브러리 문제(cuBLAS/cuDNN 없음, 드라이버 오류 등)가 나면 CPU 로 바꿔 한 번 더 시도
                if self.device == "cpu":
                    raise
                log.warning("GPU 인식 실패, CPU 로 전환합니다: %s", e)
                with self._lock:
                    self.device, self._model = "cpu", None
                return self._run(audio, prompt)
        except Exception as e:  # noqa: BLE001
            raise STTError(f"로컬 Whisper 인식 실패: {e}") from e

    def _run(self, audio, prompt: str | None) -> str:
        segments, _ = self._get_model().transcribe(audio, language="ko", initial_prompt=prompt or None, beam_size=5)
        return " ".join(s.text.strip() for s in segments).strip()


class MockSTT(STTProvider):
    """키 없이 화면을 확인하기 위한 가짜 STT. 방언 힌트가 있으면(B 경로) 방언을 제대로 알아들은 척한다."""

    name = "mock"
    HEARD_WITH_HINT = "할매 밥 뭇나 오늘 정구지 찌짐 해 묵자 병원 예약 할라카는데 우째 해야 되노"
    HEARD_PLAIN = "할매 밥 묻나 오늘 정구 지 찜 해 묶자 병원 예약 할라 카는데 어째 해야 되나"

    def transcribe(self, wav_path: Path, prompt: str | None = None) -> str:
        return self.HEARD_WITH_HINT if prompt else self.HEARD_PLAIN


def make_stt(settings: Settings) -> STTProvider:
    if settings.stt_provider == "openai":
        return OpenAISTT(settings.openai_api_key, settings.openai_stt_model)
    if settings.stt_provider == "local":
        # 기본은 cpu. "cuda" 는 NVIDIA GPU + CUDA 12 cuBLAS/cuDNN 필요 (pip: backend/requirements-gpu.txt)
        return LocalWhisperSTT(settings.local_whisper_model, os.getenv("LOCAL_WHISPER_DEVICE", "cpu").strip() or "cpu")
    if settings.stt_provider == "mock":
        return MockSTT()
    raise ValueError(f"알 수 없는 STT_PROVIDER: {settings.stt_provider}")
