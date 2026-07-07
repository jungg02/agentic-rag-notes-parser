import math

from app.ingestion.embedder import embed_query, embed_texts


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_embed_texts_returns_384_dim_normalized_vectors():
    vectors = embed_texts(["The mitochondria is the powerhouse of the cell."])
    assert len(vectors) == 1
    assert len(vectors[0]) == 384
    norm = math.sqrt(sum(x * x for x in vectors[0]))
    assert abs(norm - 1.0) < 1e-3


def test_similar_sentences_score_higher_than_dissimilar():
    vectors = embed_texts([
        "The mitochondria is the powerhouse of the cell.",
        "Mitochondria generate ATP through cellular respiration.",
        "The stock market closed lower today amid inflation fears.",
    ])
    sim_related = _cosine(vectors[0], vectors[1])
    sim_unrelated = _cosine(vectors[0], vectors[2])
    assert sim_related > sim_unrelated


def test_embed_query_uses_prefix_and_returns_384_dim():
    vector = embed_query("what does the mitochondria do?")
    assert len(vector) == 384
