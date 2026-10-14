"""A(그냥 STT) / B(전처리 + 힌트 STT + 문맥 보정) 두 경로를 한 번에 실행하는 엔진."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import audio
from .llm import LLMProvider
from .metrics import cer
from .stt import STTProvider


def _ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))


@dataclass
class PipelineInput:
    source: Path          # 업로드/샘플 원본 (아무 포맷)
    raw_wav: Path         # 16k 모노 wav 저장 위치
    pre_wav: Path         # 전처리본 저장 위치
    is_elderly: bool = True
    region_name: str = "모름/표준어"
    stt_hint: str = ""
    dictionary: str = ""
    context: str = ""
    reference: str | None = None
    options: audio.PreprocessOptions = field(default_factory=audio.PreprocessOptions)


def run_pipeline(inp: PipelineInput, stt: STTProvider, llm: LLMProvider) -> dict:
    # [공통] 16 kHz · 16 bit · 모노 변환
    t0 = time.perf_counter()
    audio.convert_to_wav(inp.source, inp.raw_wav)
    convert_ms = _ms(t0)
    duration = audio.duration_s(inp.raw_wav)

    def path_a() -> dict:
        t = time.perf_counter()
        text = stt.transcribe(inp.raw_wav)
        return {"text": text, "ms": _ms(t)}

    def path_b() -> dict:
        steps = {}
        t = time.perf_counter()
        audio.preprocess(inp.raw_wav, inp.pre_wav, inp.options)
        steps["preprocess"] = _ms(t)

        t = time.perf_counter()
        raw_text = stt.transcribe(inp.pre_wav, prompt=inp.stt_hint or None)
        steps["stt"] = _ms(t)

        t = time.perf_counter()
        corrected = llm.correct(
            raw_text,
            is_elderly=inp.is_elderly,
            region_name=inp.region_name,
            context=inp.context,
            dictionary=inp.dictionary,
        )
        steps["correct"] = _ms(t)
        return {
            "raw_text": raw_text,
            "dialect_text": corrected["dialect_text"],
            "standard_text": corrected["standard_text"],
            "rejected": corrected.get("rejected", []),  # 발음이 달라 채택하지 않은 LLM 수정
            "ms": sum(steps.values()),
            "steps_ms": steps,
        }

    # A 와 B 는 서로 독립이므로 동시에 실행 (각 경로의 ms 는 자기 작업 시간만 잰다)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa, fb = pool.submit(path_a), pool.submit(path_b)
        a, b = fa.result(), fb.result()

    a["cer"] = cer(inp.reference, a["text"])
    b["cer"] = cer(inp.reference, b["dialect_text"])
    b["cer_standard"] = cer(inp.reference, b["standard_text"])
    return {"duration_s": duration, "convert_ms": convert_ms, "a": a, "b": b}
