"""The model boundary. Nothing calls a model directly; it is handed a Model (decision 30).

Deterministic code runs the spine. A model is asked only where a decision says so, and in the
Source lifecycle that is registration alone (ADR 0003).
"""

import subprocess
import tempfile
from typing import Protocol

# Deriving a config reads a few pages; a model that has not answered by now will not.
PATIENCE_IN_SECONDS = 180


class Model(Protocol):
    name: str
    """Which model answers, as it is recorded in the provenance of what it derived."""

    def ask(self, prompt: str) -> str:
        """The model's answer to `prompt`, as text. Raises ModelUnavailable."""
        ...


class ModelUnavailable(Exception):
    pass


class NoModel:
    """A Model that refuses every question."""

    name = "none"

    def ask(self, prompt: str) -> str:
        raise ModelUnavailable("no model is available")


class ClaudeCli:
    """The real Model: the `claude` command in print mode, under whatever plan it is signed in
    to. A prompt carries text from pages anyone can write, so the model is given no tools, no
    settings and no servers: all it can do with what it reads is answer."""

    def __init__(self, name: str, command: str = "claude") -> None:
        self.name = name
        self.command = command

    def ask(self, prompt: str) -> str:
        try:
            done = subprocess.run(
                [
                    self.command,
                    "--print",
                    "--model",
                    self.name,
                    "--tools",
                    "",
                    "--restricted",
                    "--strict-mcp-config",
                    "--no-session-persistence",
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=PATIENCE_IN_SECONDS,
                # Away from any project, whose instructions would otherwise be read along.
                cwd=tempfile.gettempdir(),
            )
        except FileNotFoundError:
            raise ModelUnavailable(f"the {self.command} command is not installed") from None
        except subprocess.TimeoutExpired:
            raise ModelUnavailable(
                f"{self.name} did not answer within {PATIENCE_IN_SECONDS} seconds"
            ) from None
        except OSError as error:
            raise ModelUnavailable(f"{self.command} could not be run: {error}") from error
        if done.returncode != 0:
            said = " ".join((done.stderr or done.stdout).split())
            if "not logged in" in said.lower():
                # What the command says here — "Please run /login" — is an instruction to a
                # developer sitting at a terminal, and a registration form is neither.
                raise ModelUnavailable(
                    f"the credential for {self.command} is missing or has expired"
                )
            raise ModelUnavailable(f"{self.command} failed: {said or done.returncode}")
        return done.stdout
