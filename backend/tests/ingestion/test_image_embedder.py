import math

from PIL import Image

from app.ingestion.image_embedder import SIGLIP_EMBEDDING_DIM, embed_image_query, embed_images


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def test_embed_images_returns_correct_dim_normalized_vectors():
    img = Image.new("RGB", (224, 224), color="red")
    vectors = embed_images([img])
    assert len(vectors) == 1
    assert len(vectors[0]) == SIGLIP_EMBEDDING_DIM
    assert abs(_norm(vectors[0]) - 1.0) < 1e-3


def test_embed_images_batches_multiple_images():
    images = [Image.new("RGB", (224, 224), color=c) for c in ["red", "blue", "green"]]
    vectors = embed_images(images)
    assert len(vectors) == 3
    assert all(len(v) == SIGLIP_EMBEDDING_DIM for v in vectors)


def test_embed_images_empty_list_returns_empty_list():
    assert embed_images([]) == []


def test_embed_image_query_returns_correct_dim_normalized_vector():
    vector = embed_image_query("a diagram of a data pipeline")
    assert len(vector) == SIGLIP_EMBEDDING_DIM
    assert abs(_norm(vector) - 1.0) < 1e-3


def test_same_image_embedded_twice_is_deterministic():
    img = Image.new("RGB", (224, 224), color="purple")
    v1 = embed_images([img])[0]
    v2 = embed_images([img])[0]
    assert v1 == v2
