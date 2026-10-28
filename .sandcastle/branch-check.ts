import { execFileSync } from 'node:child_process';

// The git commands run in the repository passed in, whatever directory the runner was started from.

// What a run is cut from, and what a branch has to be part of before it counts as finished.
const BASE = 'origin/main';

function git(repo: string, ...args: string[]): string {
  return execFileSync('git', args, {
    cwd: repo,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

function succeeds(repo: string, ...args: string[]): boolean {
  try {
    git(repo, ...args);
    return true;
  } catch {
    return false;
  }
}

/**
 * Refuses to start a run on a branch whose work is already on `main`, and says what a new branch
 * should be cut from.
 *
 * Sandcastle checks a branch out as it finds it. A branch that exists is used at the commit it
 * stands on, and is never brought forward from `main`. So a branch left behind after its work was
 * merged would start the next run on an old tree.
 *
 * Here work reaches `main` by fast-forwarding it to the branch. Afterwards the branch's tip sits
 * on `main`'s own line of commits, just like a branch that was cut and never worked on. Either
 * way it holds nothing that is not on `main`, so the local copy is removed and a new one cut.
 * A branch that still has commits `main` lacks is a run nobody has reviewed yet, and the next
 * run adds to it.
 */
export function baseForRun(repo: string, branch: string): string {
  try {
    git(repo, 'fetch', '--prune', 'origin');
  } catch {
    throw new Error(
      `Could not fetch from origin, so there is no telling whether '${branch}' is stale. ` +
        'Nothing was started.',
    );
  }

  const copies = [`refs/heads/${branch}`, `refs/remotes/origin/${branch}`].filter((ref) =>
    succeeds(repo, 'rev-parse', '--verify', '--quiet', ref),
  );

  const local = `refs/heads/${branch}`;
  if (copies.includes(local) && onMainsLine(repo, local)) {
    try {
      git(repo, 'branch', '-D', branch);
      copies.splice(copies.indexOf(local), 1);
      console.log(`'${branch}' holds nothing that is not on ${BASE}. Removed; a new one is cut.`);
    } catch {
      // Checked out in a worktree, most likely: refused below, with the way out.
    }
  }

  const stale = copies.filter((ref) => succeeds(repo, 'merge-base', '--is-ancestor', ref, BASE));
  if (stale.length > 0) {
    throw new Error(
      [
        `'${branch}' holds nothing that is not on ${BASE} yet could not be removed.`,
        'Starting on it would give the agent an old tree. Nothing was started.',
        '',
        'Remove it, and run this again:',
        ...(stale.includes(local) ? [`  git -C ${repo} worktree prune && git -C ${repo} branch -D ${branch}`] : []),
        ...(stale.includes(`refs/remotes/origin/${branch}`) ? [`  git -C ${repo} push origin --delete ${branch}`] : []),
      ].join('\n'),
    );
  }

  if (copies.length > 0) {
    console.log(`'${branch}' holds work that is not on ${BASE} yet. This run adds to it.`);
  }

  // Only read when the branch does not exist: a new one is cut from what is on the remote, not
  // from whatever happens to be checked out in the repository.
  return BASE;
}

/** Whether the branch's tip is on `main`'s first-parent line: cut and never added to, or fast-forwarded into. */
function onMainsLine(repo: string, ref: string): boolean {
  const tip = git(repo, 'rev-parse', ref);
  return git(repo, 'rev-list', '--first-parent', BASE).split('\n').includes(tip);
}
