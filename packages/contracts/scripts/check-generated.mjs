import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { generate } from "./generate.mjs";

const packageRoot = resolve(import.meta.dirname, "..");
const committedPath = resolve(packageRoot, "src/index.ts");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "relay-contracts-"));
const generatedPath = join(temporaryDirectory, "index.ts");

try {
  await generate(generatedPath);
  const [committed, generated] = await Promise.all([
    readFile(committedPath, "utf8"),
    readFile(generatedPath, "utf8"),
  ]);
  if (committed !== generated) {
    throw new Error("packages/contracts/src/index.ts is stale; run pnpm --filter @relay/contracts generate");
  }
} finally {
  await rm(temporaryDirectory, { force: true, recursive: true });
}
