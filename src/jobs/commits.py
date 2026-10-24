"""The commit boundary. Every mutation of the data ends in a commit that is pushed to the data
repository (decisions 9 and 10), and nothing that writes runs git directly; it is handed a
Committer."""

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from jobs.git import GitFailed, run_git
from jobs.store import Origin, push

# Who the system commits as, so its commits can be told from a human's at a glance.
SYSTEM_NAME = "jobs"
SYSTEM_EMAIL = "jobs@localhost"


class Committer(Protocol):
    def commit(self, message: str, paths: Sequence[Path]) -> None:
        """Commit `paths`, and nothing else, under `message`, and push where there is a data
        repository to push to. Raises CommitFailed.

        A push that fails raises nothing: the commit stands, and `not_pushed` says why it has
        gone no further.
        """
        ...

    def not_pushed(self) -> str | None:
        """Why there are commits the data repository does not have, or None when it has them
        all."""
        ...


class CommitFailed(Exception):
    pass


class NotARepository(Exception):
    pass


class GitCommitter:
    """Commits to the git repository whose working tree is the data directory.

    Only the paths handed over are committed, as they are on disk. Every other path a human has
    edited, staged or left untracked stays exactly as it was, neither swept into a system commit
    nor discarded.

    With an `origin`, every commit is followed by a push. Without one nothing is pushed, and
    nothing is ever reported as not pushed: the store is a repository of its own.
    """

    def __init__(self, data_dir: Path, origin: Origin | None = None) -> None:
        self._data_dir = data_dir
        self._origin = origin
        self._not_pushed: str | None = None
        # Requests are served from threads, and two git processes in one repository trip over
        # each other's index lock.
        self._one_at_a_time = threading.Lock()
        # The data directory sits ignored inside the engine's working tree. Were it not a
        # repository of its own, git would walk up and commit personal data to the engine.
        try:
            way_up = self._git("rev-parse", "--show-cdup")
        except CommitFailed as failed:
            raise NotARepository(f"{data_dir} is not a git repository: {failed}") from failed
        if way_up.strip():
            raise NotARepository(f"{data_dir} is inside a git repository, not the root of one")

    def commit(self, message: str, paths: Sequence[Path]) -> None:
        with self._one_at_a_time:
            self._commit(message, paths)
            self._push()

    def _commit(self, message: str, paths: Sequence[Path]) -> None:
        names = [str(path) for path in paths]
        self._git("add", "--", *names)
        try:
            self._git(
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "--no-verify",
                "--only",
                f"--message={message}",
                "--",
                *names,
            )
        except CommitFailed:
            self._unstage(names)
            raise

    def push(self) -> None:
        """Push every commit the data repository does not have yet. Also called at start, for
        what an earlier run left behind."""
        with self._one_at_a_time:
            self._push()

    def _push(self) -> None:
        if self._origin is None:
            return
        try:
            push(self._data_dir, self._origin)
        except GitFailed as failed:
            self._not_pushed = str(failed)
        else:
            self._not_pushed = None

    def not_pushed(self) -> str | None:
        return self._not_pushed

    def _unstage(self, names: Sequence[str]) -> None:
        try:
            self._git("reset", "--quiet", "--", *names)
        except CommitFailed:
            pass

    def _git(self, *arguments: str) -> str:
        identity = {
            "GIT_AUTHOR_NAME": SYSTEM_NAME,
            "GIT_AUTHOR_EMAIL": SYSTEM_EMAIL,
            "GIT_COMMITTER_NAME": SYSTEM_NAME,
            "GIT_COMMITTER_EMAIL": SYSTEM_EMAIL,
        }
        try:
            return run_git(self._data_dir, *arguments, env=identity)
        except GitFailed as failed:
            raise CommitFailed(str(failed)) from failed
