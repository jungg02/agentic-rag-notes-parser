from bench.phase3_dataset import MEMORY_CATEGORY, REWRITE_CATEGORIES, load_items


def test_load_items_returns_course_id_and_items():
    course_id, items = load_items()
    assert course_id == 4
    assert len(items) >= 50


def test_every_item_has_a_non_empty_query_and_expected_set():
    _, items = load_items()
    for item in items:
        assert item.query
        assert item.expected


def test_cross_session_memory_items_have_a_seed_memory():
    _, items = load_items()
    for item in items:
        if item.category == MEMORY_CATEGORY:
            assert item.memory is not None
            assert item.memory.content
        else:
            assert item.memory is None


def test_multi_turn_categories_have_more_than_one_turn():
    _, items = load_items()
    for item in items:
        if item.category in REWRITE_CATEGORIES:
            assert len(item.turns) >= 2
            assert item.history == item.turns[:-1]


def test_item_ids_are_unique():
    _, items = load_items()
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids))
