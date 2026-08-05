import { BrowserWorkerError, parseBrowserSearchTask } from "./contracts.js";
import { BrowserSearchExecutor } from "./executor.js";

const MAX_INPUT_BYTES = 64 * 1024;

async function main(): Promise<void> {
  const input = await readStandardInput();
  const task = parseBrowserSearchTask(JSON.parse(input) as unknown);
  const executor = new BrowserSearchExecutor({
    allowPrivate: process.env.LINK_EVIDENCE_BROWSER_ALLOW_PRIVATE === "true",
  });
  const output = await executor.execute(task);
  process.stdout.write(`${JSON.stringify(output)}\n`);
}

async function readStandardInput(): Promise<string> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of process.stdin) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buffer.length;
    if (total > MAX_INPUT_BYTES) {
      throw new BrowserWorkerError("invalid_task", "Browser task input exceeded the safe size limit.");
    }
    chunks.push(buffer);
  }
  const input = Buffer.concat(chunks).toString("utf8").trim();
  if (!input) {
    throw new BrowserWorkerError("invalid_task", "Browser task input is required.");
  }
  return input;
}

main().catch((error: unknown) => {
  const failure = error instanceof BrowserWorkerError
    ? { ok: false, code: error.code, message: error.message }
    : { ok: false, code: "browser_failed", message: "The browser worker could not complete the task." };
  process.stdout.write(`${JSON.stringify(failure)}\n`);
  process.exitCode = 1;
});
