"""The real Model, against a stand-in for the command it runs. No model is ever asked."""

from pathlib import Path

import pytest

from jobs.models import ClaudeCli, ModelUnavailable


def command(tmp_path: Path, script: str) -> str:
    path = tmp_path / "stand-in"
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_the_prompt_goes_in_and_the_answer_comes_out(tmp_path: Path) -> None:
    model = ClaudeCli("some-model", command(tmp_path, 'echo "asked: $(cat)"'))

    assert model.ask("which links lead onward?").strip() == "asked: which links lead onward?"


def test_the_model_is_named_and_given_no_tools(tmp_path: Path) -> None:
    model = ClaudeCli("some-model", command(tmp_path, 'printf "%s\\n" "$@"'))

    arguments = model.ask("anything").splitlines()

    assert arguments[arguments.index("--model") + 1] == "some-model"
    assert arguments[arguments.index("--tools") + 1] == ""


def test_a_command_that_fails_is_a_model_that_is_unavailable(tmp_path: Path) -> None:
    model = ClaudeCli("some-model", command(tmp_path, "echo 'not signed in' >&2; exit 1"))

    with pytest.raises(ModelUnavailable, match="not signed in"):
        model.ask("anything")


def test_a_command_with_no_credential_says_so_in_plain_words(tmp_path: Path) -> None:
    """The command's own words are an instruction to a developer at a terminal; the registration
    form that shows this is neither (ticket 15)."""
    said = "Not logged in · Please run /login"
    model = ClaudeCli("some-model", command(tmp_path, f"echo '{said}' >&2; exit 1"))

    with pytest.raises(ModelUnavailable, match="credential .* is missing or has expired"):
        model.ask("anything")


def test_a_command_that_is_not_installed_is_a_model_that_is_unavailable(tmp_path: Path) -> None:
    model = ClaudeCli("some-model", str(tmp_path / "missing"))

    with pytest.raises(ModelUnavailable, match="not installed"):
        model.ask("anything")
