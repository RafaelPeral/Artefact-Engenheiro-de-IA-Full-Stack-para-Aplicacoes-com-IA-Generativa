"""Verify that deployed Markdown resources work outside the project directory."""

import pytest

from app.prompts import load_prompt


@pytest.mark.parametrize("name", ["assistant.md", "classification.md", "pdf_answer.md",
                                  "database_plan.md", "lookup_answer.md",
                                  "mixed_answer.md", "persona.md"])
def test_prompts_are_loaded_from_package_not_working_directory(name, tmp_path, monkeypatch):
    load_prompt.cache_clear()
    monkeypatch.chdir(tmp_path)
    content = load_prompt(name)
    assert content.strip()
    assert not (tmp_path / name).exists()


def test_prompt_loader_rejects_unbundled_paths():
    with pytest.raises(ValueError, match="Unknown prompt"):
        load_prompt("../app.py")


@pytest.mark.parametrize("name", [
    "assistant.md", "lookup_answer.md", "pdf_answer.md", "mixed_answer.md",
])
def test_answer_prompts_share_the_static_persona(name):
    persona = load_prompt("persona.md")
    prompt = load_prompt(name)
    assert prompt.startswith(persona + "\n\n")
    assert prompt.count(persona) == 1


@pytest.mark.parametrize("name", ["classification.md", "database_plan.md"])
def test_internal_decision_prompts_do_not_receive_customer_persona(name):
    assert load_prompt("persona.md") not in load_prompt(name)


def test_loaded_persona_is_cached_without_rereading_files(monkeypatch):
    from pathlib import Path

    load_prompt.cache_clear()
    prompt = load_prompt("mixed_answer.md")

    def unexpected_read(*args, **kwargs):
        pytest.fail("Cached prompts must not be read again")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", unexpected_read)
        assert load_prompt("mixed_answer.md") is prompt
    load_prompt.cache_clear()
