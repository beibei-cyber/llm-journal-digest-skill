# LLM Journal Digest Skill

`llm-journal-digest` is a Codex skill for generating bilingual Feishu/Lark weekly digests of high-quality journal articles about large language models or a temporary research topic such as knowledge graphs.

## Install

Copy `llm-journal-digest/` into your Codex skills directory:

```powershell
Copy-Item -Recurse .\llm-journal-digest "$env:USERPROFILE\.codex\skills\llm-journal-digest"
```

Restart or refresh Codex, then invoke:

```text
Use $llm-journal-digest to generate this week's journal digest.
```

## Configure

- Edit `llm-journal-digest/references/journal_tiers.tsv` for your accepted journal tiers.
- Edit `llm-journal-digest/references/topic_profiles.tsv` to add reusable topic profiles.
- Set a temporary topic:

```powershell
python llm-journal-digest\scripts\digest.py set-topic --topic agriculture-kg --weeks 1
```

## Feishu/Lark

Install and authenticate `lark-cli`, then set:

```powershell
$env:LARK_CLI = "C:\path\to\lark-cli.exe"
```

The skill validates article authenticity before publishing: real reports only include OpenAlex or Crossref records with public identifiers and trusted DOI/OpenAlex/Crossref links.
