import pytest

from enterprise_rag.ingestion.chunking import ChunkSpan, Section, chunk_sections


def test_short_section_becomes_one_chunk_with_document_offsets():
    section = Section("Policy > Refunds", "Refunds within 30 days.", page=2, offset=100)

    chunks = chunk_sections([section], max_chars=50, overlap=10)

    assert chunks == [
        ChunkSpan(
            ordinal=0,
            text="Refunds within 30 days.",
            section_path="Policy > Refunds",
            page=2,
            char_start=100,
            char_end=123,
        )
    ]


def test_long_section_splits_on_word_boundaries_within_max_chars():
    text = "alpha beta gamma delta epsilon"
    chunks = chunk_sections([Section("H", text, page=None, offset=0)], max_chars=12, overlap=0)

    assert [(c.text, c.char_start, c.char_end) for c in chunks] == [
        ("alpha beta", 0, 10),
        ("gamma delta", 11, 22),
        ("epsilon", 23, 30),
    ]
    assert [c.ordinal for c in chunks] == [0, 1, 2]


def test_chunks_overlap_on_word_boundaries_and_always_add_new_text():
    text = "alpha beta gamma delta epsilon"
    chunks = chunk_sections([Section("H", text, page=None, offset=0)], max_chars=12, overlap=6)

    # "delta" alone would add nothing new after "gamma delta", so it is skipped.
    assert [(c.text, c.char_start, c.char_end) for c in chunks] == [
        ("alpha beta", 0, 10),
        ("beta gamma", 6, 16),
        ("gamma delta", 11, 22),
        ("epsilon", 23, 30),
    ]


def test_word_longer_than_max_chars_is_hard_split():
    text = "ab " + "x" * 25 + " cd"
    chunks = chunk_sections([Section("H", text, page=None, offset=0)], max_chars=10, overlap=0)

    assert [(c.text, c.char_start, c.char_end) for c in chunks] == [
        ("ab", 0, 2),
        ("xxxxxxxxxx", 3, 13),
        ("xxxxxxxxxx", 13, 23),
        ("xxxxx cd", 23, 31),
    ]


@pytest.mark.parametrize(("max_chars", "overlap"), [(0, 0), (-5, 0), (10, -1), (10, 10), (10, 11)])
def test_rejects_invalid_chunking_parameters(max_chars, overlap):
    with pytest.raises(ValueError, match="chunking"):
        chunk_sections([Section("H", "text", None, 0)], max_chars=max_chars, overlap=overlap)


def test_word_starting_exactly_at_overlap_boundary_is_reused():
    text = "alpha beta gamma delta epsilon"
    chunks = chunk_sections([Section("H", text, page=None, offset=0)], max_chars=12, overlap=4)

    # "beta" starts at 6 == 10 - 4, so it opens the second chunk.
    assert [(c.char_start, c.char_end) for c in chunks] == [(0, 10), (6, 16), (17, 22), (23, 30)]


def test_chunk_may_fill_exactly_max_chars():
    chunks = chunk_sections([Section("H", "abcd efgh ij", None, 0)], max_chars=9, overlap=0)

    assert [c.text for c in chunks] == ["abcd efgh", "ij"]


def test_ordinals_and_offsets_continue_across_sections():
    sections = [
        Section("A", "one two", page=1, offset=0),
        Section("B", "three", page=2, offset=8),
    ]

    chunks = chunk_sections(sections, max_chars=4, overlap=0)

    assert [(c.ordinal, c.text, c.section_path, c.char_start) for c in chunks] == [
        (0, "one", "A", 0),
        (1, "two", "A", 4),
        (2, "thre", "B", 8),
        (3, "e", "B", 12),
    ]
