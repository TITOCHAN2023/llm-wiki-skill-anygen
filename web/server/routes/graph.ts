import fs from "node:fs";
import path from "node:path";
import type { Request, Response } from "express";
import type { ServerConfig } from "../config.js";

export interface GraphNode {
  id: string; // path relative to wikiRoot, e.g. "wiki/concepts/Transformers.md"
  label: string; // display name (stem, e.g. "Transformers")
  path: string; // same as id, kept explicit for client
  group: string; // concepts | entities | summaries | other
  degree: number; // in + out link count, used for node sizing
  title: string | null;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// Matches [text](href) and ![alt](src). href/src may be wrapped in <...>
// (which is how paths with spaces travel in strict CommonMark). Only hrefs
// whose path ends in `.md` are considered wiki page links.
const MD_LINK_RE =
  /!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+?\.md(?:#[^>\n]*)?)>|([^()\s]+?\.md(?:#[^()\s]*)?))\s*\)/g;
const EXTERNAL_URL_RE = /^[a-z][a-z0-9+.\-]*:/i;

export function buildGraph(wikiRoot: string): GraphData {
  const wikiDir = path.join(wikiRoot, "wiki");
  if (!fs.existsSync(wikiDir)) return { nodes: [], edges: [] };

  const files = collectMdFiles(wikiDir);

  // Nodes keyed by id (relative-to-wikiRoot POSIX path).
  const nodes: Map<string, GraphNode> = new Map();
  for (const f of files) {
    const relFromWiki = path.relative(wikiDir, f).split(path.sep).join("/");
    const id = `wiki/${relFromWiki}`;
    const stem = path.basename(f, ".md");
    const parts = relFromWiki.split("/");
    const group = parts.length > 1 ? parts[0]! : "other";
    const title = extractTitle(fs.readFileSync(f, "utf-8")) ?? stem;
    nodes.set(id, { id, label: stem, path: id, group, degree: 0, title });
  }

  // Build edges by resolving each MD link from the wiki/ root.
  const edges: GraphEdge[] = [];
  const seenEdges = new Set<string>();
  for (const f of files) {
    const srcId =
      `wiki/${path.relative(wikiDir, f).split(path.sep).join("/")}`;
    const text = fs.readFileSync(f, "utf-8");
    MD_LINK_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = MD_LINK_RE.exec(text))) {
      const href = (m[1] ?? m[2] ?? "").trim();
      const [pathPart] = splitAnchor(href);
      if (!pathPart || EXTERNAL_URL_RE.test(pathPart)) continue;
      const normalized = path.posix.normalize(pathPart);
      if (
        normalized === ".." ||
        normalized.startsWith("../") ||
        path.posix.isAbsolute(normalized)
      ) {
        continue;
      }

      const resolvedAbs = path.resolve(wikiDir, normalized);
      const relFromRoot = path
        .relative(wikiRoot, resolvedAbs)
        .split(path.sep)
        .join("/");
      if (relFromRoot.startsWith("..") || path.isAbsolute(relFromRoot)) {
        continue; // target escapes wiki-root (e.g. raw/refs/foo.md)
      }
      const tgtId = relFromRoot;
      if (!nodes.has(tgtId) || tgtId === srcId) continue;

      const key = `${srcId}→${tgtId}`;
      if (seenEdges.has(key)) continue;
      seenEdges.add(key);
      edges.push({ source: srcId, target: tgtId });

      nodes.get(srcId)!.degree += 1;
      nodes.get(tgtId)!.degree += 1;
    }
  }

  return {
    nodes: Array.from(nodes.values()),
    edges,
  };
}

function splitAnchor(h: string): [string, string] {
  const i = h.indexOf("#");
  if (i < 0) return [h, ""];
  return [h.slice(0, i), h.slice(i + 1)];
}

function collectMdFiles(dir: string): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith(".")) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...collectMdFiles(full));
    else if (e.isFile() && e.name.endsWith(".md")) out.push(full);
  }
  return out;
}

function extractTitle(text: string): string | null {
  const fm = /^---\n([\s\S]*?)\n---/.exec(text);
  if (fm) {
    const t = /^title:\s*(.+)$/m.exec(fm[1]!);
    if (t) return t[1]!.trim().replace(/^["']|["']$/g, "");
  }
  const h1 = /^#\s+(.+?)\s*$/m.exec(text);
  return h1 ? h1[1]! : null;
}

export function handleGraph(cfg: ServerConfig) {
  return (_req: Request, res: Response) => {
    res.json(buildGraph(cfg.wikiRoot));
  };
}
