---
name: llm-journal-digest
description: Generate and publish bilingual weekly Feishu/Lark digests of high-quality journal articles about large language models or a user-selected research topic. Use when Codex needs to set next week's digest topic, collect recent journal papers from OpenAlex/Crossref, validate that papers are real public-source records, rank them by editable journal tier and quality signals, enrich titles/abstracts with Chinese summaries, render a Feishu XML report, publish it through lark-cli, or run the weekly Monday digest automation.
---

# LLM Journal Digest

## Goal

Create a weekly bilingual Feishu report for recent high-quality journal articles. Default to large language model topics, but allow a temporary topic override such as `agriculture-kg` when the user says "next week search agriculture knowledge graph papers".

## Topic Workflow

Use topic profiles instead of asking the user to edit keyword files by hand.

- Built-in profiles live in `references/topic_profiles.tsv`.
- Default fallback keywords live in `references/search_keywords.tsv`.
- Local temporary overrides are written to `runtime/topic_override.json`, which is ignored by Git.

When the user asks to change next week's topic, run:

```powershell
& '<python.exe>' scripts/digest.py set-topic --topic agriculture-kg --weeks 1
```

For an ad-hoc topic without a built-in profile, pass comma-separated English keywords:

```powershell
& '<python.exe>' scripts/digest.py set-topic --topic "agricultural knowledge graph,knowledge graph,smart agriculture" --weeks 1
```

For one manual run without saving an override:

```powershell
& '<python.exe>' scripts/digest.py collect --topic agriculture-kg --out artifacts/candidates.json
```

If the user gives a Chinese topic, translate it into a concise English profile name or comma-separated English search keywords before calling `set-topic`.

## Weekly Workflow

1. Load `references/journal_tiers.tsv` and keep only rows where `enabled=true`.
2. Resolve keywords from `--topic`, `runtime/topic_override.json`, `references/topic_profiles.tsv`, then `references/search_keywords.tsv`.
3. Collect public-source records:

```powershell
& '<python.exe>' scripts/digest.py collect --out artifacts/candidates.json
```

4. Validate authenticity before enrichment:

```powershell
& '<python.exe>' scripts/digest.py validate --input artifacts/candidates.json --out artifacts/validated.json
```

5. Enrich each validated article with:
   - `title_zh`: concise Chinese title.
   - `abstract_zh`: faithful Chinese translation or `公开源未提供摘要`.
   - `ai_summary_zh`: 3-5 factual Chinese insights based only on available metadata and abstract.
   - `recommendation_zh`: why this paper is worth reading.
6. Save the enriched JSON as `artifacts/enriched.json`.
7. Render Feishu XML:

```powershell
& '<python.exe>' scripts/digest.py render --input artifacts/enriched.json --out artifacts/llm-journal-digest.xml
```

8. Publish a new Feishu document:

```powershell
& '<python.exe>' scripts/digest.py publish --xml artifacts/llm-journal-digest.xml
```

Use the bundled Codex Python runtime if `python` is not on PATH:

```powershell
& 'C:\Users\littl\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/digest.py collect --out artifacts/candidates.json
```

## Authenticity Rules

Never publish fixture, example, imagined, or manually invented articles in a real weekly report.

An article is publishable only if it passes `scripts/digest.py validate`:

- `source` must be `OpenAlex` or `Crossref`.
- It must have a public source id.
- It must have a valid DOI or an OpenAlex URL.
- Its URL must point to a trusted public record domain such as `doi.org`, `openalex.org`, or `crossref.org`.
- The report table must include the authenticity method.

Use `--allow-fixtures` only for visual preview tests, never for the weekly automation or real Feishu publishing.

## Ranking Rules

Use `scripts/digest.py` for deterministic filtering and ranking:

- Tier priority: `一区` > `二区` > `核心` > `其他`.
- Relevance: title and abstract matches against resolved topic keywords.
- Recency: newer publication dates rank higher inside similar quality groups.
- Citation signal: use OpenAlex cited-by count or Crossref referenced-by count when available.
- Limit: publish at most 5 articles.

If fewer than 5 articles pass authenticity and relevance checks, publish only the actual matches. If no articles match, create a short Feishu document saying no qualified public-source records were found and list checked journals and validation notes.

## Journal List

Edit `references/journal_tiers.tsv` to maintain the accepted journal set. Required columns:

```text
journal_name	issn	tier	notes	enabled	aliases
```

Use one row per journal/source. Put multiple ISSNs or aliases in a semicolon-separated list. Disable a journal with `enabled=false` instead of deleting it.

## Feishu/Lark

The publish command uses the existing local CLI path:

```text
C:\Users\littl\Documents\工作日志\tools\npm-global\node_modules\@larksuite\cli\bin\lark-cli.exe
```

For portable installs, set `LARK_CLI` to the local `lark-cli.exe` path before publishing:

```powershell
$env:LARK_CLI = 'C:\path\to\lark-cli.exe'
```

Check authorization before publishing:

```powershell
& '<lark-cli.exe>' auth status
```

If authorization is missing or expired, ask the user to complete Lark authorization and rerun `publish`. Always create a new weekly document with a date-stamped title such as `大模型高水平期刊文章周报(YYYY-MM-DD)`.

## Missing Data Policy

- Missing abstract: write `公开源未提供摘要`; summarize only title, venue, date, DOI/link, tier, and citation metadata.
- Unmatched tier: exclude by default unless there are not enough matched candidates; clearly mark `未匹配`.
- Duplicate DOI/OpenAlex ID: keep the highest-scoring record.
- Public-source API failure: keep any successful source results and mention the failed source in the final note.
