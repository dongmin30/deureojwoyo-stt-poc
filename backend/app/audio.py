"""오디오 변환(ffmpeg)과 B 경로 전처리."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
TEMPO = 1.15
LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"


class AudioError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """PATH 의 ffmpeg 우선, 없으면 imageio-ffmpeg 번들 바이너리."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise AudioError("ffmpeg 를 찾을 수 없습니다. ffmpeg 를 설치하거나 imageio-ffmpeg 패키지를 설치하세요.") from e


def _run_ffmpeg(src: Path, dst: Path, audio_filter: str | None = None) -> None:
    """src 를 16 kHz · 16 bit · 모노 wav 로 dst 에 쓴다. audio_filter 가 있으면 함께 적용."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn"]
    if audio_filter:
        cmd += ["-af", audio_filter]
    cmd += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not dst.exists():
        err = proc.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise AudioError(f"ffmpeg 처리 실패: {err or '알 수 없는 오류'}")


def convert_to_wav(src: Path, dst: Path) -> None:
    _run_ffmpeg(Path(src), Path(dst))


def duration_s(wav: Path) -> float:
    info = sf.info(str(wav))
    return round(info.frames / info.samplerate, 3)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


def write_wav(path: Path, samples: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    sf.write(str(path), np.clip(samples, -1.0, 1.0), sr, subtype="PCM_16")


# ---------------------------------------------------------------- VAD

def _energy_speech_flags(frames: np.ndarray) -> np.ndarray:
    """webrtcvad 가 없을 때 쓰는 에너지 기반 판정: 상위 에너지 대비 -35 dB 이상이면 음성."""
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    ref = np.percentile(db, 95)
    return (db > ref - 35) & (db > -60)


def _webrtc_speech_flags(frames: np.ndarray, sr: int, aggressiveness: int = 2) -> np.ndarray:
    import webrtcvad  # webrtcvad-wheels

    vad = webrtcvad.Vad(aggressiveness)
    pcm = (np.clip(frames, -1.0, 1.0) * 32767).astype("<i2")
    return np.array([vad.is_speech(f.tobytes(), sr) for f in pcm], dtype=bool)


def speech_flags(samples: np.ndarray, sr: int, frame_ms: int = 30, backend: str = "auto") -> tuple[np.ndarray, int]:
    """프레임별 음성 여부와 프레임 길이(샘플 수). backend: auto|webrtc|energy"""
    frame_len = int(sr * frame_ms / 1000)
    n = len(samples) // frame_len
    if n == 0:
        return np.zeros(0, dtype=bool), frame_len
    frames = samples[: n * frame_len].reshape(n, frame_len)
    if backend in ("auto", "webrtc"):
        try:
            flags = _webrtc_speech_flags(frames, sr)
            # 디지털 무음(에너지 거의 0)은 webrtcvad 판정과 무관하게 무음 처리
            silent = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) < 1e-4
            return flags & ~silent, frame_len
        except Exception as e:  # noqa: BLE001
            if backend == "webrtc":
                raise
            log.warning("webrtcvad 사용 불가, 에너지 VAD 로 대체: %s", e)
    return _energy_speech_flags(frames), frame_len


def shorten_pauses(
    samples: np.ndarray,
    sr: int,
    max_pause_s: float = 0.7,
    target_pause_s: float = 0.3,
    backend: str = "auto",
) -> np.ndarray:
    """max_pause_s 보다 긴 무음 구간을 target_pause_s 로 줄인다 (앞뒤로 절반씩 남김)."""
    flags, frame_len = speech_flags(samples, sr, backend=backend)
    if len(flags) == 0 or not flags.any():
        return samples
    keep = np.ones(len(samples), dtype=bool)
    half = int(target_pause_s * sr / 2)
    i = 0
    while i < len(flags):
        if flags[i]:
            i += 1
            continue
        j = i
        while j < len(flags) and not flags[j]:
            j += 1
        start, end = i * frame_len, j * frame_len
        if j == len(flags):
            end = len(samples)  # 마지막 프레임 뒤 자투리도 무음 구간에 포함
        if (end - start) / sr > max_pause_s:
            keep[start + half: end - half] = False
        i = j
    return samples[keep]


# ---------------------------------------------------------------- 전처리

@dataclass
class PreprocessOptions:
    # 실측(README "전처리 기본값")상 음량 정규화·속도 보정은 Whisper 인식을 망쳐 기본으로 끈다
    loudnorm: bool = False
    vad: bool = True
    denoise: bool = False
    tempo: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {"loudnorm": self.loudnorm, "vad": self.vad, "denoise": self.denoise, "tempo": self.tempo}


def denoise(samples: np.ndarray, sr: int) -> np.ndarray:
    import noisereduce as nr

    return nr.reduce_noise(y=samples, sr=sr).astype(np.float32)


def preprocess(src_wav: Path, dst_wav: Path, opts: PreprocessOptions) -> None:
    """음량 정규화 → 잡음 제거 → 긴 무음 단축 → 속도 보정 순서로 적용해 dst_wav 에 저장."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        current = Path(src_wav)
        if opts.loudnorm:
            out = tmp / "loudnorm.wav"
            _run_ffmpeg(current, out, LOUDNORM_FILTER)
            current = out

        if opts.denoise or opts.vad:
            samples, sr = read_wav(current)
            if opts.denoise and len(samples) > 0:
                samples = denoise(samples, sr)
            if opts.vad:
                samples = shorten_pauses(samples, sr)
            out = tmp / "numpy.wav"
            write_wav(out, samples, sr)
            current = out

        if opts.tempo:
            out = tmp / "tempo.wav"
            _run_ffmpeg(current, out, f"atempo={TEMPO}")
            current = out

        if current == Path(src_wav):
            shutil.copyfile(current, dst_wav)
        else:
            shutil.move(str(current), str(dst_wav))
