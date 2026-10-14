from app.guard import guard_correction, sound_similarity
from app.llm import LLMProvider

JEOLLA_RAW = "그 집 맨누리는 아침 진하그로 집안 어른들께 문안인사를 드리고 얼굴도 동구시리한 거에 아주 복시롭게 생겼더라"


def test_sound_similarity_orders_plausible_fixes_above_fabrications():
    assert sound_similarity("문나", "뭇나") > 0.7
    assert sound_similarity("맨누리", "할아버지") < 0.5
    assert sound_similarity("진하그로", "일찍") < 0.5


def test_guard_keeps_similar_sounding_fixes_and_spacing():
    assert guard_correction("오늘 복번 8시 뉴스", "오늘 못 본 8시 뉴스") == ("오늘 못 본 8시 뉴스", [])
    assert guard_correction("밥 문나? 오늘", "밥 뭇나? 오늘")[0] == "밥 뭇나? 오늘"
    assert guard_correction("정구 지는 언제", "정구지는 언제")[0] == "정구지는 언제"


def test_guard_rejects_fabricated_and_dropped_words():
    # 실제 사례: 7.8B LLM 이 '맨누리→할아버지' 로 바꾸고 '아주 복시롭게' 를 빼먹음
    text, rejected = guard_correction(
        JEOLLA_RAW, "그 집 할아버지는 아침 일찍 집안 어른들께 문안 인사를 드리고 얼굴이 둥글둥글하게 생겼더라"
    )
    assert text == "그 집 맨누리는 아침 진하그로 집안 어른들께 문안 인사를 드리고 얼굴도 동구시리한 거에 아주 복시롭게 생겼더라"
    assert {r["to"] for r in rejected} >= {"할아버지는", "일찍"}


def test_guard_rejects_syllable_inflation_and_insertions():
    assert guard_correction("간주 나오면 넘겨줘", "간호사 나오면 넘겨줘")[0] == "간주 나오면 넘겨줘"
    assert guard_correction("병원 가요", "병원 빨리 가요")[0] == "병원 가요"


def test_guard_accepts_dictionary_backed_replacement():
    assert guard_correction("정구지 심어요", "부추 심어요", {"정구지": "부추"})[0] == "부추 심어요"


class _ScriptedLLM(LLMProvider):
    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def complete(self, system, messages, max_tokens=1024, temperature=None):
        self.calls.append(system)
        return self.outputs.pop(0)


def test_correct_regenerates_standard_from_guarded_text_when_llm_fabricates():
    llm = _ScriptedLLM(
        '{"dialect_text": "그 집 할아버지는 아침 일찍 집안 어른들께 문안 인사를 드리고 얼굴이 둥글둥글하게 생겼더라",'
        ' "standard_text": "그 집 할아버지는 아침 일찍 집안 어른들께 인사를 드리고 얼굴이 둥글둥글하게 생겼더라"}',
        '{"standard_text": "그 집 맏며느리는 아침 진지하고로 집안 어른들께 문안 인사를 드리고 얼굴도 동그스름한 게 아주 복스럽게 생겼더라"}',
    )
    r = llm.correct(JEOLLA_RAW, is_elderly=True, region_name="전라도", context="", dictionary="- 맨누리 → 맏며느리")
    assert "할아버지" not in r["dialect_text"] and "할아버지" not in r["standard_text"]
    assert "복스럽게" in r["standard_text"]
    assert len(llm.calls) == 2 and r["rejected"]


def test_correct_falls_back_to_dictionary_when_standard_drops_content():
    llm = _ScriptedLLM('{"dialect_text": "밥 뭇나 오늘 병원 예약 할라카는데", "standard_text": "밥"}')
    r = llm.correct("밥 문나 오늘 병원 예약 할라카는데", is_elderly=True, region_name="경상도", context="",
                    dictionary="- 뭇나 → 먹었니")
    assert r["dialect_text"] == "밥 뭇나 오늘 병원 예약 할라카는데"
    assert r["standard_text"] == "밥 먹었니 오늘 병원 예약 할라카는데"


def test_standardization_guard_rejects_invented_standard_but_keeps_plausible_one():
    from app.guard import guard_standardization

    dialect = "그 집 맨누리는 아침 문안 인사를 드리고 얼굴도 아주 복시롭게 생겼더라"
    invented = "그 집 마당은 아침 문안 인사를 드리고 얼굴이 밝고 예쁘게 생겼더라"
    plausible = "그 집 맏며느리는 아침 문안 인사를 드리고 얼굴도 아주 복스럽게 생겼더라"
    guarded, rejected = guard_standardization(dialect, invented)
    assert not any(w in guarded for w in ("마당", "밝고", "예쁘게"))  # 지어낸 낱말은 모두 거부
    assert "맨누리는" in guarded and "복시롭게" in guarded and len(rejected) == 3
    assert guard_standardization(dialect, plausible) == (plausible, [])
    # 사전에 있는 방언 낱말은 LLM 의 표준어 표현을 그대로 채택
    assert guard_standardization("밥 뭇나", "밥 먹었어요?", {"뭇나": "먹었니"})[0] == "밥 먹었어요?"


def test_standardization_accepts_llm_rendering_for_misspelled_dictionary_word():
    from app.guard import guard_standardization

    # turbo STT 는 '뭇나' 를 '문나' 로 적는다 → 사전 낱말과 소리가 같으면 LLM 의 표준어를 채택
    assert guard_standardization("밥 문나 오늘", "밥 먹었어요? 오늘", {"뭇나": "먹었니"})[0] == "밥 먹었어요? 오늘"
    # 사전과 상관없는 낱말을 지어낸 경우는 여전히 거부
    assert guard_standardization("아침 진학으로 인사", "아침 친절하게 인사", {"뭇나": "먹었니"})[0] == "아침 진학으로 인사"
