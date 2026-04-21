# llm-wiki-anygen

**A Claw Agent Skill for building Karpathy-style LLM knowledge bases.**

> Experimental skill — will iterate over time.
> Please send your feedbacks in github issues.

Inspired by [Andrej Karpathy's llm-wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and the community's work building on it.

## What this is

Instead of RAG (re-retrieving raw docs on every query), this pattern has the LLM **compile** raw sources into a persistent, cross-linked Markdown wiki. Every `compile`, `ingest`, `query`, `lint`, and `audit` pass makes the wiki richer. Knowledge compounds over time.

- You own: sourcing raw material, asking good questions, steering direction, filing feedback on things the AI got wrong.
- LLM owns: all writing, cross-referencing, filing, bookkeeping, and acting on your feedback.

The skill comes with two companion tools in this repo:

- **`plugins/obsidian-audit/`** — an Obsidian plugin: select text in any page, leave a comment with severity, the comment is written into `audit/` as an anchored markdown file.
- **`web/`** — a local Node.js preview server: renders the wiki with mermaid, KaTeX, and resolved internal MD links, lets you select + file feedback from the browser, and shows open audits per page.

Both tools share a single TypeScript library (`audit-shared/`) so audit files written from Obsidian and the web viewer are byte-identical in shape.

## Install

```bash
# Copy the skill into your agent's skills directory
cp -r llm-wiki-anygen/ <your skill directory>
```

Then reference it in your agent config, or simply paste `llm-wiki-anygen/SKILL.md` into your agent context.

## Quick start

```bash
# 1. Scaffold a new wiki
python3 llm-wiki-anygen/scripts/scaffold.py <your knowledge path>/my-wiki "My Research Topic"

# 2. Add a source
cp my-article.md <your knowledge path>/my-wiki/raw/articles/

# 3. Tell your agent: "ingest raw/articles/my-article.md"

# 4. Ask questions: "what does the wiki say about X?"

# 5. Run lint periodically
python3 llm-wiki-anygen/scripts/lint_wiki.py <your knowledge path>/my-wiki

# 6. File a comment from the web viewer or Obsidian plugin, then process it
python3 llm-wiki-anygen/scripts/audit_review.py <your knowledge path>/my-wiki --open
# then tell the agent: "audit: process the open comments"
```

## Repo contents

```
llm-wiki-skill/
├── llm-wiki-anygen/             ← The skill
│   ├── SKILL.md                 ← Main skill file (read by agent)
│   ├── references/
│   │   ├── schema-guide.md      ← ANYGEN.md schema template
│   │   ├── article-guide.md     ← Article writing (divide & conquer, mermaid, KaTeX)
│   │   ├── log-guide.md         ← log/ folder convention
│   │   ├── audit-guide.md       ← audit file format + processing workflow
│   │   └── tooling-tips.md      ← Obsidian, qmd, plugin + web
│   └── scripts/
│       ├── scaffold.py          ← Bootstrap new wiki directory
│       ├── lint_wiki.py         ← 7-pass health check (links, audit, log shape)
│       └── audit_review.py      ← Group open/resolved audits by target
├── audit-shared/                ← Shared TypeScript library
│   └── src/{schema,anchor,id,serialize,index}.ts
├── plugins/obsidian-audit/      ← Obsidian plugin — file audit from vault
└── web/                         ← Local Node.js preview + feedback server
    ├── server/                  ← Express + markdown-it + KaTeX + internal MD link resolver
    └── client/                  ← Vanilla-TS SPA with mermaid + selection popover
```

## Running the web viewer

```bash
# one-time setup (builds audit-shared, installs deps, bundles client)
cd audit-shared && npm install && npm run build && cd ..
cd web && npm install && npm run build && cd ..

# start the server against a wiki
cd web
npm start -- --wiki "/path/to/your/wiki-root" --port 4175
# open http://127.0.0.1:4175
```

## Building the Obsidian plugin

```bash
cd audit-shared && npm install && npm run build && cd ..
cd plugins/obsidian-audit
npm install
npm run build
npm run link -- "/path/to/your/Obsidian vault"
# Enable 'LLM Wiki Audit' in Obsidian → Settings → Community plugins.
```

## Use cases

- **Research deep-dive** — reading papers/articles on a topic over weeks
- **Personal wiki** — Farzapedia-style: journal entries compiled into personal encyclopedia  
- **Team knowledge base** — fed by Slack threads, meeting notes, docs
- **Reading companion** — building a rich companion wiki as you read a book

## Related work

- [Karpathy's original Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [pedronauck/skills karpathy-kb](https://github.com/pedronauck/skills/tree/main/skills/karpathy-kb) — full Obsidian vault integration
- [Astro-Han/karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki) — example implementation
- [qmd](https://github.com/tobi/qmd) — semantic search for Markdown wikis

## License

MIT
