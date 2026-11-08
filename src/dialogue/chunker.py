from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List

try:
    import tiktoken
except ImportError:  # pragma: no cover - fallback for environments without tiktoken
    tiktoken = None


def _basic_tokenizer(text: str) -> List[str]:
    return re.findall(r"\w+|\S", text)


@dataclass
class TokenChunker:
    """Split long transcripts into token-aware chunks for model consumption."""

    max_tokens: int = 12000
    overlap_tokens: int = 200
    encoding_name: str = "cl100k_base"

    def __post_init__(self) -> None:
        if tiktoken is not None:
            self._encoder = tiktoken.get_encoding(self.encoding_name)
        else:  # pragma: no cover
            self._encoder = None

    def _encode(self, text: str) -> List[int]:
        if self._encoder:
            return self._encoder.encode(text)
        return list(range(len(_basic_tokenizer(text))))

    def _tail(self, text: str) -> str:
        if self._encoder:
            return self._encoder.decode(self._encoder.encode(text)[-self.overlap_tokens :])
        return " ".join(text.split()[-self.overlap_tokens :])

    def chunk(self, text: str) -> Iterable[str]:
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        if not paragraphs:
            return []

        chunks: List[str] = []
        current_tokens = 0
        current_parts: List[str] = []

        for paragraph in paragraphs:
            token_len = len(self._encode(paragraph))
            if current_tokens + token_len <= self.max_tokens:
                current_parts.append(paragraph)
                current_tokens += token_len
                continue

            overlap = ""
            if current_parts:
                closed = "\n\n".join(current_parts)
                chunks.append(closed)
                # carry the tail of the closed chunk into the next one for context
                if self.overlap_tokens > 0:
                    overlap = self._tail(closed)

            if token_len >= self.max_tokens:
                chunks.append(paragraph)
                current_parts = []
                current_tokens = 0
            else:
                current_parts = [overlap, paragraph] if overlap else [paragraph]
                current_tokens = token_len + (len(self._encode(overlap)) if overlap else 0)

        if current_parts:
            chunks.append("\n\n".join(current_parts))
        return chunks

