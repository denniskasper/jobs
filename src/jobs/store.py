"""The store and the data repository. On the server the data directory is a volume holding a
clone of the data repository (decisions 9 and 10): cloned into the volume when it is empty,
brought up to date when it is not, and pushed back after every commit. On a machine with no data
repository the store is a repository of its own, which the first start creates."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from jobs.git import GitFailed, is_repository_root, run_git

# A start must not hang on a network that never answers.
PATIENCE_IN_SECONDS = 120

# A push holds up the request whose commit it carries, so it gives up sooner.
PUSH_PATIENCE_IN_SECONDS = 30

# Hands git the token out of its own environment, so it is never an argument another process
# could read, never part of the URL, and never written into the volume.
_TOKEN_VARIABLE = "JOBS_DATA_REPO_TOKEN"
_CREDENTIAL_HELPER = (
    f'!f() {{ echo username=x-access-token; echo "password=${_TOKEN_VARIABLE}"; }}; f'
)


@dataclass(frozen=True)
class Origin:
    """Where the data repository is cloned from, pulled from and pushed to, and the credential
    that reads and writes it."""

    url: str
    token: str = field(repr=False)


class StoreNotUsable(Exception):
    pass


def bring_up_to_date(data_dir: Path, origin: Origin | None) -> str | None:
    """Clone the data repository into an empty `data_dir`, or fast-forward the clone in it.

    Returns a warning when an existing store could not be brought up to date: it is still the
    newest copy the server has, so the service starts on it. Raises StoreNotUsable when there
    is no store to start on and none can be made. With no `origin` a store that exists is left
    exactly as it is, and a `data_dir` that is missing or empty becomes a repository with
    nothing in it.
    """
    if origin is None:
        if not is_repository_root(data_dir):
            _create(data_dir)
        return None
    if not is_repository_root(data_dir):
        _clone(data_dir, origin)
        return None
    try:
        _git(data_dir, origin, "pull", "--quiet", "--ff-only", origin.url)
    except GitFailed as failed:
        return f"the store was not brought up to date: {failed}"
    return None


def push(data_dir: Path, origin: Origin) -> None:
    """Push the branch the store is on, with every commit not pushed before. Raises GitFailed.

    Never forced: the server is the only writer (decision 10), so a data repository that has
    moved ahead means someone else wrote to it, and that is for a human to look at.
    """
    try:
        _git(
            data_dir,
            origin,
            "push",
            "--quiet",
            origin.url,
            "HEAD",
            patience=PUSH_PATIENCE_IN_SECONDS,
        )
    except GitFailed as failed:
        if "[rejected]" in str(failed):
            raise GitFailed(
                "the data repository has commits this server does not have, and nothing was "
                "overwritten. Someone else has pushed to it; bring the two together by hand"
            ) from failed
        raise


def _create(data_dir: Path) -> None:
    try:
        # Files that are no repository were put there by someone; the service does not adopt
        # them.
        if data_dir.exists() and not _is_empty_directory(data_dir):
            raise StoreNotUsable(f"{data_dir} is neither empty nor a git repository")
        data_dir.mkdir(parents=True, exist_ok=True)
        run_git(data_dir, "init", "--quiet", "--initial-branch=main")
    except (OSError, GitFailed) as failed:
        raise StoreNotUsable(f"{data_dir} could not be made a git repository: {failed}") from failed


def _clone(data_dir: Path, origin: Origin) -> None:
    # Whatever is in a volume that is not a clone was put there by someone; it is not ours to
    # clone over.
    if not _is_empty_directory(data_dir):
        raise StoreNotUsable(f"{data_dir} is neither empty nor a git repository")
    try:
        _git(data_dir, origin, "clone", "--quiet", origin.url, ".")
    except GitFailed as failed:
        # A clone that was cut short leaves half a repository behind, and the next start would
        # find a volume that is neither empty nor a clone. It was empty a moment ago, so
        # everything in it now is the clone's.
        for leftover in data_dir.iterdir():
            if leftover.is_dir() and not leftover.is_symlink():
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)
        raise StoreNotUsable(f"the data repository could not be cloned: {failed}") from failed


def _is_empty_directory(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


def _git(
    data_dir: Path, origin: Origin, *arguments: str, patience: float = PATIENCE_IN_SECONDS
) -> str:
    # The empty value first drops every helper configured on the machine.
    helpers = ["-c", "credential.helper="]
    if origin.token:
        helpers += ["-c", f"credential.helper={_CREDENTIAL_HELPER}"]
    return run_git(
        data_dir,
        *helpers,
        *arguments,
        # The C locale keeps what git says in the words `push` looks for.
        env={_TOKEN_VARIABLE: origin.token, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        timeout=patience,
    )
