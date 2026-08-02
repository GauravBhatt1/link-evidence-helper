import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { zodSchemas } from "./schemas.js";

type ManifestEntry = { file: string; schema: keyof typeof zodSchemas; valid: boolean; provenance: string };
const here = dirname(fileURLToPath(import.meta.url));
const contractsRoot = resolve(here, "..");
const workspaceRoot = resolve(contractsRoot, "../..");
const schemaDir = join(contractsRoot, "schema");
const fixtureDir = join(workspaceRoot, "packages/testing/fixtures");
const manifest = JSON.parse(readFileSync(join(fixtureDir, "manifest.json"), "utf8")) as ManifestEntry[];

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
for (const name of readdirSync(schemaDir).filter((name) => name.endsWith(".json"))) {
  ajv.addSchema(JSON.parse(readFileSync(join(schemaDir, name), "utf8")));
}

const forbidden = /(?:cookie|authorization|workflowMetadata|selector|signedQuery|apiKey|password|token)/i;
const normalized = (value: unknown) => JSON.parse(JSON.stringify(value));

describe("canonical contract parity", () => {
  for (const entry of manifest) {
    it(`${entry.valid ? "accepts" : "rejects"} ${entry.file}`, () => {
      const value = JSON.parse(readFileSync(join(fixtureDir, entry.file), "utf8"));
      const schemaId = `https://schemas.jobinfo.local/v1/${entry.schema}`;
      const canonicalAccepted = Boolean(ajv.validate(schemaId, value));
      const zodResult = zodSchemas[entry.schema].safeParse(value);
      expect(canonicalAccepted).toBe(entry.valid);
      expect(zodResult.success).toBe(entry.valid);
      if (zodResult.success) expect(normalized(zodResult.data)).toEqual(normalized(value));
    });
  }

  it("contains no secret or source-internal public fields", () => {
    for (const name of readdirSync(schemaDir).filter((name) => name.endsWith(".json"))) {
      expect(readFileSync(join(schemaDir, name), "utf8")).not.toMatch(forbidden);
    }
    for (const entry of manifest.filter((entry) => entry.valid)) {
      expect(readFileSync(join(fixtureDir, entry.file), "utf8")).not.toMatch(forbidden);
    }
  });

  it("keeps Zod top-level fields aligned with canonical schemas", () => {
    for (const [name, zodSchema] of Object.entries(zodSchemas)) {
      const canonical = JSON.parse(readFileSync(join(schemaDir, name), "utf8")) as {properties: Record<string, unknown>};
      const zodFields = Object.keys((zodSchema as unknown as {shape: Record<string, unknown>}).shape).sort();
      expect(zodFields).toEqual(Object.keys(canonical.properties).sort());
    }
  });
});
