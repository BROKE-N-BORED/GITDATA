"""Tests for offline detection (T67).

The network is mocked at the urlopen boundary: no live requests.
The GUI side (banner, cache-only search) is covered by the GUI
tests.
"""

import io
import unittest
import unittest.mock
import urllib.error

from scout import apis


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return b""


class IsOnlineTests(unittest.TestCase):
    def setUp(self):
        apis._ONLINE_CACHE["at"] = None
        apis._ONLINE_CACHE["online"] = None

    def tearDown(self):
        apis._ONLINE_CACHE["at"] = None
        apis._ONLINE_CACHE["online"] = None

    def test_a_reply_means_online(self):
        with unittest.mock.patch.object(
                apis.urllib.request, "urlopen",
                return_value=FakeResponse(200)):
            self.assertTrue(apis.is_online())

    def test_an_error_page_still_means_online(self):
        error = urllib.error.HTTPError(
            "https://huggingface.co", 500, "boom", {}, io.BytesIO(b""))
        with unittest.mock.patch.object(
                apis.urllib.request, "urlopen", side_effect=error):
            self.assertTrue(apis.is_online())

    def test_a_failed_connection_means_offline(self):
        with unittest.mock.patch.object(
                apis.urllib.request, "urlopen",
                side_effect=urllib.error.URLError("no route")):
            self.assertFalse(apis.is_online())

    def test_the_answer_is_cached(self):
        calls = []

        def fake_open(*args, **kwargs):
            calls.append(1)
            return FakeResponse(200)

        with unittest.mock.patch.object(
                apis.urllib.request, "urlopen", side_effect=fake_open):
            self.assertTrue(apis.is_online())
            self.assertTrue(apis.is_online())
        self.assertEqual(len(calls), 1)

    def test_force_checks_again(self):
        calls = []

        def fake_open(*args, **kwargs):
            calls.append(1)
            return FakeResponse(200)

        with unittest.mock.patch.object(
                apis.urllib.request, "urlopen", side_effect=fake_open):
            self.assertTrue(apis.is_online())
            self.assertTrue(apis.is_online(force=True))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
