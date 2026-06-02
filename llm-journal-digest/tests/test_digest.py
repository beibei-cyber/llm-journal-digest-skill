import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import digest


class DigestTests(unittest.TestCase):
    def test_load_journals_splits_issn_and_skips_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journals.tsv"
            path.write_text(
                "journal_name\tissn\ttier\tnotes\tenabled\taliases\n"
                "A\t1234-5678;8765-4321\t一区\tNote\ttrue\tAA;AAA\n"
                "B\t0000-0000\t二区\tNote\tfalse\t\n",
                encoding="utf-8",
            )
            rows = digest.load_journals(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["issns"], ["1234-5678", "8765-4321"])
        self.assertEqual(rows[0]["aliases"], ["AA", "AAA"])

    def test_topic_profile_keywords_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.tsv"
            path.write_text(
                "topic\tkeyword\tenabled\tnotes\n"
                "agriculture-kg\tagricultural knowledge graph\ttrue\tone-off topic\n"
                "agriculture-kg\tsmart agriculture\ttrue\tone-off topic\n"
                "llm\tlarge language model\tfalse\tdisabled\n",
                encoding="utf-8",
            )
            profiles = digest.load_topic_profiles(path)
        self.assertEqual(profiles["agriculture-kg"], ["agricultural knowledge graph", "smart agriculture"])
        self.assertNotIn("llm", profiles)

    def test_custom_keywords_change_relevance_filter(self):
        articles = [
            {
                "source": "OpenAlex",
                "id": "https://openalex.org/W123",
                "title": "Agricultural knowledge graph construction for crop management",
                "abstract": "A graph-based method for smart agriculture",
                "tier": "核心",
                "publication_date": "2026-06-01",
                "cited_by_count": 1,
                "url": "https://openalex.org/W123",
            }
        ]
        ranked = digest.dedupe_and_rank(
            articles, dt.date(2026, 6, 2), 5, ["agricultural knowledge graph"]
        )
        self.assertEqual(len(ranked), 1)

    def test_authenticity_rejects_fixture_article_by_default(self):
        article = {
            "source": "test-fixture",
            "id": "fixture",
            "doi": "10.0000/fake",
            "title": "Fake article",
            "url": "https://doi.org/10.0000/fake",
        }
        self.assertTrue(digest.authenticity_errors(article))

    def test_render_filters_fake_articles(self):
        payload = {
            "title": "大模型高水平期刊文章周报(2026-06-02)",
            "date": "2026-06-02",
            "range": {"start": "2026-05-27", "end": "2026-06-02"},
            "articles": [
                {
                    "source": "test-fixture",
                    "id": "fixture",
                    "doi": "10.0000/fake",
                    "title": "Large Language Models in Science",
                    "title_zh": "科学领域的大语言模型",
                    "abstract": "",
                    "journal": "A",
                    "tier": "一区",
                    "publication_date": "2026-06-01",
                    "url": "https://doi.org/10.0000/fake",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "in.json"
            output_path = Path(tmp) / "out.xml"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            args = type("Args", (), {"input": str(input_path), "out": str(output_path), "allow_fixtures": False})
            digest.render(args)
            text = output_path.read_text(encoding="utf-8")
        self.assertIn("本期没有命中符合条件的公开源记录", text)
        self.assertIn("真实性校验记录", text)

    def test_render_keeps_openalex_article(self):
        payload = {
            "title": "大模型高水平期刊文章周报(2026-06-02)",
            "date": "2026-06-02",
            "range": {"start": "2026-05-27", "end": "2026-06-02"},
            "articles": [
                {
                    "source": "OpenAlex",
                    "id": "https://openalex.org/W123",
                    "title": "Large Language Models in Science",
                    "title_zh": "科学领域的大语言模型",
                    "abstract": "",
                    "journal": "A",
                    "tier": "一区",
                    "publication_date": "2026-06-01",
                    "url": "https://openalex.org/W123",
                    "authenticity": {"checked": True, "method": "OpenAlex work returned by public API"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "in.json"
            output_path = Path(tmp) / "out.xml"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            args = type("Args", (), {"input": str(input_path), "out": str(output_path), "allow_fixtures": False})
            digest.render(args)
            text = output_path.read_text(encoding="utf-8")
        self.assertIn("公开源未提供摘要", text)
        self.assertIn("真实性校验 / Authenticity", text)


if __name__ == "__main__":
    unittest.main()
