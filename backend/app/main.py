"""FastAPI 앱: 실행 명령 `cd backend && uvicorn app.main:app --reload --port 8000`"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import re
import secrets
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, BeforeValidator

from . import audio
from .config import ConfigError, Settings
from .db import COLUMN_NAMES, Database
from .dialect import DialectDict
from .llm import LLMError, LLMProvider, make_llm
from .pipeline import PipelineInput, run_pipeline
from .search import SearchEngine, hit_at_k
from .stt import STTError, STTProvider, make_stt

log = logging.getLogger("poc")

SAMPLE_EXTS = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac"}
SEARCH_CONTEXT = "사용자가 검색엔진에 찾고 싶은 것을 말로 검색하는 상황 (고령자 복지, 병원·약국, 버스·요금·공과금, 농사·날씨 정보)"
EMPTY_UTTERANCE = "(음성이 인식되지 않았습니다)"


# 프론트가 run_id 를 숫자로 보내도 받는다
RunId = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)]


class CounselRequest(BaseModel):
    run_id: RunId
    path: Literal["a", "b"]
    scenario_id: str
    history: list[dict] = []


class SearchRequest(BaseModel):
    run_id: RunId
    path: Literal["a", "b"]
    target_doc_id: str | None = None


class LoginRequest(BaseModel):
    code: str


# 외부 공유 시 접속 코드를 통과한 브라우저에 주는 쿠키 (값은 코드 자체가 아니라 코드의 HMAC)
ACCESS_COOKIE = "poc_access"
# 코드 없이 열려 있어야 하는 API: 화면이 모드·잠금 여부를 알아야 하고, 로그인 자체도 해야 하므로
PUBLIC_API_PATHS = {"/api/status", "/api/login"}


def access_token(code: str) -> str:
    return hmac.new(code.encode("utf-8"), b"poc-access-v1", hashlib.sha256).hexdigest()


class FeedbackRequest(BaseModel):
    run_id: RunId
    target: Literal["a_text", "b_text", "a_result", "b_result"]
    ok: bool


class AppState:
    def __init__(self, settings: Settings, stt: STTProvider | None = None, llm: LLMProvider | None = None):
        settings.validate()
        self.settings = settings
        settings.audio_dir.mkdir(parents=True, exist_ok=True)
        (settings.storage_dir / "tmp").mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.dialect = DialectDict.load(settings.data_dir / "dialect_dict.json")
        self.scenarios = _load_json(settings.data_dir / "scenarios.json")
        self.docs = _load_json(settings.data_dir / "search_docs.json")
        self.search = SearchEngine(self.docs)
        self.stt = stt or make_stt(settings)
        self.llm = llm or make_llm(settings)

    def scenario(self, scenario_id: str | None) -> dict | None:
        return next((s for s in self.scenarios if s["id"] == scenario_id), None)

    def list_samples(self) -> list[str]:
        d = self.settings.samples_dir
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix.lower() in SAMPLE_EXTS)


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("true", "1", "yes", "on", "y")


def _safe_code(code: str | None) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_-]", "", code or "")[:20]
    return cleaned or "anon"


def _rate(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _n(values: list) -> int:
    return sum(1 for v in values if v is not None)


def summarize(rows: list[dict]) -> dict:
    def side(p: str) -> dict:
        text_fb = [r[f"fb_{p}_text"] for r in rows]
        result_fb = [r[f"fb_{p}_result"] for r in rows]
        cers = [r[f"{p}_cer"] for r in rows]
        hits = [r[f"hit_{p}"] for r in rows]
        out = {
            "text_ok_rate": _rate(text_fb), "text_fb_n": _n(text_fb),
            "result_ok_rate": _rate(result_fb), "result_fb_n": _n(result_fb),
            "avg_cer": _rate(cers), "cer_n": _n(cers),
            "hit3_rate": _rate(hits), "hit3_n": _n(hits),
        }
        if p == "b":
            std = [r["b_cer_standard"] for r in rows]
            out["avg_cer_standard"] = _rate(std)
        return out

    by_sim = {"counsel": 0, "search": 0}
    for r in rows:
        if r["sim"] in by_sim:
            by_sim[r["sim"]] += 1
    return {"total_runs": len(rows), "by_sim": by_sim, "a": side("a"), "b": side("b")}


def warm_up(state: "AppState") -> None:
    """Whisper 모델과 LLM 을 한 번씩 불러 GPU/메모리에 올려 둔다. 실패해도 서비스에는 영향 없음."""
    import numpy as np

    from .llm import MockLLM
    from .stt import MockSTT

    t = time.perf_counter()
    try:
        if not isinstance(state.stt, MockSTT):
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "silence.wav"
                audio.write_wav(wav, np.zeros(audio.SAMPLE_RATE, dtype=np.float32))
                state.stt.transcribe(wav)
        if not isinstance(state.llm, MockLLM):
            state.llm.complete("짧게 답하라.", [{"role": "user", "content": "안녕"}], max_tokens=1, temperature=0)
        log.info("모델 예열 완료 (%.1fs)", time.perf_counter() - t)
    except Exception as e:  # noqa: BLE001
        log.warning("모델 예열 실패 (첫 요청이 느릴 수 있음): %s", e)


def create_app(settings: Settings | None = None, stt: STTProvider | None = None, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.poc = AppState(settings, stt, llm)
        except ConfigError as e:
            log.error("\n%s\n%s\n%s", "=" * 60, e, "=" * 60)
            raise
        log.info("STT=%s LLM=%s(%s) ffmpeg=%s", settings.stt_provider, settings.llm_provider,
                 settings.llm_model, audio.ffmpeg_exe())
        log.info("접속 코드: %s", settings.access_code or "없음 (잠금 꺼짐)")
        # 첫 사용자가 모델 로딩(Whisper 수 초, Ollama 10초 이상)을 기다리지 않도록 미리 데워 둔다 (서버 시작은 막지 않음)
        threading.Thread(target=warm_up, args=(app.state.poc,), daemon=True).start()
        yield

    app = FastAPI(title="고령자·사투리 STT 개선 POC", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def st(request: Request) -> AppState:
        return request.app.state.poc

    def is_authed(request: Request) -> bool:
        if not settings.access_code:
            return True
        cookie = request.cookies.get(ACCESS_COOKIE, "")
        return secrets.compare_digest(cookie, access_token(settings.access_code))

    @app.middleware("http")
    async def require_access_code(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_API_PATHS and not is_authed(request):
            return JSONResponse({"detail": "접속 코드가 필요합니다. 화면을 새로고침한 뒤 코드를 입력해 주세요."}, status_code=401)
        return await call_next(request)

    def get_run_or_404(state: AppState, run_id: str) -> dict:
        run = state.db.get_run(run_id)
        if not run:
            raise HTTPException(404, f"실행 기록을 찾을 수 없습니다: {run_id}")
        return run

    # ------------------------------------------------------------ 조회

    @app.get("/api/status")
    def status(request: Request):
        s = st(request).settings
        stt_model = {"openai": s.openai_stt_model, "local": s.local_whisper_model}.get(s.stt_provider, "mock")
        return {
            "stt_provider": s.stt_provider,
            "llm_provider": s.llm_provider,
            # 배너는 '음성을 실제로 듣지 않는' 경우에만 띄운다. LLM 만 mock 이면 사전 기반 보정으로 동작.
            "mock": s.stt_provider == "mock",
            "stt_model": stt_model,
            "llm_model": s.llm_model,
            "auth_required": bool(s.access_code),
            "authed": is_authed(request),
        }

    @app.post("/api/login")
    def login(req: LoginRequest, request: Request):
        if not settings.access_code:
            return {"ok": True}
        if not secrets.compare_digest(req.code.strip(), settings.access_code):
            time.sleep(0.5)  # 무차별 대입을 느리게
            raise HTTPException(401, "접속 코드가 맞지 않습니다.")
        resp = JSONResponse({"ok": True})
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        resp.set_cookie(ACCESS_COOKIE, access_token(settings.access_code), max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax", secure=https)
        return resp

    @app.get("/api/regions")
    def regions(request: Request):
        return st(request).dialect.regions()

    @app.get("/api/scenarios")
    def scenarios(request: Request):
        return st(request).scenarios

    @app.get("/api/search_docs")
    def search_docs(request: Request):
        return [{"id": d["id"], "category": d.get("category", ""), "title": d["title"]} for d in st(request).docs]

    @app.get("/api/samples")
    def samples(request: Request):
        return st(request).list_samples()

    @app.get("/api/audio/{filename}")
    def get_audio(filename: str, request: Request):
        audio_dir = st(request).settings.audio_dir
        path = (audio_dir / filename).resolve()
        if path.parent != audio_dir.resolve() or not path.is_file():
            raise HTTPException(404, "오디오 파일을 찾을 수 없습니다")
        return FileResponse(path, media_type="audio/wav")

    # ------------------------------------------------------------ 인식

    @app.post("/api/transcribe")
    def transcribe(
        request: Request,
        audio_file: UploadFile | None = File(None, alias="audio"),
        sample: str | None = Form(None),
        region: str = Form("none"),
        is_elderly: str = Form("true"),
        sim: str = Form("counsel"),
        scenario_id: str | None = Form(None),
        reference: str | None = Form(None),
        participant_code: str | None = Form(None),
        age_group: str | None = Form(None),
        participant_region: str | None = Form(None),
        consent: str | None = Form(None),
        pre_loudnorm: str | None = Form(None),
        pre_vad: str | None = Form(None),
        pre_tempo: str | None = Form(None),
        pre_denoise: str | None = Form(None),
    ):
        state = st(request)
        if not _as_bool(consent, False):
            raise HTTPException(400, "음성이 외부 AI 서비스로 전송되는 것에 동의해야 합니다 (consent=true)")
        if sim not in ("counsel", "search"):
            raise HTTPException(400, "sim 은 counsel 또는 search 여야 합니다")
        if not state.dialect.has_region(region):
            raise HTTPException(400, f"알 수 없는 지역입니다: {region}")
        scenario = state.scenario(scenario_id)
        if sim == "counsel" and scenario_id and not scenario:
            raise HTTPException(400, f"알 수 없는 시나리오입니다: {scenario_id}")

        has_upload = audio_file is not None and bool(audio_file.filename)
        if not has_upload and not sample:
            raise HTTPException(400, "audio 파일 또는 sample 이름 중 하나가 필요합니다")

        run_id = uuid.uuid4().hex[:12]
        prefix = f"{datetime.now():%Y%m%d}_{_safe_code(participant_code)}_{run_id}"
        raw_wav = state.settings.audio_dir / f"{prefix}_raw.wav"
        pre_wav = state.settings.audio_dir / f"{prefix}_pre.wav"

        tmp_path: Path | None = None
        try:
            if has_upload:
                suffix = Path(audio_file.filename).suffix.lower()
                if not re.fullmatch(r"\.[0-9a-z]{1,5}", suffix):
                    suffix = ".bin"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=state.settings.storage_dir / "tmp") as f:
                    while chunk := audio_file.file.read(1024 * 1024):
                        f.write(chunk)
                    tmp_path = Path(f.name)
                source = tmp_path
            else:
                if sample not in state.list_samples():
                    raise HTTPException(400, f"samples 폴더에 없는 파일입니다: {sample}")
                source = state.settings.samples_dir / sample

            if sim == "counsel":
                context = f"상담 시나리오: {scenario['name']}. 사용자가 상담사에게 전화로 말하는 상황" if scenario else "상담사와 대화하는 상황"
            else:
                context = SEARCH_CONTEXT
            options = audio.PreprocessOptions(
                # 기본값은 실측 근거로 무음 단축만 켬: 음량 정규화·속도 보정은 Whisper 인식을 오히려 망침 (README 참고)
                loudnorm=_as_bool(pre_loudnorm, False),
                vad=_as_bool(pre_vad, True),
                denoise=_as_bool(pre_denoise, False),
                tempo=_as_bool(pre_tempo, False),
            )
            elderly = _as_bool(is_elderly, True)
            reference = (reference or "").strip() or None
            inp = PipelineInput(
                source=source,
                raw_wav=raw_wav,
                pre_wav=pre_wav,
                is_elderly=elderly,
                region_name=state.dialect.region_name(region),
                stt_hint=state.dialect.stt_hint(region),
                dictionary=state.dialect.dict_text(region),
                context=context,
                reference=reference,
                options=options,
            )
            try:
                result = run_pipeline(inp, state.stt, state.llm)
            except audio.AudioError as e:
                raise HTTPException(400, f"오디오를 처리하지 못했습니다: {e}") from e
            except STTError as e:
                raise HTTPException(502, f"음성인식 실패: {e}") from e
            except LLMError as e:
                raise HTTPException(502, f"문맥 보정 실패: {e}") from e
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

        a, b = result["a"], result["b"]
        state.db.insert_run({
            "run_id": run_id,
            "participant_code": (participant_code or "").strip() or None,
            "age_group": age_group or None,
            "participant_region": participant_region or None,
            "sim": sim,
            "scenario_id": scenario_id if sim == "counsel" else None,
            "region": region,
            "is_elderly": int(elderly),
            "reference": reference,
            "raw_audio_path": str(raw_wav),
            "pre_audio_path": str(pre_wav),
            "duration_s": result["duration_s"],
            "convert_ms": result["convert_ms"],
            "pre_loudnorm": int(options.loudnorm),
            "pre_vad": int(options.vad),
            "pre_denoise": int(options.denoise),
            "pre_tempo": int(options.tempo),
            "stt_provider": state.settings.stt_provider,
            "llm_provider": f"{state.settings.llm_provider}:{state.settings.llm_model}",
            "a_text": a["text"],
            "a_ms": a["ms"],
            "a_cer": a["cer"],
            "b_raw_text": b["raw_text"],
            "b_dialect_text": b["dialect_text"],
            "b_standard_text": b["standard_text"],
            "b_ms": b["ms"],
            "b_preprocess_ms": b["steps_ms"]["preprocess"],
            "b_stt_ms": b["steps_ms"]["stt"],
            "b_correct_ms": b["steps_ms"]["correct"],
            "b_cer": b["cer"],
            "b_cer_standard": b["cer_standard"],
        })
        return {
            "run_id": run_id,
            "duration_s": result["duration_s"],
            "convert_ms": result["convert_ms"],
            "a": a,
            "b": b,
            "audio": {"raw_url": f"/api/audio/{raw_wav.name}", "pre_url": f"/api/audio/{pre_wav.name}"},
        }

    # ------------------------------------------------------------ 상담 / 검색

    @app.post("/api/counsel")
    def counsel(req: CounselRequest, request: Request):
        state = st(request)
        run = get_run_or_404(state, req.run_id)
        scenario = state.scenario(req.scenario_id)
        if not scenario:
            raise HTTPException(400, f"알 수 없는 시나리오입니다: {req.scenario_id}")
        user_text = (run["a_text"] if req.path == "a" else run["b_standard_text"]) or ""
        history = [*req.history, {"role": "user", "content": user_text.strip() or EMPTY_UTTERANCE}]
        try:
            reply = state.llm.counsel(scenario["system_prompt"], history)
        except LLMError as e:
            raise HTTPException(502, f"상담 답변 생성 실패: {e}") from e
        state.db.update_run(req.run_id, {f"counsel_{req.path}_reply": reply})
        return {"reply": reply, "user_text": user_text}

    @app.post("/api/search")
    def search(req: SearchRequest, request: Request):
        state = st(request)
        run = get_run_or_404(state, req.run_id)
        query = (run["a_text"] if req.path == "a" else run["b_standard_text"]) or ""
        results = state.search.search(query, k=3)
        hit = hit_at_k(results, req.target_doc_id)
        state.db.update_run(req.run_id, {
            f"search_{req.path}_query": query,
            f"search_{req.path}_results": json.dumps(results, ensure_ascii=False),
            f"hit_{req.path}": None if hit is None else int(hit),
            "target_doc_id": req.target_doc_id,
        })
        return {"query": query, "results": results, "hit": hit}

    # ------------------------------------------------------------ 피드백 / 요약

    @app.post("/api/feedback")
    def feedback(req: FeedbackRequest, request: Request):
        state = st(request)
        if not state.db.set_feedback(req.run_id, req.target, req.ok):
            raise HTTPException(404, f"실행 기록을 찾을 수 없습니다: {req.run_id}")
        return {"ok": True}

    @app.get("/api/summary")
    def summary(request: Request):
        return summarize(st(request).db.all_runs())

    @app.get("/api/export.csv")
    def export_csv(request: Request):
        rows = st(request).db.all_runs()
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=COLUMN_NAMES, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
        data = "﻿" + buf.getvalue()
        filename = f"poc_runs_{datetime.now():%Y%m%d_%H%M}.csv"
        return Response(
            content=data.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ------------------------------------------------------------ 화면 (빌드된 프론트엔드)
    # frontend/dist 가 있으면 API 와 같은 주소에서 화면을 내보낸다. 외부 공유(터널) 때 주소 하나로 끝난다.
    dist = settings.frontend_dist
    if dist and (dist / "index.html").is_file():
        dist_root = dist.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404, "없는 API 입니다")
            target = (dist_root / full_path).resolve()
            if full_path and target.is_file() and dist_root in target.parents:
                return FileResponse(target)
            return FileResponse(dist_root / "index.html")  # /summary 같은 화면 경로는 SPA 가 처리

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = create_app()
