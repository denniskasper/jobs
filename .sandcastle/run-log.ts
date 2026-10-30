// The runner's own output, copied as it is printed to `.sandcastle/logs/run.log` in this
// repository, so a run started in the background is followed with
// `tail -F .sandcastle/logs/run.log`. Never send a run's output to a shared path such as
// `/tmp/sandcastle-run.log`: the runners of other repositories on the same machine pick the same
// name, and each truncates and overwrites the others'. Imported first, so nothing is printed before
// it. Output of a child process that inherits the terminal reaches the terminal only.
import { mkdirSync, openSync, writeSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const logs = join(dirname(fileURLToPath(import.meta.url)), 'logs');
mkdirSync(logs, { recursive: true });
// Written synchronously, so a line printed just before `process.exit` is not lost.
const file = openSync(join(logs, 'run.log'), 'a');
writeSync(
  file,
  `\n=== ${new Date().toISOString()} pid ${process.pid} ${process.argv.slice(2).join(' ')}\n`,
);

for (const stream of [process.stdout, process.stderr]) {
  const write = stream.write.bind(stream) as (...args: unknown[]) => boolean;
  stream.write = ((chunk: string | Uint8Array, ...rest: unknown[]) => {
    writeSync(file, chunk);
    return write(chunk, ...rest);
  }) as typeof stream.write;
}
