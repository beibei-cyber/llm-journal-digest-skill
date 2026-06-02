#!/usr/bin/env python3
"""Collect, validate, render, and publish weekly journal digests."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOURNALS = ROOT / "references" / "journal_tiers.tsv"
DEFAULT_KEYWORDS = ROOT / "references" / "search_keywords.tsv"
DEFAULT_TOPIC_PROFILES = ROOT / "references" / "topic_profiles.tsv"
DEFAULT_TOPIC_OVERRIDE = ROOT / "runtime" / "topic_override.json"
DEFAULT_ARTIFACTS = ROOT / "artifacts"
DEFAULT_LARK_CLI = r"C:\Users\littl\Documents\工作日志\tools\npm-global\node_modules\@larksuite\cli\bin\lark-cli.exe"
LARK_CLI = Path(os.environ.get("LARK_CLI", DEFAULT_LARK_CLI))

ZH_TIER_1 = "\u4e00\u533a"
ZH_TIER_2 = "\u4e8c\u533a"
ZH_CORE = "\u6838\u5fc3"
ZH_OTHER = "\u5176\u4ed6"
ZH_UNMATCHED = "\u672a\u5339\u914d"
ZH_NO_ABSTRACT = "\u516c\u5f00\u6e90\u672a\u63d0\u4f9b\u6458\u8981"
ZH_DIGEST_TITLE = "\u5927\u6a21\u578b\u9ad8\u6c34\u5e73\u671f\u520a\u6587\u7ae0\u5468\u62a5"
ZH_PENDING_TRANSLATION = "\u5f85 Codex \u7ffb\u8bd1"
ZH_PENDING_SUMMARY = "\u5f85 Codex \u57fa\u4e8e\u516c\u5f00\u6e90\u5143\u6570\u636e\u548c\u6458\u8981\u751f\u6210\u603b\u7ed3"
ZH_PENDING_RECOMMENDATION = "\u5f85 Codex \u7ed9\u51fa\u63a8\u8350\u7406\u7531"

TIER_PRIORITY = {ZH_TIER_1: 400, ZH_TIER_2: 300, ZH_CORE: 200, ZH_OTHER: 100, ZH_UNMATCHED: 0}
LLM_KEYWORDS = [
    "large language model",
    "large language models",
    "llm",
    "llms",
    "foundation model",
    "foundation models",
    "generative ai",
    "chatgpt",
    "gpt",
    "transformer",
    "instruction tuning",
    "retrieval augmented generation",
    "rag",
    "prompt",
    "alignment",
    "language model",
]
TRUSTED_SOURCES = {"OpenAlex", "Crossref"}
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def parse_date(value: str | None, fallback: dt.date) -> dt.date:
    if not value:
        return fallback
    return dt.date.fromisoformat(value)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[;,]", value) if item.strip()]


def enabled_value(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def load_journals(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    journals = []
    for row in rows:
        if not enabled_value(row.get("enabled")):
            continue
        journal = {
            "journal_name": row.get("journal_name", "").strip(),
            "issns": split_list(row.get("issn")),
            "tier": row.get("tier", ZH_OTHER).strip() or ZH_OTHER,
            "notes": row.get("notes", "").strip(),
            "aliases": split_list(row.get("aliases")),
        }
        if journal["journal_name"] and journal["issns"]:
            journals.append(journal)
    return journals


def load_topic_profiles(path: Path = DEFAULT_TOPIC_PROFILES) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    profiles: dict[str, list[str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if not enabled_value(row.get("enabled")):
                continue
            topic = row.get("topic", "").strip().lower()
            keyword = row.get("keyword", "").strip().lower()
            if topic and keyword:
                profiles.setdefault(topic, []).append(keyword)
    return profiles


def override_is_active(payload: dict[str, Any], current_date: dt.date) -> bool:
    expires_on = payload.get("expires_on")
    if not expires_on:
        return True
    try:
        return current_date <= dt.date.fromisoformat(str(expires_on))
    except ValueError:
        return False


def load_topic_override(path: Path = DEFAULT_TOPIC_OVERRIDE, current_date: dt.date | None = None) -> dict[str, Any] | None:
    if current_date is None:
        current_date = today_utc()
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and override_is_active(payload, current_date):
        return payload
    return None


def load_keywords(path: Path | None = None, topic: str | None = None) -> list[str]:
    topic_name = (topic or "").strip().lower()
    if not topic_name:
        override = load_topic_override()
        topic_name = str((override or {}).get("topic", "")).strip().lower()
    if topic_name:
        profiles = load_topic_profiles()
        if topic_name in profiles:
            return profiles[topic_name]
        return [item.strip().lower() for item in split_list(topic_name)]

    if path is None:
        path = DEFAULT_KEYWORDS
    if not path.exists():
        return LLM_KEYWORDS
    keywords: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if not enabled_value(row.get("enabled")):
                continue
            keyword = row.get("keyword", "").strip().lower()
            if keyword:
                keywords.append(keyword)
    return keywords or LLM_KEYWORDS


def set_topic(args: argparse.Namespace) -> int:
    topic = args.topic.strip().lower()
    profiles = load_topic_profiles()
    keywords = profiles.get(topic) or [item.strip().lower() for item in split_list(topic)]
    expires_on = args.expires_on
    if args.weeks and not expires_on:
        expires_on = (today_utc() + dt.timedelta(days=args.weeks * 7)).isoformat()
    payload = {"topic": topic, "keywords": keywords, "expires_on": expires_on}
    out = Path(args.out)
    ensure_parent(out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote topic override to {out}")
    print(f"Topic: {topic}")
    print("Keywords: " + ", ".join(keywords))
    if expires_on:
        print(f"Expires on: {expires_on}")
    return 0


def http_json(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "llm-journal-digest/1.0 (mailto:local@example.com)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def reconstruct_openalex_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in index.items():
        pairs.extend((pos, word) for pos in positions)
    return " ".join(word for _, word in sorted(pairs))


def normalize_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value if item)
    if value is None:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def date_from_crossref(item: dict[str, Any]) -> str:
    for key in ("published-online", "published-print", "published", "issued"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            year, month, day = (list(parts[0]) + [1, 1])[:3]
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def relevance_score(title: str, abstract: str, keywords: list[str] | None = None) -> int:
    if keywords is None:
        keywords = LLM_KEYWORDS
    blob = f"{title} {abstract}".lower()
    score = 0
    for keyword in keywords:
        if keyword in blob:
            score += 3 if keyword in {"large language model", "large language models", "llm", "llms"} else 1
    return score


def recency_score(date_text: str, end_date: dt.date) -> int:
    if not date_text:
        return 0
    try:
        age = max((end_date - dt.date.fromisoformat(date_text[:10])).days, 0)
    except ValueError:
        return 0
    return max(14 - age, 0)


def article_score(article: dict[str, Any], end_date: dt.date, keywords: list[str] | None = None) -> int:
    cited = int(article.get("cited_by_count") or 0)
    return (
        TIER_PRIORITY.get(article.get("tier", ZH_UNMATCHED), 0)
        + relevance_score(article.get("title", ""), article.get("abstract", ""), keywords) * 15
        + recency_score(article.get("publication_date", ""), end_date) * 3
        + min(cited, 500) // 10
    )


def openalex_url(issn: str, start: dt.date, end: dt.date) -> str:
    params = {
        "filter": ",".join(
            [
                f"from_publication_date:{start.isoformat()}",
                f"to_publication_date:{end.isoformat()}",
                "type:article",
                f"primary_location.source.issn:{issn}",
            ]
        ),
        "per-page": "50",
        "sort": "publication_date:desc",
    }
    return "https://api.openalex.org/works?" + urllib.parse.urlencode(params)


def crossref_url(issn: str, start: dt.date, end: dt.date) -> str:
    params = {
        "filter": f"from-pub-date:{start.isoformat()},until-pub-date:{end.isoformat()},type:journal-article",
        "select": "DOI,title,abstract,container-title,URL,published-online,published-print,published,issued,is-referenced-by-count,ISSN",
        "rows": "50",
        "sort": "published",
        "order": "desc",
    }
    return f"https://api.crossref.org/journals/{urllib.parse.quote(issn)}/works?" + urllib.parse.urlencode(params)


def collect_openalex(journal: dict[str, Any], start: dt.date, end: dt.date) -> tuple[list[dict[str, Any]], list[str]]:
    articles: list[dict[str, Any]] = []
    errors: list[str] = []
    for issn in journal["issns"]:
        try:
            payload = http_json(openalex_url(issn, start, end))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"OpenAlex {issn}: {exc}")
            continue
        for item in payload.get("results", []):
            source = ((item.get("primary_location") or {}).get("source") or {})
            article = {
                "source": "OpenAlex",
                "id": item.get("id", ""),
                "doi": (item.get("doi") or "").replace("https://doi.org/", ""),
                "title": normalize_text(item.get("title")),
                "abstract": reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
                "journal": source.get("display_name") or journal["journal_name"],
                "issn": issn,
                "tier": journal["tier"],
                "journal_notes": journal["notes"],
                "publication_date": item.get("publication_date", ""),
                "cited_by_count": item.get("cited_by_count", 0),
                "url": item.get("doi") or item.get("id", ""),
                "authenticity": {
                    "checked": True,
                    "method": "OpenAlex work returned by public API",
                    "source_id": item.get("id", ""),
                },
            }
            articles.append(article)
    return articles, errors


def collect_crossref(journal: dict[str, Any], start: dt.date, end: dt.date) -> tuple[list[dict[str, Any]], list[str]]:
    articles: list[dict[str, Any]] = []
    errors: list[str] = []
    for issn in journal["issns"]:
        try:
            payload = http_json(crossref_url(issn, start, end))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"Crossref {issn}: {exc}")
            continue
        for item in payload.get("message", {}).get("items", []):
            article = {
                "source": "Crossref",
                "id": item.get("DOI", ""),
                "doi": item.get("DOI", ""),
                "title": normalize_text(item.get("title")),
                "abstract": normalize_text(item.get("abstract")),
                "journal": normalize_text(item.get("container-title")) or journal["journal_name"],
                "issn": issn,
                "tier": journal["tier"],
                "journal_notes": journal["notes"],
                "publication_date": date_from_crossref(item),
                "cited_by_count": item.get("is-referenced-by-count", 0),
                "url": item.get("URL", ""),
                "authenticity": {
                    "checked": True,
                    "method": "Crossref work returned by public API",
                    "source_id": item.get("DOI", ""),
                },
            }
            articles.append(article)
    return articles, errors


def authenticity_errors(article: dict[str, Any], allow_fixtures: bool = False) -> list[str]:
    errors: list[str] = []
    source = str(article.get("source", ""))
    doi = str(article.get("doi", "")).strip()
    url = str(article.get("url", "")).strip()
    source_id = str(article.get("id", "")).strip()

    if allow_fixtures and source in {"fixture", "test-fixture"}:
        return []
    if source not in TRUSTED_SOURCES:
        errors.append(f"untrusted source: {source or '<missing>'}")
    if not source_id:
        errors.append("missing public source id")
    if doi and not DOI_RE.match(doi):
        errors.append(f"invalid DOI format: {doi}")
    if not doi and "openalex.org/" not in url.lower():
        errors.append("missing DOI or OpenAlex URL")
    if url and not any(token in url.lower() for token in ("doi.org/", "openalex.org/", "crossref.org/")):
        errors.append(f"untrusted article URL: {url}")
    return errors


def filter_authentic_articles(payload: dict[str, Any], allow_fixtures: bool = False) -> dict[str, Any]:
    valid_articles = []
    validation_errors = list(payload.get("validation_errors") or [])
    for article in payload.get("articles", []):
        errors = authenticity_errors(article, allow_fixtures=allow_fixtures)
        if errors:
            validation_errors.append({"title": article.get("title", ""), "errors": errors})
            continue
        authenticity = dict(article.get("authenticity") or {})
        authenticity["checked"] = True
        authenticity.setdefault("method", f"{article.get('source')} public-source validation")
        article["authenticity"] = authenticity
        valid_articles.append(article)
    payload = dict(payload)
    payload["articles"] = valid_articles
    payload["validation_errors"] = validation_errors
    return payload


def dedupe_and_rank(
    articles: list[dict[str, Any]], end_date: dt.date, limit: int, keywords: list[str] | None = None
) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for article in articles:
        if not article.get("title"):
            continue
        if relevance_score(article.get("title", ""), article.get("abstract", ""), keywords) <= 0:
            continue
        if authenticity_errors(article):
            continue
        key = (article.get("doi") or article.get("id") or article.get("title", "")).lower()
        article["score"] = article_score(article, end_date, keywords)
        if key not in best or article["score"] > best[key]["score"]:
            best[key] = article
    return sorted(best.values(), key=lambda item: item["score"], reverse=True)[:limit]


def collect(args: argparse.Namespace) -> int:
    end = parse_date(args.end_date, today_utc())
    start = parse_date(args.start_date, end - dt.timedelta(days=args.days - 1))
    journals = load_journals(Path(args.journals))
    keywords = load_keywords(Path(args.keywords), args.topic)
    all_articles: list[dict[str, Any]] = []
    errors: list[str] = []
    for journal in journals:
        articles, source_errors = collect_openalex(journal, start, end)
        all_articles.extend(articles)
        errors.extend(source_errors)
        if args.include_crossref:
            articles, source_errors = collect_crossref(journal, start, end)
            all_articles.extend(articles)
            errors.extend(source_errors)
    selected = dedupe_and_rank(all_articles, end, args.limit, keywords)
    payload = {
        "title": f"{ZH_DIGEST_TITLE}({end.isoformat()})",
        "date": end.isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "limit": args.limit,
        "topic": args.topic or (load_topic_override() or {}).get("topic") or "default",
        "keywords": keywords,
        "active_journals": journals,
        "errors": errors,
        "articles": selected,
    }
    out = Path(args.out)
    ensure_parent(out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(selected)} selected articles to {out}")
    if errors:
        print(f"Completed with {len(errors)} source warnings", file=sys.stderr)
    return 0


def validate(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    payload = filter_authentic_articles(payload, allow_fixtures=args.allow_fixtures)
    out = Path(args.out or args.input)
    ensure_parent(out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rejected = len(payload.get("validation_errors") or [])
    print(f"Validated {len(payload.get('articles', []))} articles; rejected {rejected}")
    return 0 if payload.get("articles") or args.allow_empty else 3


def xml_text(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def paragraph(text: str) -> str:
    return f"<p>{xml_text(text)}</p>"


def render_article(article: dict[str, Any], index: int) -> str:
    abstract = article.get("abstract") or ZH_NO_ABSTRACT
    abstract_zh = article.get("abstract_zh") or (ZH_NO_ABSTRACT if not article.get("abstract") else ZH_PENDING_TRANSLATION)
    title_zh = article.get("title_zh") or ZH_PENDING_TRANSLATION
    summary = article.get("ai_summary_zh") or ZH_PENDING_SUMMARY
    recommendation = article.get("recommendation_zh") or ZH_PENDING_RECOMMENDATION
    url = article.get("url") or (f"https://doi.org/{article.get('doi')}" if article.get("doi") else "")
    auth = article.get("authenticity") or {}
    rows = [
        ("\u671f\u520a / Journal", article.get("journal", "")),
        ("\u7b49\u7ea7 / Tier", article.get("tier", ZH_UNMATCHED)),
        ("\u65e5\u671f / Date", article.get("publication_date", "")),
        ("\u5f15\u7528 / Citations", article.get("cited_by_count", 0)),
        ("\u6765\u6e90 / Source", article.get("source", "")),
        ("\u771f\u5b9e\u6027\u6821\u9a8c / Authenticity", auth.get("method", "")),
        ("\u94fe\u63a5 / Link", url),
    ]
    table_rows = "".join(
        f"<tr><td>{xml_text(label)}</td><td>{xml_text(value)}</td></tr>" for label, value in rows
    )
    return "\n".join(
        [
            f"<h2>{index}. {xml_text(title_zh)}</h2>",
            f"<h3>{xml_text(article.get('title', ''))}</h3>",
            f"<table>{table_rows}</table>",
            "<h3>\u4e2d\u6587\u6458\u8981</h3>",
            paragraph(abstract_zh),
            "<h3>Original Abstract</h3>",
            paragraph(abstract),
            "<h3>AI \u603b\u7ed3</h3>",
            paragraph(summary),
            "<h3>\u63a8\u8350\u7406\u7531</h3>",
            paragraph(recommendation),
            "<hr/>",
        ]
    )


def render(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    payload = filter_authentic_articles(payload, allow_fixtures=args.allow_fixtures)
    articles = payload.get("articles", [])
    title = payload.get("title") or f"{ZH_DIGEST_TITLE}({payload.get('date', '')})"
    if articles:
        body = "\n".join(render_article(article, idx + 1) for idx, article in enumerate(articles))
    else:
        journals = ", ".join(journal["journal_name"] for journal in payload.get("active_journals", []))
        body = "\n".join(
            [
                "<h2>\u672c\u671f\u6ca1\u6709\u547d\u4e2d\u7b26\u5408\u6761\u4ef6\u7684\u516c\u5f00\u6e90\u8bb0\u5f55</h2>",
                paragraph(
                    f"\u68c0\u7d22\u533a\u95f4: {payload.get('range', {}).get('start', '')} "
                    f"\u81f3 {payload.get('range', {}).get('end', '')}"
                ),
                paragraph(f"\u5df2\u68c0\u67e5\u671f\u520a: {journals}"),
            ]
        )
    warnings = payload.get("errors") or []
    validation_errors = payload.get("validation_errors") or []
    warning_block = ""
    if warnings:
        warning_block += "<h2>\u516c\u5f00\u6e90\u8b66\u544a</h2>" + "".join(paragraph(item) for item in warnings[:10])
    if validation_errors:
        warning_block += "<h2>\u771f\u5b9e\u6027\u6821\u9a8c\u8bb0\u5f55</h2>" + "".join(
            paragraph(f"{item.get('title', '')}: {'; '.join(item.get('errors', []))}")
            for item in validation_errors[:10]
        )
    xml = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<doc>",
            f"<title>{xml_text(title)}</title>",
            "<callout color=\"light-blue\">",
            paragraph(
                f"\u68c0\u7d22\u533a\u95f4: {payload.get('range', {}).get('start', '')} "
                f"\u81f3 {payload.get('range', {}).get('end', '')}; "
                f"\u672c\u671f\u6536\u5f55 {len(articles)} \u7bc7\u3002"
            ),
            "</callout>",
            body,
            warning_block,
            "</doc>",
        ]
    )
    out = Path(args.out)
    ensure_parent(out)
    out.write_text(xml, encoding="utf-8")
    print(f"Wrote Feishu XML to {out}")
    return 0


def publish(args: argparse.Namespace) -> int:
    if not LARK_CLI.exists():
        print(f"lark-cli not found: {LARK_CLI}", file=sys.stderr)
        return 2
    xml_path = Path(args.xml).resolve()
    if not xml_path.exists():
        print(f"XML not found: {xml_path}", file=sys.stderr)
        return 2
    command = [
        str(LARK_CLI),
        "docs",
        "+create",
        "--api-version",
        "v2",
        "--as",
        "user",
        "--content",
        f"@{xml_path.name}",
    ]
    result = subprocess.run(
        command,
        cwd=str(xml_path.parent),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    return result.returncode


def run(args: argparse.Namespace) -> int:
    DEFAULT_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    candidates = DEFAULT_ARTIFACTS / "candidates.json"
    xml_path = DEFAULT_ARTIFACTS / "llm-journal-digest.xml"
    collect_args = argparse.Namespace(
        journals=args.journals,
        keywords=args.keywords,
        topic=args.topic,
        out=str(candidates),
        start_date=args.start_date,
        end_date=args.end_date,
        days=args.days,
        limit=args.limit,
        include_crossref=args.include_crossref,
    )
    code = collect(collect_args)
    if code != 0:
        return code
    render_args = argparse.Namespace(input=str(candidates), out=str(xml_path), allow_fixtures=False)
    code = render(render_args)
    if code != 0 or args.no_publish:
        return code
    return publish(argparse.Namespace(xml=str(xml_path)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    set_topic_parser = sub.add_parser("set-topic", help="Set a local temporary topic override")
    set_topic_parser.add_argument("--topic", required=True, help="Topic profile name or comma-separated keywords")
    set_topic_parser.add_argument("--weeks", type=int, default=1)
    set_topic_parser.add_argument("--expires-on")
    set_topic_parser.add_argument("--out", default=str(DEFAULT_TOPIC_OVERRIDE))
    set_topic_parser.set_defaults(func=set_topic)

    collect_parser = sub.add_parser("collect", help="Collect and rank public-source candidates")
    collect_parser.add_argument("--journals", default=str(DEFAULT_JOURNALS))
    collect_parser.add_argument("--keywords", default=str(DEFAULT_KEYWORDS))
    collect_parser.add_argument("--topic", help="Topic profile name or comma-separated keywords")
    collect_parser.add_argument("--out", default=str(DEFAULT_ARTIFACTS / "candidates.json"))
    collect_parser.add_argument("--start-date")
    collect_parser.add_argument("--end-date")
    collect_parser.add_argument("--days", type=int, default=7)
    collect_parser.add_argument("--limit", type=int, default=5)
    collect_parser.add_argument("--include-crossref", action="store_true")
    collect_parser.set_defaults(func=collect)

    validate_parser = sub.add_parser("validate", help="Validate article authenticity in a JSON payload")
    validate_parser.add_argument("--input", required=True)
    validate_parser.add_argument("--out")
    validate_parser.add_argument("--allow-fixtures", action="store_true")
    validate_parser.add_argument("--allow-empty", action="store_true")
    validate_parser.set_defaults(func=validate)

    render_parser = sub.add_parser("render", help="Render enriched JSON to Feishu XML")
    render_parser.add_argument("--input", required=True)
    render_parser.add_argument("--out", default=str(DEFAULT_ARTIFACTS / "llm-journal-digest.xml"))
    render_parser.add_argument("--allow-fixtures", action="store_true")
    render_parser.set_defaults(func=render)

    publish_parser = sub.add_parser("publish", help="Publish Feishu XML with lark-cli")
    publish_parser.add_argument("--xml", required=True)
    publish_parser.set_defaults(func=publish)

    run_parser = sub.add_parser("run", help="Collect, render, and optionally publish")
    run_parser.add_argument("--journals", default=str(DEFAULT_JOURNALS))
    run_parser.add_argument("--keywords", default=str(DEFAULT_KEYWORDS))
    run_parser.add_argument("--topic", help="Topic profile name or comma-separated keywords")
    run_parser.add_argument("--start-date")
    run_parser.add_argument("--end-date")
    run_parser.add_argument("--days", type=int, default=7)
    run_parser.add_argument("--limit", type=int, default=5)
    run_parser.add_argument("--include-crossref", action="store_true")
    run_parser.add_argument("--no-publish", action="store_true")
    run_parser.set_defaults(func=run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
