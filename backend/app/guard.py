"""LLM 보정 결과를 '발음이 비슷한 수정'만 남기도록 거르는 안전장치.

LLM 은 STT 원문을 고치다가 원문에 없는 말을 지어내거나(STT '맨누리' → LLM '할아버지') 낱말을 빼먹는다.
STT 오류는 '소리가 비슷한 다른 글자'로 적힌 것이므로, 원문과 보정문을 낱말 단위로 맞대어
- 소리(자모)가 충분히 비슷한 교체, 띄어쓰기만 다른 교체, 방언 사전에 있는 교체 → 채택
- 그 밖의 교체, 낱말 삭제, 낱말 추가 → 거부하고 원문 유지
한다.
"""
from __future__ import annotations

from difflib import SequenceMatcher

# 한글 음절 → 초성·중성·종성 (종성 없음은 생략)
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"

# 이 값 이상이면 '소리가 비슷하다'고 본다 (자모 단위 유사도). 실측:
#   채택: 무난↔문안 0.91, 문나↔뭇나 0.80, 복번↔못본 0.50
#   거부: 진하그로↔일찍 0.27, 맨누리↔할아버지 0.25, 정구지↔부추 0.18(사전에 있으면 채택)
SIMILARITY_THRESHOLD = 0.5
# STT 오인식은 대개 음절 수를 유지한다 (간주→간호사 처럼 음절이 늘어난 교체는 지어낸 것으로 본다).
# 긴 구간은 조사·어미 차이를 감안해 1음절까지 허용.


def to_jamo(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_CHO[code // 588])
            out.append(_JUNG[(code % 588) // 28])
            jong = _JONG[code % 28]
            if jong != " ":
                out.append(jong)
        elif not ch.isspace():
            out.append(ch)
    return "".join(out)


def sound_similarity(a: str, b: str) -> float:
    ja, jb = to_jamo(a), to_jamo(b)
    if not ja and not jb:
        return 1.0
    return SequenceMatcher(None, ja, jb, autojunk=False).ratio()


def _strip(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


# 방언 → 표준어 변환은 음절 수가 더 많이 바뀌므로(할라카는데→하려고 하는데) 음절 차는 2까지 허용.
# 실측: 채택 복시롭게→복스럽게 0.80, 할라카는데→하려고하는데 0.72, 동구시리한→동그스름한 0.72, 맨누리는→맏며느리는 0.64
#       거부 진학으로→친절하게 0.50 (LLM 이 지어낸 말)
STANDARD_THRESHOLD = 0.6
STANDARD_MAX_SYLLABLE_DIFF = 2
# STT 가 사전 방언 낱말을 비슷한 소리로 적은 경우(뭇나→문나 0.80)도 사전 낱말로 인정하는 기준
DICT_FUZZY_THRESHOLD = 0.75


def guard_standardization(dialect: str, standard: str, dictionary: dict[str, str] | None = None) -> tuple[str, list[dict]]:
    """방언 문장 → 표준어 문장 변환 중 근거 있는 것(소리가 비슷하거나 사전에 있는 낱말)만 반영."""
    return guard_correction(dialect, standard, dictionary, threshold=STANDARD_THRESHOLD,
                            max_syllable_diff=STANDARD_MAX_SYLLABLE_DIFF, trust_dictionary_source=True)


def guard_correction(raw: str, corrected: str, dictionary: dict[str, str] | None = None, *,
                     threshold: float = SIMILARITY_THRESHOLD, max_syllable_diff: int | None = None,
                     trust_dictionary_source: bool = False) -> tuple[str, list[dict]]:
    """raw 를 기준으로 corrected 의 수정 중 믿을 만한 것만 반영한 문장과, 거부한 수정 목록을 돌려준다.

    max_syllable_diff 가 None 이면 3음절 이하 0, 4음절 이상 1 음절 차이까지 허용.
    trust_dictionary_source 이면 원문 구간에 사전의 방언 낱말이 있을 때 LLM 의 표준어 표현을 그대로 채택.
    """
    raw_words, cor_words = raw.split(), corrected.split()
    dict_pairs = {(_strip(k), _strip(v)) for k, v in (dictionary or {}).items()}
    out: list[str] = []
    rejected: list[dict] = []
    matcher = SequenceMatcher(None, raw_words, cor_words, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        src, dst = " ".join(raw_words[i1:i2]), " ".join(cor_words[j1:j2])
        if op == "equal":
            out.append(src)
        elif op == "replace" and (i2 - i1) == (j2 - j1) > 1:
            # 낱말 수가 같으면 낱말끼리 하나씩 판단 (같은 낱말이 섞여 유사도가 부풀려지는 것을 막음)
            for sw, dw in zip(raw_words[i1:i2], cor_words[j1:j2]):
                if _accept(sw, dw, dict_pairs, threshold, max_syllable_diff, trust_dictionary_source):
                    out.append(dw)
                else:
                    out.append(sw)
                    rejected.append({"from": sw, "to": dw, "reason": "발음 다름"})
        elif op == "replace" and _accept(src, dst, dict_pairs, threshold, max_syllable_diff, trust_dictionary_source):
            out.append(dst)
        elif op == "insert":
            # 원문에 없던 낱말 추가: 앞 낱말과 붙여 쓴 것을 띄운 경우 등은 replace 로 잡히므로 여기선 거부
            rejected.append({"from": "", "to": dst, "reason": "추가"})
        else:
            if src:
                out.append(src)
            rejected.append({"from": src, "to": dst, "reason": "삭제" if op == "delete" else "발음 다름"})
    return " ".join(w for w in out if w), rejected


def _accept(src: str, dst: str, dict_pairs: set[tuple[str, str]], threshold: float,
            max_syllable_diff: int | None, trust_dictionary_source: bool) -> bool:
    s, d = _strip(src), _strip(dst)
    if s == d:  # 띄어쓰기·문장부호만 다름
        return True
    if any(k in s and v in d for k, v in dict_pairs if k and v):  # 사전에 근거한 교체
        return True
    if trust_dictionary_source and any(k and (k in s or sound_similarity(k, s) >= DICT_FUZZY_THRESHOLD)
                                       for k, _ in dict_pairs):
        # 원문에 사전의 방언 낱말(또는 STT 가 비슷한 소리로 적은 형태, 예: 뭇나→'문나')이 있으면 LLM 의 표준어 표현을 채택
        return True
    allowed = max_syllable_diff if max_syllable_diff is not None else (1 if _syllables(s) >= 4 else 0)
    if abs(_syllables(s) - _syllables(d)) > allowed:
        return False
    return sound_similarity(s, d) >= threshold


def _syllables(text: str) -> int:
    return sum(1 for ch in text if 0xAC00 <= ord(ch) <= 0xD7A3)
