"""환경 설정 (.env) 로드와 시작 시 검증."""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# 프로젝트 루트의 .env 우선, backend/.env 도 허용 (이미 설정된 환경변수는 덮어쓰지 않음)
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")

STT_PROVIDERS = ("openai", "local", "mock")
LLM_PROVIDERS = ("anthropic", "openai", "ollama", "mock")
DEFAULT_LLM_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini", "ollama": "exaone3.5:7.8b", "mock": "mock"}


class ConfigError(RuntimeError):
    """필수 설정이 빠졌거나 잘못되었을 때."""


def _path_env(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).resolve() if value else default


@dataclass
class Settings:
    stt_provider: str
    llm_provider: str
    llm_model: str
    openai_stt_model: str
    local_whisper_model: str
    openai_api_key: str
    anthropic_api_key: str
    ollama_base_url: str
    data_dir: Path
    storage_dir: Path
    samples_dir: Path
    db_path: Path
    cors_origins: list[str]
    # 외부 공유용: 비우면 잠금 없음(로컬 기본). 정하면 이 코드를 아는 사람만 API 를 쓸 수 있다
    access_code: str = ""
    # 빌드된 프론트엔드(frontend/dist)가 있으면 백엔드가 같은 주소에서 화면도 내보낸다 (외부 공유 시 주소 하나로 충분)
    frontend_dist: Path | None = None

    @property
    def audio_dir(self) -> Path:
        return self.storage_dir / "audio"

    @classmethod
    def from_env(cls) -> "Settings":
        stt = os.getenv("STT_PROVIDER", "openai").strip().lower()
        llm = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
        storage_dir = _path_env("STORAGE_DIR", PROJECT_ROOT / "storage")
        origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        return cls(
            stt_provider=stt,
            llm_provider=llm,
            llm_model=os.getenv("LLM_MODEL", "").strip() or DEFAULT_LLM_MODELS.get(llm, ""),
            openai_stt_model=os.getenv("OPENAI_STT_MODEL", "whisper-1").strip(),
            local_whisper_model=os.getenv("LOCAL_WHISPER_MODEL", "small").strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").strip(),
            data_dir=_path_env("DATA_DIR", PROJECT_ROOT / "data"),
            storage_dir=storage_dir,
            samples_dir=_path_env("SAMPLES_DIR", PROJECT_ROOT / "samples"),
            db_path=_path_env("DB_PATH", storage_dir / "poc.db"),
            cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
            access_code=os.getenv("ACCESS_CODE", "").strip(),
            frontend_dist=_path_env("FRONTEND_DIST", PROJECT_ROOT / "frontend" / "dist"),
        )

    def validate(self) -> None:
        """선택된 공급자에 필요한 키·패키지·데이터 파일이 있는지 확인. 빠진 것을 모두 모아 알려 준다."""
        problems: list[str] = []
        if self.stt_provider not in STT_PROVIDERS:
            problems.append(f"STT_PROVIDER='{self.stt_provider}' 는 지원하지 않습니다 ({'|'.join(STT_PROVIDERS)})")
        if self.llm_provider not in LLM_PROVIDERS:
            problems.append(f"LLM_PROVIDER='{self.llm_provider}' 는 지원하지 않습니다 ({'|'.join(LLM_PROVIDERS)})")

        if self.stt_provider == "openai" and not self.openai_api_key:
            problems.append("OPENAI_API_KEY 가 없습니다 (STT_PROVIDER=openai 에 필요)")
        if self.stt_provider == "local" and importlib.util.find_spec("faster_whisper") is None:
            problems.append(
                "faster-whisper 패키지가 없습니다 (STT_PROVIDER=local 에 필요: "
                "python -m pip install -r backend/requirements-local.txt)"
            )
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY 가 없습니다 (LLM_PROVIDER=anthropic 에 필요)")
        if self.llm_provider == "openai" and not self.openai_api_key:
            problems.append("OPENAI_API_KEY 가 없습니다 (LLM_PROVIDER=openai 에 필요)")

        for name in ("dialect_dict.json", "scenarios.json", "search_docs.json"):
            if not (self.data_dir / name).is_file():
                problems.append(f"데이터 파일이 없습니다: {self.data_dir / name}")

        if problems:
            lines = "\n".join(f"  - {p}" for p in problems)
            raise ConfigError(
                "서버 설정 오류 — 다음 항목을 확인하세요 (.env.example 참고, 키 없이 화면만 보려면 "
                f"STT_PROVIDER=mock LLM_PROVIDER=mock):\n{lines}"
            )
