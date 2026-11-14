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
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._raw = payload
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self._raw[self._pos:]
            self._pos = len(self._raw)
            return chunk
        chunk = self._raw[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

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


class RateMeterTests(unittest.TestCase):
    """T16: the rate-limit headers are captured, not thrown away."""

    def setUp(self):
        apis.GITHUB_RATE["remaining"] = None
        apis.GITHUB_RATE["reset"] = None

    def test_success_response_records_remaining(self):
        headers = {"X-RateLimit-Remaining": "7",
                   "X-RateLimit-Reset": "1794629400"}
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse({"items": []},
                                                  headers=headers)):
            apis.search_github("q", "all")
        self.assertEqual(apis.GITHUB_RATE["remaining"], 7)
        self.assertIsNotNone(apis.GITHUB_RATE["reset"])

    def test_error_response_records_remaining_too(self):
        err = http_error("https://api.github.com/x", 403,
                         {"X-RateLimit-Remaining": "0",
                          "X-RateLimit-Reset": "1794629400"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(core.RateLimitedError):
                apis.search_github("q", "all")
        self.assertEqual(apis.GITHUB_RATE["remaining"], 0)


class UpdateCheckTests(unittest.TestCase):
    """T21: the latest release tag is read from GitHub."""

    def test_latest_tag_returned(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(
                            {"tag_name": "v0.1.2"})):
            self.assertEqual(apis.latest_release_tag(), "v0.1.2")

    def test_network_failure_returns_none(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("down")):
            self.assertIsNone(apis.latest_release_tag())

    def test_missing_tag_returns_none(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse({"name": "x"})):
            self.assertIsNone(apis.latest_release_tag())


class SniffTests(unittest.TestCase):
    """T29: STELLA-ready sniffing from file lists + a shape peek."""

    JSONL = ('{"prompt": "a", "completion": "b"}\n'
             '{"prompt": "c", "completion": "d"}\n')
    INSTRUCTION = ('{"instruction": "Add", "output": "2"}\n'
                   '{"instruction": "Add", "output": "4"}\n')

    def _route(self, mapping):
        """A urlopen stand-in that answers by URL substring."""
        def fake_urlopen(request, timeout=None):
            url = request.full_url
            for needle, response in mapping:
                if needle in url:
                    if isinstance(response, Exception):
                        raise response
                    return response
            raise AssertionError(f"unexpected URL: {url}")
        return fake_urlopen

    def test_hf_ready_jsonl(self):
        detail = {"siblings": [{"rfilename": "data/train.jsonl"},
                               {"rfilename": "README.md"}],
                  "usedStorage": 12345}
        mapping = [
            ("api/datasets/acme/tiny", FakeResponse(detail)),
            ("resolve/main/data/train.jsonl", FakeResponse(self.JSONL)),
        ]
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._route(mapping)):
            result = apis.sniff_huggingface("acme/tiny")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["files"], ["data/train.jsonl"])
        self.assertEqual(result["total_size"], 12345)

    def test_hf_convert_jsonl(self):
        detail = {"siblings": [{"rfilename": "train.jsonl"}],
                  "usedStorage": 99}
        mapping = [
            ("api/datasets/acme/tiny", FakeResponse(detail)),
            ("resolve/main/train.jsonl", FakeResponse(self.INSTRUCTION)),
        ]
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._route(mapping)):
            result = apis.sniff_huggingface("acme/tiny")
        self.assertEqual(result["status"], "convert")

    def test_hf_parquet_only_is_convert(self):
        detail = {"siblings": [{"rfilename": "data/train.parquet"}],
                  "usedStorage": 99}
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(detail)):
            result = apis.sniff_huggingface("acme/tiny")
        self.assertEqual(result["status"], "convert")
        self.assertIn("parquet", result["detail"])

    def test_hf_no_data_files(self):
        detail = {"siblings": [{"rfilename": "README.md"}],
                  "usedStorage": 99}
        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResponse(detail)):
            result = apis.sniff_huggingface("acme/tiny")
        self.assertEqual(result["status"], "none")

    def test_hf_failure_is_unknown(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("down")):
            result = apis.sniff_huggingface("acme/tiny")
        self.assertEqual(result["status"], "unknown")

    def test_github_uses_default_branch_and_tree(self):
        repo = {"default_branch": "main"}
        tree = {"tree": [
            {"path": "data.jsonl", "type": "blob", "size": 500},
            {"path": "README.md", "type": "blob", "size": 10},
            {"path": "data", "type": "tree"},
        ], "truncated": False}
        mapping = [
            ("git/trees/main", FakeResponse(tree)),
            ("raw.githubusercontent.com/acme/tiny/main/data.jsonl",
             FakeResponse(self.JSONL)),
            ("api.github.com/repos/acme/tiny", FakeResponse(repo)),
        ]
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._route(mapping)):
            result = apis.sniff_github("acme/tiny")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["files"], ["data.jsonl"])
        self.assertEqual(result["total_size"], 510)

    def test_github_truncated_tree_says_so(self):
        repo = {"default_branch": "main"}
        tree = {"tree": [{"path": "data.csv", "type": "blob", "size": 5}],
                "truncated": True}
        mapping = [
            ("git/trees/main", FakeResponse(tree)),
            ("api.github.com/repos/acme/tiny", FakeResponse(repo)),
        ]
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._route(mapping)):
            result = apis.sniff_github("acme/tiny")
        self.assertIn("cut the file list short", result["detail"])

    def test_sniff_item_dispatches_by_source(self):
        detail = {"siblings": [{"rfilename": "train.jsonl"}],
                  "usedStorage": 1}
        mapping = [
            ("api/datasets/acme/tiny", FakeResponse(detail)),
            ("resolve/main/train.jsonl", FakeResponse(self.JSONL)),
        ]
        item = {"source": "Hugging Face", "title": "acme/tiny",
                "url": "https://huggingface.co/datasets/acme/tiny"}
        with mock.patch("urllib.request.urlopen",
                        side_effect=self._route(mapping)):
            result = apis.sniff_item(item)
        self.assertEqual(result["status"], "ready")

    def test_sniff_item_refuses_other_sources(self):
        item = {"source": "Kaggle",
                "url": "https://www.kaggle.com/datasets/x/y"}
        result = apis.sniff_item(item)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("GitHub and Hugging Face", result["detail"])


class DownloadFileTests(unittest.TestCase):
    """T35: streamed download with progress."""

    def test_download_writes_bytes_and_reports_progress(self):
        import tempfile
        body = b"a" * 5000
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.jsonl"
            with mock.patch(
                    "urllib.request.urlopen",
                    return_value=FakeResponse(
                        body, headers={"Content-Length": "5000"})):
                total = apis.download_file(
                    "https://example.com/file.jsonl", dest,
                    progress=lambda got, total: seen.append((got, total)),
                    chunk=1024)
            self.assertEqual(total, 5000)
            self.assertEqual(dest.read_bytes(), body)
        self.assertEqual(seen[-1], (5000, 5000))
        self.assertTrue(all(call[1] == 5000 for call in seen))

    def test_download_http_error_is_plain_words(self):
        import tempfile
        err = http_error("https://example.com/x", 404)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("urllib.request.urlopen", side_effect=err):
                with self.assertRaises(apis.SearchError) as ctx:
                    apis.download_file("https://example.com/x",
                                       Path(tmp) / "x")
        self.assertIn("404", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
