"""Running git. Nothing of git's is taken from the environment the service was started in."""

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


class GitFailed(Exception):
    pass


def run_git(
    directory: Path,
    *arguments: str,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    """Run git in `directory` and return what it printed. Raises GitFailed."""
    # An inherited GIT_DIR or GIT_INDEX_FILE would point git at some other repository.
    inherited = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    try:
        done = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            env={**inherited, **(env or {})},
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as expired:
        raise GitFailed(f"git gave no answer within {expired.timeout:.0f} seconds") from expired
    except OSError as error:
        raise GitFailed(f"git could not be run: {error.strerror}") from error
    if done.returncode != 0:
        raise GitFailed(" ".join(done.stderr.split()) or "git gave no reason")
    return done.stdout


def is_repository_root(directory: Path) -> bool:
    """Whether `directory` is the root of a repository, rather than merely inside one.

    The data directory sits ignored inside the engine's working tree, so git asked about a
    directory that is no repository of its own would walk up and answer for the engine.
    """
    try:
        return not run_git(directory, "rev-parse", "--show-cdup").strip()
    except GitFailed:
        return False
