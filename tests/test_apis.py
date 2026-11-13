"""API tests — fake urllib responses only, never live network."""
import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scout import apis, core


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        self._raw = payload
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, headers=None):
    return urllib.error.HTTPError(url, code, "error", headers or {}, io.BytesIO(b""))


class GithubApiTests(unittest.TestCase):
    def test_search_parses_fake_response(self):
        payload = {"items": [{
            "full_name": "org/repo",
            "html_url": "https://github.com/org/repo",
            "description": "desc",
            "stargazers_count": 9,
            "pushed_at": "2026-11-01T00:00:00Z",
            "license": {"spdx_id": "Apache-2.0"},
            "topics": [],
        }]}
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(payload)) as patched:
            results = apis.search_github("reasoning dataset", "reasoning")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "org/repo")
        request = patched.call_args[0][0]
        self.assertIn("api.github.com/search/repositories", request.full_url)
        headers_lower = {k.lower(): v for k, v in request.headers.items()}
        self.assertIn("user-agent", headers_lower)
        self.assertNotIn("authorization", headers_lower)

    def test_token_sent_as_bearer_header(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse({"items": []})) as patched:
            apis.search_github("q", "all", token="tok123")
        request = patched.call_args[0][0]
        self.assertEqual(request.headers.get("Authorization"), "Bearer tok123")

    def test_403_rate_limit_becomes_rate_limited_error(self):
        err = http_error("https://api.github.com/x", 403,
                         {"X-RateLimit-Reset": "1794629400"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(core.RateLimitedError) as ctx:
                apis.search_github("q", "all")
        self.assertIn("GitHub search limit reached", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.reset_at)

    def test_401_names_the_token(self):
        err = http_error("https://api.github.com/x", 401)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_github("q", "all", token="bad")
        self.assertIn("token", str(ctx.exception))

    def test_network_failure_is_plain_words(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(apis.SearchError) as ctx:
                apis.search_github("q", "all")
        self.assertIn("Could not reach the internet", str(ctx.exception))


class HfApiTests(unittest.TestCase):
    def test_search_parses_fake_response(self):
        payload = [{
            "id": "org/data",
            "likes": 4,
            "downloads": 100,
            "lastModified": "2026-10-01T00:00:00.000Z",
            "tags": [],
        }]
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(payload)) as patched:
            results = apis.search_huggingface("math", "training")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "Hugging Face")
        request = patched.call_args[0][0]
        self.assertIn("huggingface.co/api/datasets", request.full_url)

    def test_unexpected_shape_is_an_error(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse({"not": "a list"})):
            with self.assertRaises(apis.SearchError):
                apis.search_huggingface("math", "training")


class LinkCheckTests(unittest.TestCase):
    def test_head_ok(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(b"", status=200)):
            self.assertEqual(apis.check_link("https://example.com/"), "ok")

    def test_head_404_is_dead(self):
        err = http_error("https://example.com/", 404)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(apis.check_link("https://example.com/"), "dead")

    def test_head_refused_falls_back_to_get(self):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if request.method == "HEAD":
                raise http_error(request.full_url, 405)
            return FakeResponse(b"x", status=206)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(apis.check_link("https://example.com/"), "ok")
        self.assertEqual(calls["n"], 2)

    def test_network_error_is_unknown(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("down")):
            self.assertEqual(apis.check_link("https://example.com/"), "unknown")


if __name__ == "__main__":
    unittest.main()
