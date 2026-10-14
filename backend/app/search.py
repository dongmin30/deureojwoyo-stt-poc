"""모의 검색엔진: 음절 bigram 토큰 + BM25."""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ]+")


def tokenize(text: str | None) -> list[str]:
    """어절마다 음절 bigram. 한 글자 어절은 그대로 한 토큰."""
    tokens: list[str] = []
    for word in _NON_WORD.sub(" ", (text or "").lower()).split():
        if len(word) == 1:
            tokens.append(word)
        else:
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


def doc_text(doc: dict) -> str:
    return " ".join([doc.get("title", ""), doc.get("snippet", ""), " ".join(doc.get("keywords", []))])


class SearchEngine:
    def __init__(self, docs: list[dict]):
        self.docs = docs
        self._bm25 = BM25Okapi([tokenize(doc_text(d)) or ["_"] for d in docs])

    def search(self, query: str, k: int = 3) -> list[dict]:
        """점수가 0보다 큰 문서만 상위 k개. 검색어가 비었거나 겹치는 토큰이 없으면 빈 목록."""
        q = tokenize(query)
        if not q:
            return []
        scores = self._bm25.get_scores(q)
        order = sorted(range(len(self.docs)), key=lambda i: -scores[i])
        out = []
        for i in order[:k]:
            if scores[i] <= 0:
                break
            d = self.docs[i]
            out.append({"id": d["id"], "title": d["title"], "snippet": d["snippet"], "score": round(float(scores[i]), 3)})
        return out


def hit_at_k(results: list[dict], target_doc_id: str | None, k: int = 3) -> bool | None:
    if not target_doc_id:
        return None
    return any(r["id"] == target_doc_id for r in results[:k])
