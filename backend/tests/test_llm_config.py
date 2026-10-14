from types import SimpleNamespace

import pytest

from app.config import ConfigError, Settings
from app.llm import AnthropicLLM, MockLLM, normalize_history, parse_correction


def test_parse_correction_plain_json():
    r = parse_correction('{"dialect_text": "밥 뭇나", "standard_text": "밥 먹었니"}', "밥 묻나")
    assert r == {"dialect_text": "밥 뭇나", "standard_text": "밥 먹었니", "parsed": True}


def test_parse_correction_code_fence_and_prose():
    out = '보정 결과입니다:\n```json\n{"dialect_text": "밥 뭇나", "standard_text": "밥 먹었니"}\n```\n끝.'
    assert parse_correction(out, "밥 묻나")["standard_text"] == "밥 먹었니"


def test_parse_correction_fallback_to_raw():
    r = parse_correction("죄송하지만 JSON 을 만들 수 없어요", "밥 묻나")
    assert r == {"dialect_text": "밥 묻나", "standard_text": "밥 묻나", "parsed": False}


def test_parse_correction_missing_standard_uses_dialect():
    r = parse_correction('{"dialect_text": "밥 뭇나"}', "밥 묻나")
    assert r["dialect_text"] == "밥 뭇나" and r["standard_text"] == "밥 뭇나"


def test_normalize_history_starts_with_user_and_merges():
    msgs = normalize_history([
        {"role": "assistant", "content": "안녕하세요"},
        {"role": "user", "content": "예약"},
        {"role": "user", "content": "할라꼬요"},
        {"role": "system", "content": "무시"},
        {"role": "assistant", "content": ""},
    ])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[2]["content"] == "예약\n할라꼬요"


def test_mock_llm_correct_uses_dictionary():
    r = MockLLM().correct("할매 밥 뭇나", is_elderly=True, region_name="경상도", context="",
                          dictionary="- 뭇나 → 먹었니\n- 할매 → 할머니")
    assert r["dialect_text"] == "할매 밥 뭇나"
    assert r["standard_text"] == "할머니 밥 먹었니"


class FakeAnthropicMessages:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)])


def make_fake_anthropic(text):
    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm.model = "claude-sonnet-5-5"
    llm.client = SimpleNamespace(messages=FakeAnthropicMessages(text))
    return llm


def test_anthropic_correct_prompt_contents():
    llm = make_fake_anthropic('{"dialect_text": "밥 뭇나", "standard_text": "밥 먹었니"}')
    r = llm.correct("밥 묻나", is_elderly=True, region_name="경상도", context="상담 시나리오: 병원 예약",
                    dictionary="- 뭇나 → 먹었니")
    assert r["standard_text"] == "밥 먹었니"
    call = llm.client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    for needle in ("고령자 여부: 예", "경상도", "병원 예약", "- 뭇나 → 먹었니", "지어내지 말 것", "원문을 그대로 유지"):
        assert needle in call["system"]
    assert call["messages"] == [{"role": "user", "content": "STT 결과: 밥 묻나"}]


def test_anthropic_counsel():
    llm = make_fake_anthropic("네, 예약 도와드릴게요.")
    reply = llm.counsel("병원 상담사", [{"role": "assistant", "content": "안녕하세요"},
                                     {"role": "user", "content": "예약하고 싶어요"}])
    assert reply == "네, 예약 도와드릴게요."
    assert llm.client.messages.calls[0]["messages"][0]["role"] == "user"


def test_empty_stt_text_skips_llm():
    llm = make_fake_anthropic("should not be called")
    r = llm.correct("  ", is_elderly=False, region_name="", context="", dictionary="")
    assert r["dialect_text"] == "" and not llm.client.messages.calls


def test_config_reports_missing_keys(settings):
    s = Settings(**{**settings.__dict__, "stt_provider": "openai", "llm_provider": "anthropic",
                    "openai_api_key": "", "anthropic_api_key": ""})
    with pytest.raises(ConfigError) as e:
        s.validate()
    assert "OPENAI_API_KEY" in str(e.value) and "ANTHROPIC_API_KEY" in str(e.value)


def test_config_reports_missing_data(settings, tmp_path):
    s = Settings(**{**settings.__dict__, "data_dir": tmp_path / "nope"})
    with pytest.raises(ConfigError, match="dialect_dict.json"):
        s.validate()


def test_config_mock_ok_and_default_model(settings, monkeypatch):
    settings.validate()
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert Settings.from_env().llm_model == "claude-sonnet-5-5"


def test_make_llm_ollama_uses_local_openai_compatible_endpoint(settings):
    from app.llm import OllamaLLM, make_llm

    s = Settings(**{**settings.__dict__, "llm_provider": "ollama", "llm_model": "exaone3.5:7.8b",
                    "ollama_base_url": "http://127.0.0.1:11434/v1"})
    s.validate()  # 키가 필요 없어야 한다
    llm = make_llm(s)
    assert isinstance(llm, OllamaLLM)
    assert llm.model == "exaone3.5:7.8b"
    assert str(llm.client.base_url).startswith("http://127.0.0.1:11434/v1")


def test_local_whisper_falls_back_to_cpu_when_gpu_fails(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    from app.stt import LocalWhisperSTT

    wav = tmp_path / "a.wav"
    sf.write(wav, np.zeros(1600, dtype="float32"), 16000)
    stt = LocalWhisperSTT("medium", device="cuda")
    calls = []

    def fake_run(audio, prompt):
        calls.append(stt.device)
        if stt.device == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return "됐다"

    monkeypatch.setattr(stt, "_run", fake_run)
    assert stt.transcribe(wav) == "됐다"
    assert calls == ["cuda", "cpu"] and stt.device == "cpu"
