import csv
import io
import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.main import create_app
from tests.conftest import silence, voice_like

BASE_FORM = {"consent": "true", "region": "gyeongsang", "is_elderly": "true"}
REFERENCE = "할매 밥 뭇나 오늘 정구지 찌짐 해 묵자 병원 예약 할라카는데 우째 해야 되노"


def transcribe(client, **extra):
    data = {**BASE_FORM, "sample": "demo.wav", **extra}
    r = client.post("/api/transcribe", data=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_lookup_endpoints(client):
    regions = client.get("/api/regions").json()
    assert regions[0]["id"] == "none" and any(r["id"] == "gyeongsang" for r in regions)
    assert [s["id"] for s in client.get("/api/scenarios").json()] == ["hospital", "civil", "parcel"]
    docs = client.get("/api/search_docs").json()
    assert set(docs[0]) == {"id", "category", "title"}
    assert client.get("/api/samples").json() == ["demo.wav"]


def test_status_reports_mock_mode(client):
    assert client.get("/api/status").json() == {
        "stt_provider": "mock", "llm_provider": "mock", "mock": True, "stt_model": "mock", "llm_model": "mock",
        "auth_required": False, "authed": True,
    }


def test_status_reports_real_mode(settings):
    from app.llm import MockLLM
    from app.stt import MockSTT

    real = Settings(**{**settings.__dict__, "stt_provider": "openai", "openai_api_key": "sk-test",
                       "llm_provider": "anthropic", "anthropic_api_key": "sk-ant-test", "llm_model": "claude-x"})
    with TestClient(create_app(real, stt=MockSTT(), llm=MockLLM())) as c:
        body = c.get("/api/status").json()
    assert body == {"stt_provider": "openai", "llm_provider": "anthropic", "mock": False,
                    "stt_model": "whisper-1", "llm_model": "claude-x", "auth_required": False, "authed": True}


def test_consent_required(client):
    r = client.post("/api/transcribe", data={**BASE_FORM, "consent": "false", "sample": "demo.wav"})
    assert r.status_code == 400 and "동의" in r.json()["detail"]


def test_audio_or_sample_required(client):
    r = client.post("/api/transcribe", data=BASE_FORM)
    assert r.status_code == 400


def test_unknown_sample_rejected(client):
    r = client.post("/api/transcribe", data={**BASE_FORM, "sample": "../secret.wav"})
    assert r.status_code == 400


def test_unknown_region_rejected(client):
    r = client.post("/api/transcribe", data={**BASE_FORM, "sample": "demo.wav", "region": "seoul"})
    assert r.status_code == 400


def test_transcribe_upload_response_shape(client, tmp_path):
    path = tmp_path / "rec.wav"
    sf.write(path, np.concatenate([voice_like(1.0, 48000), silence(1.0, 48000), voice_like(0.5, 48000)]), 48000)
    with open(path, "rb") as f:
        r = client.post(
            "/api/transcribe",
            data={**BASE_FORM, "sim": "counsel", "scenario_id": "hospital", "reference": REFERENCE,
                  "participant_code": "P01"},
            files={"audio": ("rec.wav", f, "audio/wav")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"run_id", "duration_s", "convert_ms", "a", "b", "audio"}
    assert set(body["a"]) == {"text", "ms", "cer"}
    assert set(body["b"]) == {"raw_text", "dialect_text", "standard_text", "rejected", "ms", "cer", "cer_standard", "steps_ms"}
    assert set(body["b"]["steps_ms"]) == {"preprocess", "stt", "correct"}
    assert body["duration_s"] == pytest.approx(2.5, abs=0.05)
    # mock STT: B(방언 힌트)는 정답과 같고 A 는 틀림 → CER 차이
    assert body["b"]["cer"] == 0.0 and body["a"]["cer"] > 0
    assert "먹었니" in body["b"]["standard_text"]
    # 원본·전처리본 저장과 파일명 규칙
    raw_name = body["audio"]["raw_url"].rsplit("/", 1)[1]
    assert raw_name.endswith(f"_P01_{body['run_id']}_raw.wav")
    assert body["audio"]["pre_url"].endswith(f"_P01_{body['run_id']}_pre.wav")
    for url in body["audio"].values():
        got = client.get(url)
        assert got.status_code == 200 and got.content[:4] == b"RIFF"
    assert client.get("/api/audio/poc.db").status_code == 404


def test_transcribe_rejects_garbage_upload(client):
    r = client.post("/api/transcribe", data=BASE_FORM, files={"audio": ("x.webm", b"garbage", "audio/webm")})
    assert r.status_code == 400 and "오디오" in r.json()["detail"]


def test_transcribe_without_reference_has_null_cer(client):
    body = transcribe(client, region="none")
    assert body["a"]["cer"] is None and body["b"]["cer"] is None and body["b"]["cer_standard"] is None
    # 지역 모름 → 힌트 없음 → mock STT 는 A 와 같은 문장
    assert body["b"]["raw_text"] == body["a"]["text"]


def test_full_flow_counsel_search_feedback_summary_export(client):
    # 상담: 두 턴 (프론트가 창별 기록을 유지해서 보냄)
    run1 = transcribe(client, sim="counsel", scenario_id="hospital", participant_code="P02", age_group="70대",
                      reference=REFERENCE)
    history_b = [{"role": "assistant", "content": "안녕하세요, 병원 예약 도와드릴게요."}]
    r = client.post("/api/counsel", json={"run_id": run1["run_id"], "path": "b", "scenario_id": "hospital",
                                          "history": history_b})
    assert r.status_code == 200, r.text
    reply1 = r.json()
    assert reply1["user_text"] == run1["b"]["standard_text"] and reply1["reply"]
    history_b += [{"role": "user", "content": reply1["user_text"]}, {"role": "assistant", "content": reply1["reply"]}]
    run2 = transcribe(client, sim="counsel", scenario_id="hospital")
    r = client.post("/api/counsel", json={"run_id": run2["run_id"], "path": "b", "scenario_id": "hospital",
                                          "history": history_b})
    assert "2번째" in r.json()["reply"]
    r = client.post("/api/counsel", json={"run_id": run1["run_id"], "path": "a", "scenario_id": "hospital",
                                          "history": []})
    assert r.json()["user_text"] == run1["a"]["text"]

    # 검색 + hit@3: mock B 표준어에는 '부추' 가 들어가 d04 적중, A 는 '정구 지' 로 실패
    run3 = transcribe(client, sim="search")
    ra = client.post("/api/search", json={"run_id": run3["run_id"], "path": "a", "target_doc_id": "d04"}).json()
    rb = client.post("/api/search", json={"run_id": run3["run_id"], "path": "b", "target_doc_id": "d04"}).json()
    assert len(rb["results"]) <= 3 and rb["hit"] is True and ra["hit"] is False
    assert rb["query"] == run3["b"]["standard_text"]

    # 피드백 (같은 대상은 마지막 응답이 덮어씀)
    def fb(run_id, target, ok):
        return client.post("/api/feedback", json={"run_id": run_id, "target": target, "ok": ok})

    assert fb(run1["run_id"], "a_text", True).json() == {"ok": True}
    fb(run1["run_id"], "a_text", False)
    fb(run1["run_id"], "b_text", True)
    fb(run1["run_id"], "b_result", True)
    fb(run3["run_id"], "b_text", True)
    fb(run3["run_id"], "a_result", False)
    assert fb("nope", "a_text", True).status_code == 404
    assert fb(run1["run_id"], "x", True).status_code == 422

    s = client.get("/api/summary").json()
    assert s["total_runs"] == 3 and s["by_sim"] == {"counsel": 2, "search": 1}
    assert s["a"]["text_ok_rate"] == 0.0 and s["a"]["text_fb_n"] == 1
    assert s["b"]["text_ok_rate"] == 1.0 and s["b"]["text_fb_n"] == 2
    assert s["a"]["result_ok_rate"] == 0.0 and s["b"]["result_ok_rate"] == 1.0
    assert s["a"]["avg_cer"] > 0 and s["a"]["cer_n"] == 1
    assert s["b"]["avg_cer"] == 0.0 and s["b"]["cer_n"] == 1
    assert s["a"]["hit3_rate"] == 0.0 and s["b"]["hit3_rate"] == 1.0 and s["b"]["hit3_n"] == 1

    # CSV: UTF-8 BOM, 피드백·상담·검색 결과 반영
    r = client.get("/api/export.csv")
    assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 3
    row1 = next(x for x in rows if x["run_id"] == run1["run_id"])
    assert row1["fb_a_text"] == "0" and row1["fb_b_text"] == "1" and row1["fb_b_result"] == "1"
    assert row1["age_group"] == "70대" and row1["participant_code"] == "P02"
    assert row1["counsel_b_reply"] == reply1["reply"]
    row3 = next(x for x in rows if x["run_id"] == run3["run_id"])
    assert row3["hit_a"] == "0" and row3["hit_b"] == "1" and row3["target_doc_id"] == "d04"
    assert json.loads(row3["search_b_results"])[0]["id"] == "d04"

    # target 없이 다시 검색하면 hit 는 null
    again = client.post("/api/search", json={"run_id": run3["run_id"], "path": "b"}).json()
    assert again["hit"] is None


def test_empty_summary(client):
    s = client.get("/api/summary").json()
    assert s["total_runs"] == 0 and s["a"]["text_ok_rate"] is None and s["b"]["avg_cer"] is None


def test_counsel_errors(client):
    run = transcribe(client)
    r = client.post("/api/counsel", json={"run_id": run["run_id"], "path": "a", "scenario_id": "zzz", "history": []})
    assert r.status_code == 400
    r = client.post("/api/counsel", json={"run_id": "missing", "path": "a", "scenario_id": "hospital", "history": []})
    assert r.status_code == 404


def test_startup_fails_clearly_without_keys(settings):
    bad = Settings(**{**settings.__dict__, "llm_provider": "anthropic", "anthropic_api_key": ""})
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        with TestClient(create_app(bad)):
            pass


def test_numeric_run_id_accepted(client):
    # 숫자 run_id 도 검증 오류(422)가 아니라 조회 결과(404)로 처리
    assert client.post("/api/feedback", json={"run_id": 123, "target": "a_text", "ok": True}).status_code == 404
    assert client.post("/api/search", json={"run_id": 123, "path": "a"}).status_code == 404
    r = client.post("/api/counsel", json={"run_id": 123, "path": "a", "scenario_id": "hospital", "history": []})
    assert r.status_code == 404


def test_frontend_style_request_against_real_data(tmp_path, monkeypatch, settings):
    """실제 data/ 파일 + 브라우저 녹음(webm/opus, MIME 무시) + 빈 선택 필드 생략 + 인사말 없는 history."""
    from app import audio
    from app.config import PROJECT_ROOT

    real = Settings(**{**settings.__dict__, "data_dir": PROJECT_ROOT / "data"})
    wav = tmp_path / "src.wav"
    sf.write(wav, np.concatenate([voice_like(1.0), silence(1.2), voice_like(1.0)]), 16000)
    webm = tmp_path / "recording.webm"
    import subprocess
    subprocess.run([audio.ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libopus", str(webm)],
                   check=True)

    with TestClient(create_app(real)) as c:
        with open(webm, "rb") as f:
            r = c.post("/api/transcribe",
                       data={"consent": "true", "sim": "counsel", "region": "gyeongsang", "is_elderly": "true",
                             "pre_loudnorm": "true", "pre_vad": "true", "pre_tempo": "true", "pre_denoise": "false",
                             "scenario_id": "hospital"},
                       files={"audio": ("recording.webm", f, "application/octet-stream")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["audio"]["raw_url"].startswith("/api/audio/")
        assert body["duration_s"] > 3
        # 인사말 없이 이전 턴만 (첫 턴이면 빈 목록)
        r = c.post("/api/counsel", json={"run_id": body["run_id"], "path": "b", "scenario_id": "hospital",
                                         "history": []})
        assert r.status_code == 200 and r.json()["reply"]
        r = c.post("/api/counsel", json={"run_id": body["run_id"], "path": "a", "scenario_id": "hospital",
                                         "history": [{"role": "user", "content": "여보세요"},
                                                     {"role": "assistant", "content": "네, 말씀하세요."}]})
        assert r.status_code == 200
        res = c.post("/api/search", json={"run_id": body["run_id"], "path": "b"}).json()
        assert res["hit"] is None and len(res["results"]) <= 3


# ---------------------------------------------------------------- 외부 공유: 접속 코드 · 화면 서빙

def _locked_client(settings, code="1234"):
    return TestClient(create_app(Settings(**{**settings.__dict__, "access_code": code})))


def test_access_code_blocks_api_until_login(settings):
    with _locked_client(settings) as c:
        assert c.get("/api/status").json()["auth_required"] is True
        assert c.get("/api/status").json()["authed"] is False
        r = c.get("/api/regions")
        assert r.status_code == 401 and "접속 코드" in r.json()["detail"]
        assert c.get("/api/export.csv").status_code == 401  # 참여자 기록도 잠김
        assert c.post("/api/login", json={"code": "0000"}).status_code == 401
        assert c.post("/api/login", json={"code": " 1234 "}).status_code == 200
        assert c.get("/api/status").json()["authed"] is True
        assert c.get("/api/regions").status_code == 200


def test_access_cookie_does_not_contain_the_code(settings):
    with _locked_client(settings, code="secret-777") as c:
        c.post("/api/login", json={"code": "secret-777"})
        cookie = c.cookies.get("poc_access")
        assert cookie and "secret-777" not in cookie


def test_no_access_code_means_open(client):
    assert client.get("/api/regions").status_code == 200
    assert client.post("/api/login", json={"code": "anything"}).json() == {"ok": True}


def test_serves_built_frontend_with_spa_fallback(settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>INDEX</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    with TestClient(create_app(Settings(**{**settings.__dict__, "frontend_dist": dist}))) as c:
        assert "INDEX" in c.get("/").text
        assert "INDEX" in c.get("/summary").text  # 화면 경로는 index.html
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert "nope" not in c.get("/../secret.txt").text  # dist 밖 파일은 못 꺼냄
        assert c.get("/api/nope").status_code == 404  # 없는 API 를 화면으로 덮지 않음
        assert c.get("/api/regions").status_code == 200
