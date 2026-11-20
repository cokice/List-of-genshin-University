"""Offline regression tests; never contact submitted domains or rewrite README."""

import importlib.util
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dns.resolver
import requests


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_url", ROOT / "verify-url.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

PARKED = "<title>Parking Page</title><h1>Registered at Spaceship</h1><p>Is this your domain?</p>"
LANDING = "<title>Campus navigation</title><a href='https://school.edu.cn/'>University</a>"


def response(body="", status=200, location=None):
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("utf-8")
    result.encoding = "utf-8"
    if location is not None:
        result.headers["Location"] = location
    return result


class VerifyURLTests(unittest.TestCase):
    def setUp(self):
        self.pages = {}
        self.cnames = {}
        self.requests = []
        self.output = io.StringIO()
        for mock in (
            patch.object(verify.resolver, "resolve", side_effect=self.resolve),
            patch.object(verify.s, "get", side_effect=self.get),
            patch.object(verify, "proxies", [None]),
            patch.dict(os.environ, {"NO_SKIP_EDU": "0"}),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def resolve(self, host, record):
        if record == "CNAME":
            if host not in self.cnames:
                raise dns.resolver.NoAnswer()
            return [SimpleNamespace(target=SimpleNamespace(to_text=lambda: self.cnames[host] + "."))]
        return [SimpleNamespace(address="192.0.2.1")]

    def get(self, url, **kwargs):
        self.requests.append(url)
        value = self.pages.get(url, requests.ConnectionError("offline fixture missing"))
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def check(self, url="https://example.test/"):
        return verify.check_url(url, file=self.output)

    def row(self):
        match = re.match(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*",
                         "| [Example](https://example.test/) | School |")
        return verify.replace_table_row(match)

    def test_spaceship_200_is_invalid(self):
        self.pages["https://example.test/"] = response(PARKED)
        self.assertEqual(self.check(), (0, "Parked domain"))

    def test_parking_requires_multiple_signals(self):
        for html in (
            "<title>Campus</title><p>Registered at Spaceship</p>",
            "<title>Parking Page</title><p>A tutorial on HTML</p>",
            '<title>Campus</title><script>const example = "<title>Parking Page</title>Registered at";</script>',
            "<title>Campus</title><!-- <title>Parking Page</title>Registered at -->",
        ):
            with self.subTest(html=html):
                self.assertFalse(verify.PageSignals(html).is_parked())

    def test_plain_200_needs_review(self):
        self.pages["https://example.test/"] = response(LANDING)
        self.assertEqual(self.check(), (3, "No verified redirect"))

    def test_empty_200_needs_review(self):
        self.pages["https://example.test/"] = response()
        self.assertEqual(self.check()[0], 3)

    def test_js_only_redirect_needs_review_without_execution(self):
        self.pages["https://example.test/"] = response("<script>location='https://school.edu.cn/'</script>")
        self.assertEqual(self.check()[0], 3)
        self.assertEqual(self.requests, ["https://example.test/"])

    def test_cdn_cname_is_not_proof_of_forwarding(self):
        self.cnames["example.test"] = "edge.cdn.test"
        self.pages["https://example.test/"] = response(LANDING)
        self.assertEqual(self.check()[0], 3)
        self.assertEqual(self.requests, ["https://example.test/"])

    def test_university_cname_remains_supported(self):
        self.cnames["example.test"] = "www.school.edu.cn"
        self.pages["https://example.test/"] = response(LANDING)
        self.assertEqual(self.check(), (1, ("CNAME", "www.school.edu.cn")))

    def test_parking_wins_over_cname(self):
        self.cnames["example.test"] = "school.edu.cn"
        self.pages["https://example.test/"] = response(PARKED)
        self.assertEqual(self.check()[0], 0)

    def test_http_redirect_to_university(self):
        self.pages["https://example.test/"] = response(status=301, location="https://school.edu.cn/")
        self.assertEqual(self.check(), (1, ("Redirect 301", "https://school.edu.cn/")))

    def test_http_redirect_to_non_edu_destination(self):
        self.pages["https://example.test/"] = response(status=302, location="https://game.test/")
        self.pages["https://game.test/"] = response("<title>Game</title>")
        self.assertEqual(self.check()[0], 1)

    def test_http_redirect_to_parking_stays_invalid(self):
        self.pages["https://example.test/"] = response(status=302, location="https://parking.test/")
        self.pages["https://parking.test/"] = response(PARKED)
        self.assertEqual(self.check(), (0, "Parked domain"))

    def test_nested_redirect_to_parking_stays_invalid(self):
        self.pages["https://example.test/"] = response(status=301, location="/next")
        self.pages["https://example.test/next"] = response('<meta http-equiv="refresh" content="0;url=https://parking.test/">')
        self.pages["https://parking.test/"] = response(PARKED)
        self.assertEqual(self.check(), (0, "Parked domain"))

    def test_canonical_http_https_www_redirect_is_not_enough(self):
        self.pages["http://example.test/"] = response(status=301, location="https://www.example.test/")
        self.pages["https://www.example.test/"] = response(LANDING)
        self.assertEqual(self.check("http://example.test/")[0], 3)

    def test_meta_refresh_attribute_order_case_and_quotes(self):
        self.pages["https://example.test/"] = response("<META content='0.5; URL=https://school.edu.cn/' HTTP-EQUIV='Refresh'>")
        self.assertEqual(self.check(), (1, ("Meta Refresh", "https://school.edu.cn/")))

    def test_noscript_meta_refresh_for_tianjin_landing_page(self):
        self.pages["https://example.test/"] = response('<noscript><meta http-equiv="refresh" content="3;url=https://www.tju.edu.cn/"></noscript><script>location.replace(TARGET)</script>')
        self.assertEqual(self.check(), (1, ("Meta Refresh", "https://www.tju.edu.cn/")))

    def test_relative_redirects_resolve_correctly(self):
        self.pages["https://example.test/a/start"] = response(status=302, location="../next")
        self.pages["https://example.test/next"] = response('<meta http-equiv="refresh" content="0;url=//school.edu.cn/">')
        self.assertEqual(self.check("https://example.test/a/start")[0], 1)
        self.assertEqual(self.requests, ["https://example.test/a/start", "https://example.test/next"])

    def test_unrelated_content_attribute_does_not_hide_meta_refresh(self):
        self.pages["https://example.test/"] = response('<meta name="description" content="123; not a redirect"><meta http-equiv="refresh" content="0;url=https://school.edu.cn/">')
        self.assertEqual(self.check()[0], 1)

    def test_reload_only_meta_needs_review(self):
        self.pages["https://example.test/"] = response('<meta http-equiv="refresh" content="30">')
        self.assertEqual(self.check()[0], 3)

    def test_incomplete_meta_does_not_crash(self):
        self.pages["https://example.test/"] = response('<meta http-equiv><meta http-equiv="refresh" content>')
        self.assertEqual(self.check()[0], 3)

    def test_refresh_without_url_prefix_still_works(self):
        self.pages["https://example.test/"] = response('<meta http-equiv="refresh" content="0;https://school.edu.cn/">')
        self.assertEqual(self.check()[0], 1)

    def test_missing_location_is_invalid(self):
        self.pages["https://example.test/"] = response(status=302)
        self.assertEqual(self.check(), (0, "Missing redirect location"))

    def test_unsupported_redirect_is_invalid(self):
        self.pages["https://example.test/"] = response(status=302, location="javascript:alert(1)")
        self.assertEqual(self.check(), (0, "Unsupported redirect URL"))

    def test_redirect_loop_stops(self):
        self.pages["https://example.test/"] = response(status=301, location="/#fragment")
        self.assertEqual(self.check(), (0, "Redirect loop"))
        self.assertEqual(len(self.requests), 1)

    def test_redirect_hop_limit(self):
        for i in range(12):
            self.pages[f"https://example.test/{i}"] = response(status=301, location=f"/{i + 1}")
        self.assertEqual(self.check("https://example.test/0"), (0, "Too many redirects"))
        self.assertEqual(len(self.requests), 10)

    def test_network_failure_remains_error(self):
        self.assertEqual(self.check(), (0, "Connection error"))

    def test_redirect_network_failure_remains_question(self):
        self.pages["https://example.test/"] = response(status=301, location="https://down.test/")
        self.assertEqual(self.check(), (2, "Connection error"))

    def test_ssl_retry_does_not_become_redirect_loop(self):
        self.pages["https://example.test/"] = [requests.exceptions.SSLError(), response(PARKED)]
        self.assertEqual(self.check(), (0, "Parked domain"))

    def test_direct_edu_keeps_existing_warning(self):
        self.assertEqual(self.check("https://school.edu.cn/"), (1, ("EDU Domain", None)))
        self.assertEqual(self.requests, [])

    def test_no_skip_edu_checks_destination(self):
        self.pages["https://school.edu.cn/"] = response(LANDING)
        with patch.dict(os.environ, {"NO_SKIP_EDU": "1"}):
            self.assertEqual(self.check("https://school.edu.cn/")[0], 1)
        self.assertEqual(self.requests, ["https://school.edu.cn/"])

    def test_table_unknown_is_not_in_valid_group(self):
        self.pages["https://example.test/"] = response(LANDING)
        row = self.row()
        self.assertIn(":grey_question: No verified redirect", row)
        self.assertEqual(verify.sort_key(row)[0], 1)
        self.assertFalse(any(marker in row for marker in verify.VALID_STATUS_MARKERS))

    def test_table_parked_is_not_rescued_by_unknown_variant(self):
        self.pages["https://example.test/"] = response(PARKED)
        self.pages["https://www.example.test/"] = response(LANDING)
        self.assertIn(":x: Parked domain", self.row())

    def test_table_parked_is_not_rescued_by_failed_redirect(self):
        self.pages["https://example.test/"] = response(PARKED)
        self.pages["https://www.example.test/"] = response(status=301, location="https://down.test/")
        self.assertIn(":x: Parked domain", self.row())

    def test_table_working_variant_can_rescue_parked_bare_domain(self):
        self.pages["https://example.test/"] = response(PARKED)
        self.pages["https://www.example.test/"] = response(status=301, location="https://school.edu.cn/")
        row = self.row()
        self.assertIn("https://www.example.test/", row)
        self.assertIn(":white_check_mark: Redirect 301", row)

    def test_main_regenerates_counts_sorting_and_new_entries(self):
        self.pages["https://example.test/"] = response(PARKED)
        self.pages["https://good.test/"] = response(status=301, location="https://school.edu.cn/")
        self.pages["https://new.test/"] = response(LANDING)
        original_dir = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path("new").mkdir()
                Path("new/campus.add.md").write_text("| [New](https://new.test/) | School |\n", encoding="utf-8")
                Path("README.md").write_text(
                    "目前有2个高校（其中2个有效）\n\n## 高校名单（2026-11-06 19:14 UTC+8）\n\n"
                    "| 网站 | 大学 | 状态 |\n| --- | --- | --- |\n"
                    "| [Parked](https://example.test/) | School | :white_check_mark: |\n"
                    "| [Good](https://good.test/) | School | :white_check_mark: |\n\nFooter\n",
                    encoding="utf-8",
                )
                with patch.object(verify.subprocess, "check_output", return_value=b""), \
                        patch.object(verify.os, "system"), \
                        patch.dict(os.environ, {"COUNT_ONLY": "0"}), redirect_stdout(io.StringIO()):
                    verify.main()
                result = Path("README.md").read_text(encoding="utf-8")
                self.assertIn("目前有3个高校（其中1个有效）", result)
                self.assertIn(":x: Parked domain", result)
                self.assertIn(":grey_question: No verified redirect", result)
                self.assertLess(result.index("[Good]"), result.index("[Parked]"))
                self.assertLess(result.index("[Good]"), result.index("[New]"))
                self.assertTrue(result.endswith("Footer\n"))
                self.assertFalse(Path("new/campus.add.md").exists())
            finally:
                os.chdir(original_dir)


if __name__ == "__main__":
    unittest.main()
