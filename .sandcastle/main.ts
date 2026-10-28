import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { run, claudeCode } from '@ai-hero/sandcastle';
import { docker } from '@ai-hero/sandcastle/sandboxes/docker';
import { baseForRun } from './branch-check.ts';

// An agent works through this repository's `ready-for-agent` tickets one by one and leaves its
// commits on `sandcastle/work`. A person reviews that branch, fast-forwards `main` to it and
// pushes. Pushing `main` deploys, so the agent never pushes.
// Run this from the repository root with: pnpm --dir .sandcastle sandcastle
// (after `pnpm --dir .sandcastle install` and `pnpm --dir .sandcastle build-image` once, and the
// image again after any change to the Dockerfile)

// This folder, and the repository it belongs to. Worked out rather than written down, so the
// runner works in any clone.
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = dirname(HERE);
const BRANCH = 'sandcastle/work';

let baseBranch: string;
try {
  baseBranch = baseForRun(REPO, BRANCH);
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}

await run({
  name: 'jobs',
  cwd: REPO,

  sandbox: docker({
    imageName: 'sandcastle:jobs',
    mounts: [
      // The host's pip cache, so creating the virtualenv does not download every package again.
      { hostPath: '~/.cache/pip', sandboxPath: '/home/agent/.cache/pip' },
      // Playwright's browsers, kept so Chromium is downloaded once and not on every run. A cache
      // of the sandbox's own: installing into the host's would delete the browsers of every host
      // project the sandbox cannot see, since Playwright removes what it takes for unused.
      {
        hostPath: '~/.cache/ms-playwright-sandcastle',
        sandboxPath: '/home/agent/.cache/ms-playwright',
      },
      // The script that works out which tickets are ready, read-only and as it stands on the host,
      // whatever the branch the sandbox was cut from holds.
      { hostPath: join(HERE, 'scripts'), sandboxPath: '/home/agent/runner', readonly: true },
    ],
  }),

  agent: claudeCode('claude-opus-5-5'),

  // Shell expressions in the prompt run inside the sandbox at the start of every iteration, so
  // each iteration sees which tickets are ready now.
  promptFile: join(HERE, 'prompt.md'),

  // One ticket per iteration.
  maxIterations: 6,

  branchStrategy: { type: 'branch', branch: BRANCH, baseBranch },

  hooks: {
    sandbox: {
      onSandboxReady: [
        // AGENTS.md asks for a virtualenv built with Python 3.14 itself.
        { command: "python3.14 -m venv .venv && .venv/bin/pip install -q -e '.[dev]'" },
        // The browser the version of Playwright just installed expects. Already in the mounted
        // cache unless Playwright was upgraded, in which case it is fetched once.
        { command: '.venv/bin/playwright install --only-shell chromium' },
      ],
    },
  },
});
