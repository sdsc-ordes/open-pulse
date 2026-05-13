#!/usr/bin/env node
// Generate docs/data/nodes.json from nodes/*.yaml.
// Zero deps — inline YAML parser handles the subset documented in
// nodes/README.md (top-level key/value, # comments, `|` block strings).
// CI runs this and diffs the result against the committed JSON;
// contributors should re-run after editing a node YAML.

import { readdir, readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, basename } from "node:path";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const nodesDir = join(repoRoot, "nodes");
const outFile = join(repoRoot, "docs/data/nodes.json");

function parseSimpleYaml(text) {
  const lines = text.split("\n");
  const obj = {};
  let i = 0;
  while (i < lines.length) {
    const raw = lines[i];
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("#")) { i++; continue; }

    const m = raw.match(/^([A-Za-z_][\w-]*)\s*:\s*(.*)$/);
    if (!m) { i++; continue; }

    const key = m[1];
    let val = m[2].trim();

    if (val === "|" || val === ">") {
      const fold = val === ">";
      const blockLines = [];
      i++;
      while (i < lines.length) {
        const l = lines[i];
        if (!l.trim()) { blockLines.push(""); i++; continue; }
        if (!l.startsWith("  ")) break;
        blockLines.push(l.slice(2));
        i++;
      }
      while (blockLines.length && blockLines[blockLines.length - 1] === "") {
        blockLines.pop();
      }
      val = blockLines.join(fold ? " " : "\n").trim();
    } else {
      // Strip inline # comment + matching surrounding quotes.
      val = val.replace(/\s+#.*$/, "");
      val = val.replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1");
      i++;
    }
    obj[key] = val;
  }
  return obj;
}

function validate(node, file) {
  const required = ["name", "institution", "location", "url", "status", "description"];
  const missing = required.filter((k) => !(k in node) || !String(node[k]).trim());
  if (missing.length) {
    throw new Error(`${file}: missing required field(s): ${missing.join(", ")}`);
  }
  if (!/^https:\/\//.test(node.url)) {
    throw new Error(`${file}: url must start with https://`);
  }
  const validStatus = ["live", "beta", "coming-soon"];
  if (!validStatus.includes(node.status)) {
    throw new Error(`${file}: status must be one of ${validStatus.join(", ")}`);
  }
}

const files = (await readdir(nodesDir))
  .filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"))
  .sort();

const nodes = [];
for (const f of files) {
  const text = await readFile(join(nodesDir, f), "utf8");
  const parsed = parseSimpleYaml(text);
  parsed._source = basename(f);
  validate(parsed, f);
  nodes.push(parsed);
}

await mkdir(dirname(outFile), { recursive: true });
await writeFile(outFile, JSON.stringify(nodes, null, 2) + "\n");
console.log(`Wrote ${nodes.length} node${nodes.length === 1 ? "" : "s"} → ${outFile}`);
