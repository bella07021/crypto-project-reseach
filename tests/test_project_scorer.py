import unittest
import csv
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from project_scorer import (
    calculate_funding_score,
    calculate_social_percentile,
    calculate_team_score,
    calculate_total_score,
)
from live_project_fetcher import (
    enrich_team_members_from_linkedin,
    fetch_live_project_detail,
    fetch_x_signal_htmls,
    fetch_x_signal_htmls_with_browser,
    fetch_text_with_vercel_browser,
    normalize_rootdata_url,
    parse_rootdata_detail_html,
    rootdata_fetch_urls,
    supplement_tge_evidence_from_x_html,
)
from score_project import make_funding_round_rows, make_roadmap_event_rows, make_score_rows


class ProjectScorerTests(unittest.TestCase):
    def test_normalize_rootdata_url_matches_case_and_host_variants(self):
        left = "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D"
        right = "https://www.rootdata.com/Projects/detail/Nexus?k=MTE3NDI%3D"
        self.assertEqual(normalize_rootdata_url(left), normalize_rootdata_url(right))

    def test_rootdata_fetch_urls_try_original_cn_before_www(self):
        urls = rootdata_fetch_urls("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D")
        self.assertEqual(urls[0], "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D")
        self.assertIn("https://www.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", urls)

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
        self.assertEqual(
            detail.tge_evidence_links,
            [
                {
                    "text": "NEX is live for trading",
                    "url": "https://x.com/nexuslabs/status/2057000000000000000",
                }
            ],
        )
        self.assertGreaterEqual(len(detail.roadmap_events), 1)
        self.assertEqual(detail.roadmap_events[0]["name"], "Coinbase listed Nexus（NEX）")
        self.assertEqual(detail.roadmap_events[0]["days_after_tge"], 0)
        self.assertEqual(detail.team_raw_score, 70)
        self.assertEqual(detail.team_background, "international")

    def test_parse_rootdata_detail_html_infers_international_team_from_member_names_without_location(self):
        html = """
        <h1>Solstice</h1>
        <a href="https://solsticelabs.io/">solsticelabs.io</a>
        <a href="https://x.com/solsticefi">X</a>
        <script>self.__next_f.push([1,"team\\":[
          {\\"name\\":{\\"en_value\\":\\"Ben Nadareski\\"},\\"lyingUrl\\":\\"https://linkedin.com/in/ben\\",\\"twitterUrl\\":\\"https://x.com/ben\\"},
          {\\"name\\":{\\"en_value\\":\\"Rena Shah\\"},\\"lyingUrl\\":\\"https://linkedin.com/in/rena\\"},
          {\\"name\\":{\\"en_value\\":\\"Marco Di Maggio\\"},\\"lyingUrl\\":\\"https://linkedin.com/in/marco\\"}
        ]"])</script>
        """

        detail = parse_rootdata_detail_html(html)

        self.assertEqual(detail.team_member_count, 3)
        self.assertEqual(detail.team_background, "international")
        self.assertEqual(detail.team_foreign_count, 3)
        self.assertEqual(detail.team_chinese_count, 0)
        self.assertEqual(detail.team_unknown_count, 0)
        self.assertEqual(detail.team_region_summary, "3/3 foreign")
        self.assertEqual(len(detail.team_members), 3)
        self.assertEqual(detail.team_members[0]["linkedin_url"], "https://linkedin.com/in/ben")

    def test_parse_rootdata_detail_html_does_not_score_untge_rootdata_signal_evidence(self):
        html = """
        <h1>Solstice</h1>
        <a href="https://x.com/solsticefi">X</a>
        <script>self.__next_f.push([1,"Solstice published tokenomics and airdrop season details at https://x.com/solsticefi/status/1234567890123456789 before IDO sale"])</script>
        """

        detail = parse_rootdata_detail_html(html)

        self.assertEqual(detail.tge_status, "未 TGE")
        self.assertEqual(detail.tge_probability, 0)
        self.assertEqual(detail.tge_evidence, [])
        self.assertEqual(detail.tge_evidence_links, [])

    def test_supplement_tge_evidence_from_project_x_html_links_own_statuses(self):
        detail = parse_rootdata_detail_html(
            """
            <h1>Solstice</h1>
            <a href="https://x.com/solsticefi">X</a>
            <script>self.__next_f.push([1,"Solstice published tokenomics and airdrop season details before IDO sale"])</script>
            """
        )
        x_html = """
        <article>Solstice published tokenomics and airdrop season details
        https://x.com/solsticefi/status/1234567890123456789 before IDO sale</article>
        """

        supplement_tge_evidence_from_x_html(detail, x_html)

        self.assertEqual(
            detail.tge_evidence_links,
            [
                {
                    "text": "出现代币经济模型相关表述",
                    "url": "https://x.com/solsticefi/status/1234567890123456789",
                },
                {
                    "text": "出现积分/空投/赛季活动相关表述",
                    "url": "https://x.com/solsticefi/status/1234567890123456789",
                },
                {
                    "text": "出现 IDO/Launchpad/Sale 相关表述",
                    "url": "https://x.com/solsticefi/status/1234567890123456789",
                },
            ],
        )

    def test_supplement_tge_evidence_from_project_x_html_ignores_collab_airdrop(self):
        detail = parse_rootdata_detail_html(
            """
            <h1>Citrea</h1>
            <a href="https://x.com/citrea_xyz">X</a>
            """
        )
        x_html = """
        <article>We partnered with OtherProject for a collab giveaway airdrop
        https://x.com/citrea_xyz/status/1234567890123456789</article>
        """

        supplement_tge_evidence_from_x_html(detail, x_html)

        self.assertEqual(detail.tge_evidence, [])
        self.assertEqual(detail.tge_evidence_links, [])

    def test_supplement_tge_evidence_from_project_x_html_accepts_project_airdrop(self):
        detail = parse_rootdata_detail_html(
            """
            <h1>Citrea</h1>
            <a href="https://x.com/citrea_xyz">X</a>
            """
        )
        x_html = """
        <article>Citrea points season airdrop eligibility and claim details are live
        https://x.com/citrea_xyz/status/2234567890123456789</article>
        """

        supplement_tge_evidence_from_x_html(detail, x_html)

        self.assertIn("出现积分/空投/赛季活动相关表述", detail.tge_evidence)
        self.assertEqual(
            detail.tge_evidence_links,
            [{"text": "出现积分/空投/赛季活动相关表述", "url": "https://x.com/citrea_xyz/status/2234567890123456789"}],
        )

    def test_fetch_x_signal_htmls_uses_project_search_pages(self):
        seen_urls = []

        def fake_fetch(url, **kwargs):
            seen_urls.append(url)
            return "<html></html>"

        with patch("live_project_fetcher.fetch_text", side_effect=fake_fetch):
            htmls = fetch_x_signal_htmls("citrea_xyz")

        self.assertEqual(len(htmls), 5)
        self.assertTrue(any("from%3Acitrea_xyz+airdrop" in url for url in seen_urls))

    def test_fetch_x_signal_htmls_uses_browser_when_static_pages_have_no_signals(self):
        captured = {}

        def fake_browser(urls):
            captured["urls"] = urls
            return ["rendered https://x.com/citrea_xyz/status/1 airdrop claim"]

        with patch("live_project_fetcher.fetch_text", return_value="<html>X shell</html>"), patch(
            "live_project_fetcher.fetch_x_signal_htmls_with_browser",
            side_effect=fake_browser,
        ):
            htmls = fetch_x_signal_htmls("citrea_xyz")

        self.assertTrue(any("status/1" in html for html in htmls))
        self.assertTrue(any("from%3Acitrea_xyz+airdrop" in url for url in captured["urls"]))

    def test_fetch_x_signal_htmls_with_browser_returns_rendered_payload(self):
        fake_result = type("Result", (), {"returncode": 0, "stdout": "rendered status", "stderr": ""})()

        with patch("live_project_fetcher.shutil.which", return_value="/usr/bin/node"), patch(
            "live_project_fetcher.subprocess.run",
            return_value=fake_result,
        ) as run:
            htmls = fetch_x_signal_htmls_with_browser(["https://x.com/search?q=x"])

        self.assertEqual(htmls, ["rendered status"])
        self.assertIn("x_signal_scrape.js", run.call_args.args[0])

    def test_parse_rootdata_detail_html_ignores_other_project_twitter_signal_links(self):
        html = """
        <h1>Citrea</h1>
        <a href="https://x.com/citrea_xyz">X</a>
        <script>self.__next_f.push([1,"Other project published tokenomics and airdrop season details at https://x.com/other_project/status/1234567890123456789 before IDO sale"])</script>
        """

        detail = parse_rootdata_detail_html(html)

        self.assertEqual(detail.x_handle, "citrea_xyz")
        self.assertEqual(detail.tge_status, "未 TGE")
        self.assertEqual(detail.tge_probability, 0)
        self.assertEqual(detail.tge_evidence_links, [])

    def test_fetch_live_project_detail_refetches_incomplete_rootdata_html(self):
        incomplete_html = '<html><head><title>RootData</title></head><body>Please enable JavaScript</body></html>'
        complete_html = """
        <h1>Nexus</h1>
        <a href="https://www.nexus.xyz/">nexus.xyz</a>
        <a href="https://x.com/nexuslabs">X</a>
        <span>Location</span><span>United States</span>
        <script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":25000000,\\"facDate\\":\\"2024-06-10 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Series A\\"},\\"desc\\":{\\"en_value\\":\\"Nexus raised $ 25 M in Series A round\\"}}]"])</script>
        <script>self.__next_f.push([1,"team\\":[{\\"name\\":{\\"en_value\\":\\"Daniel Marin\\"},\\"twitterUrl\\":\\"https://x.com/danielmarinq\\"}]"])</script>
        """

        with patch("live_project_fetcher.fetch_text", return_value=incomplete_html), patch(
            "live_project_fetcher.fetch_text_with_curl",
            return_value=complete_html,
            create=True,
        ):
            detail = fetch_live_project_detail("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", fetch_followers=False)

        self.assertEqual(detail.project_name, "Nexus")
        self.assertEqual(detail.website, "https://www.nexus.xyz/")
        self.assertEqual(detail.latest_funding_amount_usd, 25_000_000)
        self.assertEqual(detail.team_member_count, 1)
        self.assertEqual(detail.fetch_status, "ok")

    def test_enrich_team_members_from_linkedin_updates_region_counts(self):
        members = [
            {"name": "Alice Chen", "linkedin_url": "https://linkedin.com/in/alice", "x_url": "", "region": "foreign", "location": ""},
            {"name": "Bob Smith", "linkedin_url": "https://linkedin.com/in/bob", "x_url": "", "region": "unknown", "location": ""},
        ]

        def fake_fetch(url, retries=1, timeout=8):
            if "alice" in url:
                return '<html><body><span>Shanghai, China</span></body></html>'
            return '<html><body><span>San Francisco, United States</span></body></html>'

        summary = enrich_team_members_from_linkedin(members, budget_seconds=120, fetcher=fake_fetch)

        self.assertEqual(summary["chinese"], 1)
        self.assertEqual(summary["foreign"], 1)
        self.assertEqual(summary["known"], 2)
        self.assertEqual(members[0]["location"], "Shanghai, China")

    def test_fetch_live_project_detail_uses_curl_when_urlopen_fails(self):
        complete_html = """
        <h1>Nexus</h1>
        <a href="https://www.nexus.xyz/">nexus.xyz</a>
        <script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":25000000,\\"facDate\\":\\"2024-06-10 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Series A\\"}}]"])</script>
        """

        with patch("live_project_fetcher.fetch_text", side_effect=RuntimeError("dns failed")), patch(
            "live_project_fetcher.fetch_text_with_curl",
            return_value=complete_html,
        ):
            detail = fetch_live_project_detail("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", fetch_followers=False)

        self.assertEqual(detail.project_name, "Nexus")
        self.assertEqual(detail.latest_funding_amount_usd, 25_000_000)
        self.assertEqual(detail.fetch_status, "ok")

    def test_fetch_live_project_detail_uses_browser_when_curl_is_incomplete(self):
        incomplete_html = '<html><head><title>RootData</title></head><body>Please enable JavaScript</body></html>'
        complete_html = """
        <h1>Nexus</h1>
        <a href="https://www.nexus.xyz/">nexus.xyz</a>
        <script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":25000000,\\"facDate\\":\\"2024-06-10 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Series A\\"}}]"])</script>
        """

        with patch("live_project_fetcher.fetch_text", return_value=incomplete_html), patch(
            "live_project_fetcher.fetch_text_with_curl",
            return_value=incomplete_html,
        ), patch("live_project_fetcher.fetch_text_with_browser", return_value=complete_html, create=True):
            detail = fetch_live_project_detail("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", fetch_followers=False)

        self.assertEqual(detail.project_name, "Nexus")
        self.assertEqual(detail.website, "https://www.nexus.xyz/")
        self.assertEqual(detail.latest_funding_amount_usd, 25_000_000)
        self.assertEqual(detail.fetch_status, "ok")

    def test_fetch_live_project_detail_uses_vercel_browser_endpoint(self):
        incomplete_html = '<html><head><title>RootData</title></head><body>Please enable JavaScript</body></html>'
        complete_html = """
        <h1>Nexus</h1>
        <a href="https://www.nexus.xyz/">nexus.xyz</a>
        <script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":25000000,\\"facDate\\":\\"2024-06-10 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Series A\\"}}]"])</script>
        """

        with patch.dict("os.environ", {"VERCEL": "1", "VERCEL_URL": "example.vercel.app"}), patch(
            "live_project_fetcher.fetch_text",
            return_value=incomplete_html,
        ), patch("live_project_fetcher.fetch_text_with_curl", return_value=incomplete_html), patch(
            "live_project_fetcher.fetch_text_with_vercel_browser",
            return_value=complete_html,
        ):
            detail = fetch_live_project_detail("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", fetch_followers=False)

        self.assertEqual(detail.project_name, "Nexus")
        self.assertEqual(detail.latest_funding_amount_usd, 25_000_000)
        self.assertEqual(detail.fetch_status, "ok")

    def test_fetch_live_project_detail_uses_supplied_rootdata_html(self):
        complete_html = """
        <h1>Nexus</h1>
        <a href="https://www.nexus.xyz/">nexus.xyz</a>
        <script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":25000000,\\"facDate\\":\\"2024-06-10 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Series A\\"}}]"])</script>
        """

        with patch("live_project_fetcher.fetch_text", side_effect=AssertionError("network should not be used")):
            detail = fetch_live_project_detail(
                "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
                fetch_followers=False,
                rootdata_html=complete_html,
            )

        self.assertEqual(detail.project_name, "Nexus")
        self.assertEqual(detail.website, "https://www.nexus.xyz/")
        self.assertEqual(detail.latest_funding_amount_usd, 25_000_000)
        self.assertEqual(detail.fetch_status, "ok")

    def test_vercel_browser_endpoint_url_uses_deployment_host(self):
        captured = {}

        def fake_fetch(url, retries=1, timeout=65, headers=None):
            captured["url"] = url
            captured["timeout"] = timeout
            return "<html></html>"

        with patch.dict("os.environ", {"VERCEL_URL": "example.vercel.app"}), patch(
            "live_project_fetcher.fetch_text",
            side_effect=fake_fetch,
        ):
            fetch_text_with_vercel_browser("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D")

        self.assertTrue(captured["url"].startswith("https://example.vercel.app/api/rootdata-browser?"))
        self.assertIn("url=https%3A%2F%2Fcn.rootdata.com", captured["url"])
        self.assertEqual(captured["timeout"], 65)

    def test_vercel_browser_endpoint_url_uses_automation_bypass_secret(self):
        captured = {}

        def fake_fetch(url, retries=1, timeout=65, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return "<html></html>"

        with patch.dict(
            "os.environ",
            {
                "VERCEL_URL": "example.vercel.app",
                "VERCEL_AUTOMATION_BYPASS_SECRET": "secret value",
            },
        ), patch(
            "live_project_fetcher.fetch_text",
            side_effect=fake_fetch,
        ):
            fetch_text_with_vercel_browser("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D")

        self.assertNotIn("secret", captured["url"])
        self.assertEqual(captured["headers"]["x-vercel-protection-bypass"], "secret value")
        self.assertNotIn("x-vercel-set-bypass-cookie", captured["headers"])

    def test_fetch_live_project_detail_tries_alternate_rootdata_urls(self):
        incomplete_html = '<html><head><title>RootData</title></head><body>Please enable JavaScript</body></html>'
        complete_html = """
        <h1>Nexus</h1>
        <a href="https://www.nexus.xyz/">nexus.xyz</a>
        <script>self.__next_f.push([1,"\\"milestones\\":[{\\"facAmountUs\\":25000000,\\"facDate\\":\\"2024-06-10 00:00:00\\",\\"roundsName\\":{\\"en_value\\":\\"Series A\\"}}]"])</script>
        """
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return complete_html if "cn.rootdata.com" in url else incomplete_html

        with patch("live_project_fetcher.fetch_text", side_effect=fake_fetch), patch(
            "live_project_fetcher.fetch_text_with_curl",
            return_value=incomplete_html,
        ), patch("live_project_fetcher.fetch_text_with_browser", return_value=incomplete_html, create=True):
            detail = fetch_live_project_detail("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", fetch_followers=False)

        self.assertIn("cn.rootdata.com", calls[0])
        self.assertEqual(detail.project_name, "Nexus")
        self.assertEqual(detail.latest_funding_amount_usd, 25_000_000)

    def test_fetch_live_project_detail_marks_incomplete_payload(self):
        incomplete_html = '<html><head><title>RootData</title></head><body>Please enable JavaScript</body></html>'

        with patch("live_project_fetcher.fetch_text", return_value=incomplete_html), patch(
            "live_project_fetcher.fetch_text_with_curl",
            return_value=incomplete_html,
        ), patch("live_project_fetcher.fetch_text_with_browser", return_value=incomplete_html, create=True):
            detail = fetch_live_project_detail("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", fetch_followers=False)

        self.assertEqual(detail.project_name, "")
        self.assertEqual(detail.fetch_status, "rootdata_incomplete")
        self.assertIn("RootData detail payload incomplete", detail.evidence_notes)

    def test_fetch_live_project_detail_marks_rootdata_waf_challenge(self):
        waf_html = """
        <html><head>
          <script id="CaptchaScript" src="https://sg.captcha.qcloud.com/Captcha.js"></script>
        </head><body><form action="/WafCaptcha"></form></body></html>
        """

        with patch("live_project_fetcher.fetch_text", return_value=waf_html), patch(
            "live_project_fetcher.fetch_text_with_curl",
            return_value=waf_html,
        ), patch("live_project_fetcher.fetch_text_with_browser", return_value=waf_html, create=True):
            detail = fetch_live_project_detail("https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D", fetch_followers=False)

        self.assertEqual(detail.fetch_status, "rootdata_waf_blocked")
        self.assertIn("RootData WAF captcha blocked cloud fetch", detail.evidence_notes)

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
