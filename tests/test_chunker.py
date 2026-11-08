from src.dialogue.chunker import TokenChunker


def test_chunker_splits_long_transcript():
    paragraph = "זוהי פסקה ארוכה עם הרבה מילים שמתארות תרחישי ענן."
    text = "\n\n".join([paragraph] * 20)
    chunker = TokenChunker(max_tokens=50, overlap_tokens=5)
    chunks = list(chunker.chunk(text))
    assert len(chunks) >= 2
    assert sum(len(chunk.split()) for chunk in chunks) >= len(text.split()) * 0.9


def test_chunker_overlap_carries_the_end_of_the_previous_chunk():
    paragraphs = [f"paragraph {i} " + "word " * 30 + f"END{i}" for i in range(6)]
    chunker = TokenChunker(max_tokens=80, overlap_tokens=5)
    chunks = list(chunker.chunk("\n\n".join(paragraphs)))
    assert len(chunks) >= 2
    for previous, current in zip(chunks, chunks[1:]):
        last_marker = previous.split()[-1]
        assert last_marker in current.split("\n\n")[0]

