from sentence_transformers import SentenceTransformer

_MODEL = None

_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
    return _MODEL


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors = _model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_query(query: str) -> list[float]:
    vector = _model().encode(_QUERY_PREFIX + query, normalize_embeddings=True, show_progress_bar=False)
    return vector.tolist()
