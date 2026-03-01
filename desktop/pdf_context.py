from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_WHITESPACE_RE = re.compile(r"\s+")
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "have",
    "from",
    "your",
    "you",
    "are",
    "was",
    "were",
    "will",
    "can",
    "not",
    "but",
    "use",
    "using",
    "about",
    "what",
    "when",
    "where",
    "which",
    "how",
    "why",
    "who",
    "all",
    "any",
    "too",
    "also",
    "its",
    "into",
    "than",
    "then",
    "them",
    "they",
    "their",
    "pdf",
    "document",
    "documents",
    "file",
    "files",
    "cosa",
    "parla",
    "della",
    "delle",
    "degli",
    "dallo",
    "dalle",
    "dopo",
    "prima",
    "come",
    "sono",
    "puoi",
    "puo",
    "anche",
    "nel",
    "nella",
    "nelle",
    "sul",
    "sulla",
    "sulle",
    "che",
    "per",
    "con",
    "una",
    "uno",
    "gli",
    "dei",
    "del",
    "dai",
}
_OVERVIEW_HINTS = {
    "summary",
    "summarize",
    "overview",
    "riassunto",
    "riassumi",
    "sintesi",
    "spiega",
    "argomento",
    "tema",
}


@dataclass
class _Chunk:
    source: str
    page: int
    text: str
    terms: set[str]


class PdfContextEngine:
    def __init__(
        self,
        chunk_chars: int = 900,
        chunk_overlap: int = 160,
        max_blocks: int = 6,
        max_total_chars: int = 5600,
    ) -> None:
        self._chunk_chars = chunk_chars
        self._chunk_overlap = chunk_overlap
        self._max_blocks = max_blocks
        self._max_total_chars = max_total_chars
        self._cache: dict[str, tuple[int, list[_Chunk]]] = {}

    def build_context(self, query: str, pdf_paths: list[str]) -> list[dict[str, Any]]:
        all_chunks: list[_Chunk] = []
        for raw_path in pdf_paths:
            chunks = self._load_pdf_chunks(raw_path)
            all_chunks.extend(chunks)

        if not all_chunks:
            return []

        if not query.strip():
            return self._fallback_context(all_chunks)

        query_terms = self._extract_terms(query)
        if not query_terms:
            return self._fallback_context(all_chunks)

        scored: list[tuple[float, _Chunk]] = []
        query_text = query.lower()
        for chunk in all_chunks:
            overlap = len(query_terms.intersection(chunk.terms))
            if overlap == 0:
                continue

            text_lower = chunk.text.lower()
            phrase_bonus = 0.0
            if query_text in text_lower:
                phrase_bonus += 4.0

            score = float(overlap) + phrase_bonus
            scored.append((score, chunk))

        if not scored:
            return self._fallback_context(all_chunks)

        scored.sort(key=lambda item: item[0], reverse=True)

        selected: list[dict[str, Any]] = []
        used = set()
        total_chars = 0
        for _, chunk in scored:
            key = (chunk.source, chunk.page, chunk.text)
            if key in used:
                continue

            block_text = chunk.text.strip()
            if not block_text:
                continue

            projected = total_chars + len(block_text)
            if selected and projected > self._max_total_chars:
                continue

            selected.append(
                {
                    "source": chunk.source,
                    "page": chunk.page,
                    "text": block_text,
                }
            )
            used.add(key)
            total_chars = projected

            if len(selected) >= self._max_blocks:
                break

        # Generic requests ("what is this PDF about?") benefit from introductory chunks.
        if self._is_overview_query(query, query_terms) and len(selected) < self._max_blocks:
            existing = {(b["source"], int(b["page"]), b["text"]) for b in selected}
            for block in self._fallback_context(all_chunks):
                key = (str(block["source"]), int(block["page"]), str(block["text"]))
                if key in existing:
                    continue
                selected.append(block)
                if len(selected) >= self._max_blocks:
                    break

        return selected

    def _is_overview_query(self, query: str, query_terms: set[str]) -> bool:
        lowered = query.lower()
        if any(hint in lowered for hint in _OVERVIEW_HINTS):
            return True
        if len(query_terms) <= 2 and any(marker in lowered for marker in ("pdf", "document", "file")):
            return True
        return False

    def _fallback_context(self, chunks: list[_Chunk]) -> list[dict[str, Any]]:
        if not chunks:
            return []

        ordered = sorted(chunks, key=lambda item: (item.source.lower(), item.page))
        unique_sources = {chunk.source for chunk in ordered}
        max_per_source = 2 if len(unique_sources) > 1 else 4

        selected: list[dict[str, Any]] = []
        chars = 0
        per_source_count: dict[str, int] = {}
        used: set[tuple[str, int, str]] = set()

        for chunk in ordered:
            source_count = per_source_count.get(chunk.source, 0)
            if source_count >= max_per_source:
                continue

            text = chunk.text.strip()
            if not text:
                continue

            key = (chunk.source, chunk.page, text)
            if key in used:
                continue

            projected = chars + len(text)
            if selected and projected > self._max_total_chars:
                continue

            selected.append(
                {
                    "source": chunk.source,
                    "page": chunk.page,
                    "text": text,
                }
            )
            used.add(key)
            per_source_count[chunk.source] = source_count + 1
            chars = projected

            if len(selected) >= self._max_blocks:
                break

        return selected

    def _load_pdf_chunks(self, raw_path: str) -> list[_Chunk]:
        path = Path(raw_path)
        if not path.exists():
            raise ValueError(f"PDF not found: {raw_path}")

        mtime_ns = path.stat().st_mtime_ns
        cache_key = str(path.resolve())
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]

        chunks = self._extract_chunks(path)
        self._cache[cache_key] = (mtime_ns, chunks)
        return chunks

    def _extract_chunks(self, path: Path) -> list[_Chunk]:
        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            raise ValueError(f"Cannot read PDF '{path.name}': {exc}") from exc

        out: list[_Chunk] = []
        for page_idx, page in enumerate(reader.pages, start=1):
            text = self._normalize_text(page.extract_text() or "")
            if not text:
                continue

            for part in self._split_text(text):
                terms = self._extract_terms(part)
                if not terms:
                    continue

                out.append(
                    _Chunk(
                        source=path.name,
                        page=page_idx,
                        text=part,
                        terms=terms,
                    )
                )

        return out

    def _split_text(self, text: str) -> list[str]:
        if len(text) <= self._chunk_chars:
            return [text]

        chunks: list[str] = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + self._chunk_chars, text_len)
            candidate = text[start:end]

            # Prefer ending on sentence boundary when possible.
            if end < text_len:
                dot = candidate.rfind(". ")
                if dot > self._chunk_chars // 2:
                    end = start + dot + 1
                    candidate = text[start:end]

            candidate = candidate.strip()
            if candidate:
                chunks.append(candidate)

            if end >= text_len:
                break

            start = max(end - self._chunk_overlap, 0)

        return chunks

    def _normalize_text(self, value: str) -> str:
        return _WHITESPACE_RE.sub(" ", value).strip()

    def _extract_terms(self, text: str) -> set[str]:
        terms: set[str] = set()
        for token in _TOKEN_RE.findall(text.lower()):
            if token in _STOP_WORDS:
                continue
            if len(token) < 3:
                continue
            terms.add(token)
        return terms
