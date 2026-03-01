"""Context Store: named PDF containers with BM25-indexed chunks for knowledge base feature."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from pypdf import PdfReader

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\u00c0-\u024f]{2,}")
_WHITESPACE_RE = re.compile(r"\s+")

# Common stop words (English + Italian)
_STOP_WORDS = {
    "the","and","for","with","that","this","have","from","your","you","are","was",
    "were","will","can","not","but","use","using","about","what","when","where",
    "which","how","why","who","all","any","too","also","its","into","than","then",
    "them","they","their",
    "cosa","parla","della","delle","degli","dallo","dalle","dopo","prima","come",
    "sono","puoi","puo","anche","nel","nella","nelle","sul","sulla","sulle","che",
    "per","con","una","uno","gli","dei","del","dai","una","non","una","piu","ogni",
}

CHUNK_CHARS = 900
CHUNK_OVERLAP = 160
CONTAINERS_FILE = "containers.json"


@dataclass
class Chunk:
    source: str
    page: int
    text: str
    terms: list[str]  # stored as sorted list for JSON serialization

    def term_set(self) -> set[str]:
        return set(self.terms)


@dataclass
class ContainerDoc:
    filename: str
    page_count: int
    chunk_count: int


@dataclass
class Container:
    id: str
    name: str
    documents: list[ContainerDoc] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)

    def total_chunks(self) -> int:
        return len(self.chunks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "documents": [asdict(d) for d in self.documents],
            "chunks": [
                {"source": c.source, "page": c.page, "text": c.text, "terms": c.terms}
                for c in self.chunks
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Container":
        docs = [ContainerDoc(**d) for d in data.get("documents", [])]
        chunks = [
            Chunk(
                source=c["source"],
                page=c["page"],
                text=c["text"],
                terms=c.get("terms", []),
            )
            for c in data.get("chunks", [])
        ]
        return cls(id=data["id"], name=data["name"], documents=docs, chunks=chunks)


class ContextStore:
    """CRUD for named PDF containers, persisted to containers.json."""

    def __init__(self, base_dir: Path) -> None:
        self._path = base_dir / CONTAINERS_FILE
        self._containers: dict[str, Container] = {}
        self._load()

    # ── Public CRUD ──────────────────────────────────────────────────────────

    def create(self, name: str) -> Container:
        container = Container(id=str(uuid.uuid4()), name=name.strip())
        self._containers[container.id] = container
        self._save()
        return container

    def rename(self, container_id: str, new_name: str) -> bool:
        c = self._containers.get(container_id)
        if c is None:
            return False
        c.name = new_name.strip()
        self._save()
        return True

    def delete(self, container_id: str) -> bool:
        if container_id not in self._containers:
            return False
        del self._containers[container_id]
        self._save()
        return True

    def get(self, container_id: str) -> Container | None:
        return self._containers.get(container_id)

    def all(self) -> list[Container]:
        return list(self._containers.values())

    # ── PDF ingestion ─────────────────────────────────────────────────────────

    def add_pdf(self, container_id: str, pdf_path: str) -> int:
        """Extract and index a PDF into the container. Returns number of new chunks."""
        container = self._containers.get(container_id)
        if container is None:
            raise ValueError(f"Container not found: {container_id}")

        path = Path(pdf_path)
        if not path.exists():
            raise ValueError(f"PDF not found: {pdf_path}")

        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            raise ValueError(f"Cannot read PDF '{path.name}': {exc}") from exc

        new_chunks: list[Chunk] = []
        for page_idx, page in enumerate(reader.pages, start=1):
            raw = page.extract_text() or ""
            text = _WHITESPACE_RE.sub(" ", raw).strip()
            if not text:
                continue
            for chunk_text in _split_text(text):
                terms = _extract_terms(chunk_text)
                if not terms:
                    continue
                new_chunks.append(Chunk(
                    source=path.name,
                    page=page_idx,
                    text=chunk_text,
                    terms=sorted(terms),
                ))

        container.chunks.extend(new_chunks)

        # Update document summary
        existing = next((d for d in container.documents if d.filename == path.name), None)
        if existing:
            existing.page_count = len(reader.pages)
            existing.chunk_count = sum(1 for c in container.chunks if c.source == path.name)
        else:
            container.documents.append(ContainerDoc(
                filename=path.name,
                page_count=len(reader.pages),
                chunk_count=len(new_chunks),
            ))

        self._save()
        return len(new_chunks)

    def remove_pdf(self, container_id: str, filename: str) -> bool:
        container = self._containers.get(container_id)
        if container is None:
            return False
        container.chunks = [c for c in container.chunks if c.source != filename]
        container.documents = [d for d in container.documents if d.filename != filename]
        self._save()
        return True

    # ── Export for BLE transfer ────────────────────────────────────────────────

    def export_for_transfer(self, container_id: str) -> dict[str, Any]:
        """Returns a JSON-serializable dict suitable for the BLE load_container message."""
        container = self._containers.get(container_id)
        if container is None:
            raise ValueError(f"Container not found: {container_id}")
        return container.to_dict()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("containers", []):
                c = Container.from_dict(item)
                self._containers[c.id] = c
        except Exception:
            pass

    def _save(self) -> None:
        try:
            payload = {"containers": [c.to_dict() for c in self._containers.values()]}
            with self._path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# ── Text processing helpers ────────────────────────────────────────────────────

def _split_text(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        candidate = text[start:end]
        if end < len(text):
            dot = candidate.rfind(". ")
            if dot > CHUNK_CHARS // 2:
                end = start + dot + 1
                candidate = text[start:end]
        candidate = candidate.strip()
        if candidate:
            chunks.append(candidate)
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, 0)
    return chunks


def _extract_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in _TOKEN_RE.findall(text.lower()):
        if token in _STOP_WORDS or len(token) < 3:
            continue
        terms.add(token)
    return terms
