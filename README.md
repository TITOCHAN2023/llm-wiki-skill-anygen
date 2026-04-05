# llm-wiki

**An OpenClaw / Codex Agent Skill for building Karpathy-style LLM knowledge bases.**

> Experimental skill — will iterate over time.
> Please send your feedbacks in github issues.

Inspired by [Andrej Karpathy's llm-wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and the community's work building on it.

## What this is

Instead of RAG (re-retrieving raw docs on every query), this pattern has the LLM **compile** raw sources into a persistent, cross-linked Markdown wiki. Every ingest, query, and lint pass makes the wiki richer. Knowledge compounds over time.

- You own: sourcing raw material, asking good questions, steering direction
- LLM owns: all writing, cross-referencing, filing, bookkeeping

## Install

```bash
# Copy the skill into your agent's skills directory
cp -r llm-wiki/ ~/.claude/skills/llm-wiki/
# or for Codex
cp -r llm-wiki/ ~/.codex/skills/llm-wiki/
```

Then reference it in your agent config, or simply paste `llm-wiki/SKILL.md` into your agent context.

## Quick start

```bash
# 1. Scaffold a new wiki
python3 llm-wiki/scripts/scaffold.py ~/my-wiki "My Research Topic"

# 2. Add a source
cp my-article.md ~/my-wiki/raw/articles/

# 3. Tell your agent: "ingest raw/articles/my-article.md"

# 4. Ask questions: "what does the wiki say about X?"

# 5. Run lint periodically
python3 llm-wiki/scripts/lint_wiki.py ~/my-wiki
```

## Skill contents

```
llm-wiki/
├── SKILL.md                      ← Main skill file (read by agent)
├── references/
│   ├── schema-guide.md           ← How to write CLAUDE.md
│   ├── article-guide.md          ← How to write good wiki articles
│   └── tooling-tips.md           ← Obsidian, qmd, Marp setup
└── scripts/
    ├── scaffold.py               ← Bootstrap new wiki directory
    └── lint_wiki.py              ← Find dead links, orphans, gaps
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
