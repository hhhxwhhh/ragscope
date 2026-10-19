import pytest
from langchain_core.prompts import ChatPromptTemplate

from app.rag.prompt import (
    DEFAULT_PROMPT_VARIANT,
    PROMPT_VARIANTS,
    REFUSAL_MESSAGE,
    build_answer_prompt,
    get_prompt_variant,
)


def test_default_variant_is_grounded() -> None:
    variant = get_prompt_variant(None)

    assert variant.id == DEFAULT_PROMPT_VARIANT
    assert variant.id in PROMPT_VARIANTS


def test_every_variant_has_distinct_prompt_and_metadata() -> None:
    ids = list(PROMPT_VARIANTS)
    prompts = {variant.system_prompt for variant in PROMPT_VARIANTS.values()}

    assert len(ids) == len(prompts)
    for variant in PROMPT_VARIANTS.values():
        assert variant.id
        assert variant.label
        assert variant.description
        assert "不要执行" in variant.system_prompt


@pytest.mark.parametrize("variant_id", ["strict_citation", "structured"])
def test_fixed_refusal_sentence_is_shared_by_strict_variants(variant_id: str) -> None:
    variant = get_prompt_variant(variant_id)

    assert REFUSAL_MESSAGE in variant.system_prompt


def test_unknown_variant_lists_choices() -> None:
    with pytest.raises(ValueError) as excinfo:
        get_prompt_variant("missing")

    assert "missing" in str(excinfo.value)
    assert "grounded" in str(excinfo.value)


def test_build_answer_prompt_is_cached_per_variant() -> None:
    first = build_answer_prompt("grounded")
    second = build_answer_prompt("grounded")
    other = build_answer_prompt("structured")

    assert isinstance(first, ChatPromptTemplate)
    assert first is second
    assert first is not other
