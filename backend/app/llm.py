"""LLM 공급자(anthropic / openai / mock)와 문맥 보정·상담사 기능."""
from __future__ import annotations

import json
import logging
import re

from .config import Settings
from .guard import guard_correction, guard_standardization

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


CORRECTION_SYSTEM = """너는 한국어 음성인식(STT) 결과를 바로잡는 교정기다.
STT는 고령자 음성과 사투리를 자주 잘못 알아듣는다. 아래 정보를 참고해 STT 결과를 고쳐라.

[화자 정보]
- 고령자 여부: {elderly}
- 지역(사투리): {region}

[현재 상황]
{context}

[해당 지역 방언 사전 (방언 → 표준어)]
{dictionary}

[규칙]
1. dialect_text: 화자가 실제로 말했을 문장. STT 가 소리가 비슷한 다른 글자로 잘못 적은 부분만 고친다.
   - 국어사전에 없거나 문맥상 말이 안 되는 낱말은, 발음이 비슷하면서 앞뒤 문맥에 맞는 말로 고친다.
   - 띄어쓰기는 바로잡아도 된다. 어순은 절대 바꾸지 않는다.
   - 사투리 표현은 표준어로 바꾸지 말고 그대로 둔다.
2. standard_text: dialect_text 에서 사투리 부분만 표준어로 바꾼 문장. 나머지 낱말과 뜻은 그대로 둔다.
   사투리가 없으면 dialect_text 와 똑같이 쓴다. 다른 말로 바꿔 쓰거나(의역) 요약하지 않는다.
3. 원문에 없는 내용을 지어내지 말 것. 확신이 없으면 원문을 그대로 유지할 것.
4. 설명 없이 JSON 객체 하나만 출력한다: {{"dialect_text": "...", "standard_text": "..."}}

[예시]
- STT: "소화가 안 대서 약 사러 갔다 와써" → {{"dialect_text": "소화가 안 돼서 약 사러 갔다 왔어", "standard_text": "소화가 안 돼서 약 사러 갔다 왔어"}}
- STT(경상도): "어무이 약 문나" → {{"dialect_text": "어무이 약 뭇나", "standard_text": "어머니 약 먹었어?"}}
- STT: "노래 끝나면 불 꺼줘" → {{"dialect_text": "노래 끝나면 불 꺼줘", "standard_text": "노래 끝나면 불 꺼줘"}}"""

COUNSEL_RULES = """

[답변 규칙]
- 상대는 어르신일 수 있다. 쉬운 말로 짧게(2~3문장) 존댓말로 답한다.
- 알아듣기 어려운 말이면 추측하지 말고 정중하게 다시 물어본다."""


STANDARDIZE_SYSTEM = """너는 한국어 방언 문장을 표준어로 옮기는 변환기다.

[지역 방언 사전 (방언 → 표준어)]
{dictionary}

[규칙]
1. 사투리 낱말·어미만 표준어로 바꾼다. 나머지 낱말은 그대로 둔다.
2. 낱말을 빼거나 새로 넣지 않는다. 뜻을 모르는 낱말은 바꾸지 말고 그대로 둔다.
3. 설명 없이 JSON 객체 하나만 출력한다: {{"standard_text": "..."}}"""

# 표준어 문장이 이보다 짧아지면(글자 수 비율) 내용을 빼먹은 것으로 보고 사전 치환으로 대신한다
MIN_STANDARD_LENGTH_RATIO = 0.75

CALL_CONNECTED = "(상담 전화가 연결되었습니다)"


def parse_dictionary(text: str) -> dict[str, str]:
    """DialectDict.dict_text() 형식("- 방언 → 표준어" 줄)을 dict 로."""
    return dict(
        line[2:].split(" → ", 1) for line in (text or "").splitlines() if line.startswith("- ") and " → " in line
    )


def dict_substitute(text: str, entries: dict[str, str]) -> str:
    for word in sorted(entries, key=len, reverse=True):
        text = text.replace(word, entries[word])
    return text


def _compact_len(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


def _extract_json_object(text: str) -> dict | None:
    """모델 출력에서 JSON 객체를 최대한 찾아낸다 (코드펜스·앞뒤 설명 허용)."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def parse_correction(output: str, raw_text: str) -> dict:
    """{dialect_text, standard_text, parsed}. 파싱 실패·빈 값이면 STT 원문으로 대체."""
    obj = _extract_json_object(output) or {}
    dialect = obj.get("dialect_text")
    standard = obj.get("standard_text")
    parsed = isinstance(dialect, str) and isinstance(standard, str) and bool(dialect.strip() or standard.strip())
    dialect = dialect.strip() if isinstance(dialect, str) and dialect.strip() else raw_text
    standard = standard.strip() if isinstance(standard, str) and standard.strip() else dialect
    return {"dialect_text": dialect, "standard_text": standard, "parsed": parsed}


def normalize_history(history: list[dict]) -> list[dict]:
    """user/assistant 만 남기고, 첫 턴이 user 가 되도록 하며, 같은 역할이 연속되면 합친다."""
    msgs: list[dict] = []
    for m in history:
        role, content = m.get("role"), str(m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + content
        else:
            msgs.append({"role": role, "content": content})
    if msgs and msgs[0]["role"] == "assistant":
        msgs.insert(0, {"role": "user", "content": CALL_CONNECTED})
    return msgs


class LLMProvider:
    name = "base"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024, temperature: float | None = None) -> str:
        raise NotImplementedError

    def correct(
        self,
        raw_text: str,
        *,
        is_elderly: bool,
        region_name: str,
        context: str,
        dictionary: str,
    ) -> dict:
        if not raw_text.strip():
            return {"dialect_text": "", "standard_text": "", "parsed": False, "rejected": []}
        system = CORRECTION_SYSTEM.format(
            elderly="예" if is_elderly else "아니오",
            region=region_name,
            context=context or "(정보 없음)",
            dictionary=dictionary or "(사전 없음 — 표준어 화자로 보고 오인식만 바로잡을 것)",
        )
        try:
            # 보정은 창작이 아니므로 무작위성을 끈다 (Ollama 기본값 0.8 은 없는 말을 지어냄)
            out = self.complete(system, [{"role": "user", "content": f"STT 결과: {raw_text}"}], max_tokens=1024, temperature=0)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"LLM 보정 호출 실패: {e}") from e
        result = parse_correction(out, raw_text)
        if not result["parsed"]:
            log.warning("보정 결과 JSON 파싱 실패, STT 원문 사용: %r", out[:200])
            result["rejected"] = []
            return result

        # 발음이 비슷한 수정만 채택 (지어낸 말·빠뜨린 말은 STT 원문 유지)
        entries = parse_dictionary(dictionary)
        dialect, rejected = guard_correction(raw_text, result["dialect_text"], entries)
        standard = result["standard_text"]
        if rejected:
            # LLM 의 표준어 문장은 거부된(지어낸) 문장을 바탕으로 했으므로 걸러진 문장으로 다시 만든다
            standard = self._standardize(dialect, dictionary)
        # 표준어 변환도 근거(비슷한 소리·사전) 있는 것만 남긴다
        standard, std_rejected = guard_standardization(dialect, standard, entries)
        rejected = rejected + [{**r, "reason": f"표준어 변환: {r['reason']}"} for r in std_rejected]
        standard = dict_substitute(standard, entries)  # 남아 있는 사전 방언 낱말은 확실히 표준어로
        if _compact_len(standard) < MIN_STANDARD_LENGTH_RATIO * _compact_len(dialect):
            log.warning("표준어 문장이 내용을 빠뜨림, 사전 치환으로 대체: %r", standard)
            standard = dict_substitute(dialect, entries)
        return {"dialect_text": dialect, "standard_text": standard, "parsed": True, "rejected": rejected}

    def _standardize(self, dialect_text: str, dictionary: str) -> str:
        system = STANDARDIZE_SYSTEM.format(dictionary=dictionary or "(사전 없음)")
        try:
            out = self.complete(system, [{"role": "user", "content": dialect_text}], max_tokens=512, temperature=0)
        except Exception as e:  # noqa: BLE001
            log.warning("표준어 변환 실패, 사전 치환으로 대체: %s", e)
            return dict_substitute(dialect_text, parse_dictionary(dictionary))
        obj = _extract_json_object(out) or {}
        text = obj.get("standard_text")
        return text.strip() if isinstance(text, str) and text.strip() else dict_substitute(dialect_text, parse_dictionary(dictionary))

    def counsel(self, system_prompt: str, history: list[dict]) -> str:
        msgs = normalize_history(history)
        if not msgs or msgs[-1]["role"] != "user":
            raise LLMError("상담 기록의 마지막은 사용자 발화여야 합니다")
        try:
            return self.complete(system_prompt + COUNSEL_RULES, msgs, max_tokens=512).strip()
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"LLM 상담 호출 실패: {e}") from e


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024, temperature: float | None = None) -> str:
        extra = {} if temperature is None else {"temperature": temperature}
        resp = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system, messages=messages, **extra)
        return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024, temperature: float | None = None) -> str:
        extra = {} if temperature is None else {"temperature": temperature}
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            **extra,
            messages=[{"role": "system", "content": system}, *messages],
        )
        return resp.choices[0].message.content or ""


class OllamaLLM(OpenAILLM):
    """PC 에서 도는 Ollama (OpenAI 호환 API). 키·비용 없음."""

    name = "ollama"

    def __init__(self, base_url: str, model: str):
        super().__init__(api_key="ollama", model=model, base_url=base_url)


class MockLLM(LLMProvider):
    """키 없이 화면을 확인하기 위한 가짜 LLM. 보정은 사전 치환, 상담은 정해진 문장."""

    name = "mock"

    def correct(self, raw_text, *, is_elderly, region_name, context, dictionary) -> dict:
        if not raw_text.strip():
            return {"dialect_text": "", "standard_text": "", "parsed": False, "rejected": []}
        standard = dict_substitute(raw_text, parse_dictionary(dictionary))
        return {"dialect_text": raw_text, "standard_text": standard, "parsed": True, "rejected": []}

    def complete(self, system, messages, max_tokens=1024, temperature=None) -> str:
        last = messages[-1]["content"] if messages else ""
        turn = sum(1 for m in messages if m["role"] == "user" and m["content"] != CALL_CONNECTED)
        return f"(모의 상담사 {turn}번째 답변) \"{last}\" 라고 말씀하셨군요. 조금 더 자세히 말씀해 주시겠어요?"


def make_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(settings.anthropic_api_key, settings.llm_model)
    if settings.llm_provider == "openai":
        return OpenAILLM(settings.openai_api_key, settings.llm_model)
    if settings.llm_provider == "ollama":
        return OllamaLLM(settings.ollama_base_url, settings.llm_model)
    if settings.llm_provider == "mock":
        return MockLLM()
    raise ValueError(f"알 수 없는 LLM_PROVIDER: {settings.llm_provider}")
