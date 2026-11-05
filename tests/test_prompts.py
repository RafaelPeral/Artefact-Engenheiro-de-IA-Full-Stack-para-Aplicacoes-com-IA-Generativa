"""Verify that deployed Markdown resources work outside the project directory."""

import pytest

from app.prompts import load_prompt


@pytest.mark.parametrize("name", ["assistant.md", "classification.md", "pdf_answer.md",
                                  "database_plan.md", "lookup_answer.md",
                                  "mixed_answer.md"])
def test_prompts_are_loaded_from_package_not_working_directory(name, tmp_path, monkeypatch):
    load_prompt.cache_clear()
    monkeypatch.chdir(tmp_path)
    content = load_prompt(name)
    assert content.strip()
    assert not (tmp_path / name).exists()


def test_prompt_loader_rejects_unbundled_paths():
    with pytest.raises(ValueError, match="Unknown prompt"):
        load_prompt("../app.py")
