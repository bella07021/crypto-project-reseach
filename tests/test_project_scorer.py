import unittest
import csv
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from project_scorer import (
    calculate_funding_score,
    calculate_social_percentile,
    calculate_team_score,
    calculate_total_score,
)
from live_project_fetcher import normalize_rootdata_url, parse_rootdata_detail_html
from score_project import make_funding_round_rows, make_roadmap_event_rows, make_score_rows


class ProjectScorerTests(unittest.TestCase):
    def test_normalize_rootdata_url_matches_case_and_host_variants(self):
        left = "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D"
        right = "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D"
        self.assertEqual(normalize_rootdata_url(left), normalize_rootdata_url(right))

    def test_parse_rootdata_detail_html_extracts_project_basics(self):
        html = """
        <h1>Nexus</h1>
        <p>Massively-parallelized proof mining network</p>
        <a href="https://www.nexus.xyz/">nexus.xyz</a>
        <a href="https://x.com/nexuslabs">X</a>
        <span>Tags</span><span>Infra</span><span>zk</span>
        <span>Founded</span><span>2022</span>
        <span>Location</span><span>United States</span>
        <script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":2200000,\\"facDate\\":\\"2022-12-01 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Seed\\",\\"cn_value\\":\\"种子轮\\"},\\"desc\\":{\\"en_value\\":\\"Nexus raised $ 2.2 M in Seed round\\"}},{\\"facAmountUs\\":25000000,\\"facDate\\":\\"2024-06-10 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Series A\\",\\"cn_value\\":\\"A轮\\"},\\"desc\\":{\\"en_value\\":\\"Nexus raised $ 25 M in Series A round\\"}}]"])</script>
        <script>self.__next_f.push([1,"\\"hapDate\\":\\"2026-05-20 00:00:00\\",\\"oName\\":{\\"en_value\\":\\"Coinbase listed Nexus（NEX）\\",\\"cn_value\\":\\"Coinbase 上线 Nexus（NEX）\\"},\\"siteUrl\\":\\"https://x.com/CoinbaseMarkets/status/2057085756120748167\\",\\"type\\":15"])</script>
        <script>self.__next_f.push([1,"\\"hapDate\\":\\"2026-05-20 00:00:00\\",\\"oName\\":{\\"en_value\\":\\"NEX is live for trading\\",\\"cn_value\\":\\"NEX 代币正式上线\\"},\\"siteUrl\\":\\"https://x.com/nexuslabs/status/2057000000000000000\\",\\"type\\":1"])</script>
        <div>Jun 10, 2024</div><span>Nexus raised $ 25 M in Series A round</span>
        <script>self.__next_f.push([1,"team\\":[{\\"name\\":{\\"en_value\\":\\"Daniel Marin\\"},\\"lyingUrl\\":\\"https://linkedin.com/in/x\\",\\"twitterUrl\\":\\"https://x.com/danielmarinq\\"},{\\"name\\":{\\"en_value\\":\\"Alex Fowler\\"},\\"lyingUrl\\":\\"https://linkedin.com/in/y\\",\\"twitterUrl\\":\\"https://x.com/alexanderfowler\\"}]"])</script>
        """
        detail = parse_rootdata_detail_html(html)
        self.assertEqual(detail.project_name, "Nexus")
        self.assertEqual(detail.token_ticker, "NEX")
        self.assertEqual(detail.website, "https://www.nexus.xyz/")
        self.assertEqual(detail.x_handle, "nexuslabs")
        self.assertEqual(detail.bucket, "infra")
        self.assertEqual(detail.location, "United States")
        self.assertEqual(detail.latest_funding_amount_usd, 25_000_000)
        self.assertEqual(str(detail.latest_funding_date), "2024-06-10")
        self.assertEqual(len(detail.funding_rounds), 2)
        self.assertEqual(detail.funding_rounds[0]["round"], "Seed")
        self.assertEqual(detail.funding_rounds[0]["amount_usd"], 2_200_000)
        self.assertEqual(detail.funding_rounds[1]["round"], "Series A")
        self.assertEqual(detail.funding_rounds[1]["amount_usd"], 25_000_000)
        self.assertEqual(detail.funding_total_usd, 27_200_000)
        self.assertEqual(detail.tge_status, "已 TGE")
        self.assertEqual(detail.tge_probability, 100)
        self.assertEqual(str(detail.tge_date), "2026-05-20")
        self.assertEqual(detail.tge_method, "Binance Alpha")
        self.assertGreaterEqual(len(detail.roadmap_events), 1)
        self.assertEqual(detail.roadmap_events[0]["name"], "Coinbase listed Nexus（NEX）")
        self.assertEqual(detail.roadmap_events[0]["days_after_tge"], 0)
        self.assertEqual(detail.team_raw_score, 70)
        self.assertEqual(detail.team_background, "international")

    def test_score_sheet_keeps_latest_row_per_project(self):
        rows = make_score_rows(
            [
                {
                    "assessed_at": "2026-05-22T01:00:00Z",
                    "x_handle": "NexusLabs",
                    "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                    "total_score": 0,
                },
                {
                    "assessed_at": "2026-05-22T02:00:00Z",
                    "x_handle": "NexusLabs",
                    "rootdata_url": "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D",
                    "total_score": 48.02,
                },
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "2026-05-22T02:00:00Z")
        self.assertEqual(rows[1][rows[0].index("total_score")], 48.02)

    def test_funding_round_sheet_expands_rounds(self):
        rows = make_funding_round_rows(
            [
                {
                    "assessed_at": "2026-05-22T02:00:00Z",
                    "project_name": "Nexus",
                    "x_handle": "NexusLabs",
                    "funding_rounds": [
                        {"round": "Seed", "amount_usd": 2_200_000, "date": "2022-12-01", "description": "Seed round"},
                        {"round": "Series A", "amount_usd": 25_000_000, "date": "2024-06-10", "description": "A round"},
                    ],
                }
            ]
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][3], "Seed")
        self.assertEqual(rows[2][3], "Series A")

    def test_roadmap_sheet_expands_events(self):
        rows = make_roadmap_event_rows(
            [
                {
                    "assessed_at": "2026-05-22T02:00:00Z",
                    "project_name": "Nexus",
                    "x_handle": "NexusLabs",
                    "roadmap_events": [
                        {
                            "type": "Coinbase",
                            "name": "Coinbase listed Nexus（NEX）",
                            "date": "2026-05-20",
                            "days_after_tge": 0,
                            "url": "https://x.com/CoinbaseMarkets/status/2057085756120748167",
                        }
                    ],
                }
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], "Coinbase")
        self.assertEqual(rows[1][6], 0)

    def test_team_score_applies_pure_chinese_discount(self):
        self.assertEqual(calculate_team_score(90, "pure_chinese"), 27)

    def test_funding_score_requires_size_and_recency_for_full_score(self):
        score = calculate_funding_score(
            250_000_000,
            date(2025, 11, 22),
            today=date(2026, 5, 22),
        )
        self.assertAlmostEqual(score, 50.21, places=2)

    def test_social_percentile_uses_same_bucket_followers(self):
        rows = [
            {"bucket": "infra", "x_followers": "100"},
            {"bucket": "infra", "x_followers": "300"},
            {"bucket": "infra", "x_followers": "500"},
            {"bucket": "defi", "x_followers": "10000"},
        ]
        self.assertEqual(calculate_social_percentile(rows, "infra", 300), 50.0)

    def test_tge_signals_do_not_affect_total_score(self):
        total = calculate_total_score(team_score=80, funding_score=70, social_score=60)
        self.assertEqual(total, 70.0)

    def test_cli_writes_workbook_and_prints_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            benchmark = tmp_path / "benchmark.csv"
            workbook = tmp_path / "scores.xlsx"
            with benchmark.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "bucket",
                        "project_name",
                        "project_url",
                        "x_handle",
                        "x_followers",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "bucket": "infra",
                        "project_name": "Alpha",
                        "project_url": "https://rootdata.example/alpha",
                        "x_handle": "AlphaX",
                        "x_followers": "100",
                    }
                )
                writer.writerow(
                    {
                        "bucket": "infra",
                        "project_name": "Beta",
                        "project_url": "https://rootdata.example/beta",
                        "x_handle": "BetaX",
                        "x_followers": "300",
                    }
                )

            result = subprocess.run(
                [
                    sys.executable,
                    "score_project.py",
                    "--x-handle",
                    "BetaX",
                    "--rootdata-url",
                    "https://rootdata.example/beta",
                    "--team-raw-score",
                    "80",
                    "--team-background",
                    "international",
                    "--funding-amount-usd",
                    "500000000",
                    "--funding-date",
                    "2026-05-01",
                    "--bucket",
                    "infra",
                    "--tge-signal",
                    "tokenomics",
                    "--benchmark-csv",
                    str(benchmark),
                    "--workbook",
                    str(workbook),
                    "--today",
                    "2026-05-22",
                    "--no-live",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=True,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["x_handle"], "BetaX")
            self.assertIn("total_score", payload)
            self.assertEqual(payload["tge_signals"], ["tokenomics"])
            self.assertTrue(workbook.exists())
            self.assertGreater(workbook.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
