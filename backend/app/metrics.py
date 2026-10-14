"""CER(문자 오류율) 계산."""
from __future__ import annotations

import unicodedata

import jiwer


def normalize_for_cer(text: str | None) -> str:
    """공백과 문장부호(유니코드 P*, S* 범주)를 모두 제거."""
    if not text:
        return ""
    return "".join(
        ch for ch in text
        if not ch.isspace() and not unicodedata.category(ch).startswith(("P", "S"))
    )


def cer(reference: str | None, hypothesis: str | None) -> float | None:
    """정답이 비어 있으면 None. 인식 결과가 비어 있으면 1.0(전부 삭제)."""
    ref = normalize_for_cer(reference)
    if not ref:
        return None
    hyp = normalize_for_cer(hypothesis)
    if not hyp:
        return 1.0
    return round(float(jiwer.cer(ref, hyp)), 4)
