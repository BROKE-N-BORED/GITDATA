"""Tests for the STELLA handoff (T64).

A fake STELLA home is built in a temp dir: profiles/<slug>/
profile.json plus projects. No real STELLA install is touched,
and nothing here writes outside the temp dir.
"""

import json
import tempfile
import unittest
from pathlib import Path

from scout import stella_link

JSONL = ('{"prompt": "a", "completion": "b"}\n'
         '{"prompt": "c", "completion": "d"}\n')


def _profile_manifest(name="demo", version="0.24.0"):
    return {
        "stella_version": version,
        "kind": "profile",
        "name": name,
        "display_name": name,
        "role": "",
        "ollama_tag": "demo-model:2b",
        "base_ref": "demo-model:2b",
        "processor": "",
        "config": {},
        "dependencies": [],
        "governing_seat": "",
        "created": "2026-11-01T00:00:00+00:00",
        "last_used": "",
    }


def _project_manifest(name="first", profile="demo", dataset_ref=""):
    return {
        "stella_version": "0.24.0",
        "kind": "project",
        "name": name,
        "display_name": name,
        "profile": profile,
        "dataset_ref": dataset_ref,
        "settings": dict(stella_link.STELLA_DEFAULT_SETTINGS),
        "settings_source": "manual",
        "metamorph": {"mode": "morph", "notes": ""},
        "status": "draft",
        "created": "2026-11-01T00:00:00+00:00",
    }


class StellaLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "stella"
        profile_dir = self.home / "profiles" / "demo"
        profile_dir.mkdir(parents=True)
        (profile_dir / "profile.json").write_text(
            json.dumps(_profile_manifest()), encoding="utf-8")
        self.jsonl = Path(self.tmp.name) / "train.jsonl"
        self.jsonl.write_text(JSONL, encoding="utf-8")

    def test_creates_a_project_with_the_dataset_inside(self):
        result = stella_link.link_dataset(
            self.jsonl, "demo", "My First Dataset", home=self.home)
        self.assertTrue(result["ok"], result["message"])
        self.assertTrue(result["created_project"])
        project_dir = Path(result["project_dir"])
        manifest = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], "project")
        self.assertEqual(manifest["name"], "my-first-dataset")
        self.assertEqual(manifest["profile"], "demo")
        self.assertEqual(manifest["dataset_ref"], "my-first-dataset.jsonl")
        self.assertEqual(manifest["status"], "draft")
        self.assertEqual(manifest["settings"]["method"], "lora")
        self.assertEqual(manifest["metamorph"]["mode"], "morph")
        copied = project_dir / "my-first-dataset.jsonl"
        self.assertTrue(copied.is_file())
        self.assertEqual(copied.read_text(encoding="utf-8"), JSONL)
        self.assertTrue((project_dir / "checkpoints").is_dir())
        self.assertTrue((project_dir / "notes.md").is_file())

    def test_links_into_an_existing_project_and_backs_it_up(self):
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps(_project_manifest()), encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home)
        self.assertTrue(result["ok"], result["message"])
        self.assertFalse(result["created_project"])
        self.assertIsNotNone(result["backup"])
        backup = Path(result["backup"])
        self.assertTrue(backup.is_file())
        old = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(old["dataset_ref"], "")
        manifest = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset_ref"], "first.jsonl")
        # Everything else in the manifest survived untouched.
        self.assertEqual(manifest["settings"]["rank"], 8)

    def test_refuses_to_replace_a_dataset_without_being_told_to(self):
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        before = _project_manifest(dataset_ref="other-data.jsonl")
        (project_dir / "project.json").write_text(
            json.dumps(before), encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home)
        self.assertFalse(result["ok"])
        self.assertIn("other-data.jsonl", result["message"])
        after = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(after, before)
        self.assertFalse((project_dir / "first.jsonl").exists())

    def test_replace_with_backup_when_asked(self):
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps(_project_manifest(dataset_ref="other-data.jsonl")),
            encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home, replace=True)
        self.assertTrue(result["ok"], result["message"])
        self.assertIsNotNone(result["backup"])
        manifest = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset_ref"], "first.jsonl")

    def test_refuses_same_name_dataset_with_different_content(self):
        # v0.1.4 T2: the name matches, but the file on disk is a
        # different dataset — replacing it silently would lose
        # the user's data.
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps(_project_manifest(dataset_ref="first.jsonl")),
            encoding="utf-8")
        other = '{"prompt": "old", "completion": "data"}\n'
        (project_dir / "first.jsonl").write_text(other, encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home)
        self.assertFalse(result["ok"])
        self.assertIn("already holds a different dataset",
                      result["message"])
        self.assertEqual(
            (project_dir / "first.jsonl").read_text(encoding="utf-8"),
            other)
        self.assertEqual(list(project_dir.glob("*.scout-bak")), [])

    def test_same_content_same_name_links_again(self):
        # v0.1.4 T2: re-linking the same dataset is not a
        # replacement — the file already on disk is identical.
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps(_project_manifest(dataset_ref="first.jsonl")),
            encoding="utf-8")
        (project_dir / "first.jsonl").write_text(JSONL, encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home)
        self.assertTrue(result["ok"], result["message"])
        manifest = json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset_ref"], "first.jsonl")

    def test_replace_same_name_dataset_when_asked(self):
        # v0.1.4 T2: with Replace checked, a same-name
        # different dataset is replaced — and backed up.
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps(_project_manifest(dataset_ref="first.jsonl")),
            encoding="utf-8")
        other = '{"prompt": "old", "completion": "data"}\n'
        (project_dir / "first.jsonl").write_text(other, encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home, replace=True)
        self.assertTrue(result["ok"], result["message"])
        self.assertIsNotNone(result["backup"])
        self.assertEqual(
            (project_dir / "first.jsonl").read_text(encoding="utf-8"),
            JSONL)

    def test_damaged_project_is_left_alone(self):
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            "{damaged json", encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home)
        self.assertFalse(result["ok"])
        self.assertIn("damaged", result["message"])
        self.assertEqual(
            (project_dir / "project.json").read_text(encoding="utf-8"),
            "{damaged json")

    def test_newer_stella_major_is_left_alone(self):
        project_dir = self.home / "profiles" / "demo" / "projects" / "first"
        project_dir.mkdir(parents=True)
        manifest = _project_manifest()
        manifest["stella_version"] = "1.0.0"
        (project_dir / "project.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first", home=self.home)
        self.assertFalse(result["ok"])
        self.assertIn("newer STELLA", result["message"])

    def test_missing_profile_is_a_plain_refusal(self):
        result = stella_link.link_dataset(
            self.jsonl, "nobody", "first", home=self.home)
        self.assertFalse(result["ok"])
        self.assertIn("no profile", result["message"])

    def test_missing_stella_home_is_a_plain_refusal(self):
        result = stella_link.link_dataset(
            self.jsonl, "demo", "first",
            home=Path(self.tmp.name) / "no-such-home")
        self.assertFalse(result["ok"])
        self.assertIn("not installed", result["message"])

    def test_bad_dataset_is_refused(self):
        one_row = Path(self.tmp.name) / "one.jsonl"
        one_row.write_text('{"prompt": "a", "completion": "b"}\n',
                           encoding="utf-8")
        result = stella_link.link_dataset(
            one_row, "demo", "first", home=self.home)
        self.assertFalse(result["ok"])
        self.assertIn("at least 2", result["message"])
        self.assertFalse(
            (self.home / "profiles" / "demo" / "projects"
             / "first").exists())

    def test_list_profiles(self):
        profiles = stella_link.list_profiles(home=self.home)
        self.assertEqual(
            [(entry["name"], entry["display_name"])
             for entry in profiles],
            [("demo", "demo")])

    def test_list_profiles_skips_damaged_ones(self):
        bad = self.home / "profiles" / "broken"
        bad.mkdir(parents=True)
        (bad / "profile.json").write_text("{nope", encoding="utf-8")
        profiles = stella_link.list_profiles(home=self.home)
        self.assertEqual([entry["name"] for entry in profiles], ["demo"])

    def test_slug_rules(self):
        self.assertEqual(stella_link.stella_slug("My Cool Dataset!"),
                         "my-cool-dataset")
        self.assertEqual(stella_link.stella_slug(""), "dataset")
        self.assertEqual(stella_link.stella_slug("a" * 100), "a" * 64)


class ValidateDatasetTests(unittest.TestCase):
    def test_mixed_format_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.jsonl"
            path.write_text(
                '{"prompt": "a", "completion": "b"}\n'
                '{"messages": [{"role": "user", "content": "hi"}, '
                '{"role": "assistant", "content": "yo"}]}\n',
                encoding="utf-8")
            ok, problem, _rows, _fmt = \
                stella_link.validate_dataset_file(path)
        self.assertFalse(ok)
        self.assertIn("mixes", problem)

    def test_valid_messages_file_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat.jsonl"
            path.write_text(
                '{"messages": [{"role": "user", "content": "hi"}, '
                '{"role": "assistant", "content": "yo"}]}\n'
                '{"messages": [{"role": "user", "content": "hey"}, '
                '{"role": "assistant", "content": "hi"}]}\n',
                encoding="utf-8")
            ok, _problem, rows, fmt = \
                stella_link.validate_dataset_file(path)
        self.assertTrue(ok)
        self.assertEqual(rows, 2)
        self.assertEqual(fmt, "messages")


if __name__ == "__main__":
    unittest.main()
