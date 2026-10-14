"""방언 사전 로드와 STT 힌트·LLM 사전 문자열 생성."""
from __future__ import annotations

import json
from pathlib import Path

NONE_REGION = {"id": "none", "name": "모름/표준어"}


class DialectDict:
    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def load(cls, path: Path) -> "DialectDict":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: 최상위는 지역 id → {{name, entries}} 객체여야 합니다")
        for rid, region in data.items():
            if not isinstance(region, dict) or not isinstance(region.get("entries"), dict):
                raise ValueError(f"{path}: 지역 '{rid}' 에 entries 객체가 없습니다")
        return cls(data)

    def regions(self) -> list[dict]:
        out = [{**NONE_REGION, "count": 0}]
        for rid, region in self._data.items():
            out.append({"id": rid, "name": region.get("name", rid), "count": len(region["entries"])})
        return out

    def has_region(self, region_id: str) -> bool:
        return region_id == "none" or region_id in self._data

    def region_name(self, region_id: str) -> str:
        if region_id in self._data:
            return self._data[region_id].get("name", region_id)
        return NONE_REGION["name"]

    def entries(self, region_id: str) -> dict[str, str]:
        region = self._data.get(region_id)
        return dict(region["entries"]) if region else {}

    def stt_hint(self, region_id: str, max_words: int = 30, max_chars: int = 200) -> str:
        """STT prompt 에 넣을 방언 단어 일부. Whisper prompt 는 길이 제한(약 224 토큰)이 있어 앞쪽 일부만 쓴다."""
        words: list[str] = []
        length = 0
        for word in self.entries(region_id):
            if len(words) >= max_words or length + len(word) + 2 > max_chars:
                break
            words.append(word)
            length += len(word) + 2
        if not words:
            return ""
        return f"{self.region_name(region_id)} 사투리 대화. " + ", ".join(words)

    def dict_text(self, region_id: str) -> str:
        """LLM 보정 프롬프트용 해당 지역 전체 사전."""
        return "\n".join(f"- {k} → {v}" for k, v in self.entries(region_id).items())
