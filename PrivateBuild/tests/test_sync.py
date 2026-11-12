"""sync.py, footprint.py and check.py: the pure parts, and small temporary Git repositories where Git's
behaviour matters. No network, no xcodebuild (check.py's commands are stubbed)."""

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = load("private_sync", "sync.py")
footprint = load("private_footprint", "footprint.py")
check = load("private_check", "check.py")
OLD, NEW = "a" * 40, "b" * 40


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
                           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          check=True, capture_output=True, text=True).stdout


class TempRepos(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def repo(self, name, files):
        root = self.dir / name
        root.mkdir()
        git(root, "init", "-q")
        self.commit(root, files, "base")
        return root

    @staticmethod
    def commit(root, files, message):
        for path, text in files.items():
            if text is None:
                (root / path).unlink()
                continue
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        git(root, "add", "-A")
        git(root, "commit", "-qm", message)
        return git(root, "rev-parse", "HEAD").strip()


class BaselineEditTests(unittest.TestCase):
    """The baseline bump edits by exact text and fails loudly on anything else."""

    def test_the_real_files_take_the_bump(self):
        verify_text = (REPO / "PrivateBuild/verify.py").read_text()
        old_hash, old_tag = sync.verify_baseline(verify_text)
        edited = sync.bump_verify(verify_text, NEW, "v9.9.9-999")
        self.assertEqual((NEW, "v9.9.9-999"), sync.verify_baseline(edited))
        self.assertEqual(1, sum(a != b for a, b in zip(verify_text.splitlines(), edited.splitlines())))
        manifest = sync.bump_upstream_json((REPO / "PrivateBuild/UPSTREAM.json").read_text(), NEW, "9.9.9 (999)", "2030-01-02")
        data = json.loads(manifest)
        self.assertEqual((NEW, "9.9.9 (999)", NEW, "2030-01-02"), (data["rivulet"]["revision"], data["rivulet"]["changelog_label"],
                         data["rivulet"]["last_reviewed_upstream_main"], data["prepared_on"]))
        self.assertNotEqual(NEW, data["engine"]["revision"], "the engine's revision is not the Rivulet baseline")
        notes = sync.bump_notes((REPO / sync.NOTES).read_text(), old_tag, old_hash, "v9.9.9-999", NEW, "2030-01-02")
        self.assertIn(f"upstream Rivulet `v9.9.9-999` (`{NEW}`), merged 2030-01-02", notes)
        self.assertNotIn(old_hash, notes)
        label = sync.newest_changelog_label((REPO / "Rivulet/Views/Components/WhatsNewView.swift").read_text())
        self.assertRegex(label, r"^\d+\.\d+\.\d+ \(\d+\)$")

    def test_unexpected_text_fails_loudly(self):
        line = f'BASELINE = "{OLD}"  # upstream v1-1, the last upstream release merged into this fork'
        for text in ("", line.replace("# upstream", "# Upstream"), line + "\n" + line):
            with self.subTest(text=text[:30]), self.assertRaises(sync.SyncError):
                sync.bump_verify(text, NEW, "v2-2")
        manifest = json.dumps({"prepared_on": "2026-01-01", "rivulet": {"revision": OLD, "changelog_label": "1 (1)",
                                                                       "last_reviewed_upstream_main": OLD},
                               "engine": {"revision": OLD}}, indent=2)
        with self.assertRaises(sync.SyncError):   # the same revision twice: which one is Rivulet's?
            sync.bump_upstream_json(manifest, NEW, "2 (2)", "2026-02-02")
        notes = f"This tree is upstream Rivulet `v1-1` (`{OLD}`), merged 2026-01-01, plus the overlay."
        self.assertEqual(f"This tree is upstream Rivulet `v2-2` (`{NEW}`), merged 2026-02-02, plus the overlay.",
                         sync.bump_notes(notes, "v1-1", OLD, "v2-2", NEW, "2026-02-02"))
        for broken in (notes.replace("merged 2026", "merged in 2026"), notes.replace(OLD, OLD[:7]), notes + "\n" + notes, ""):
            with self.subTest(broken=broken[-40:]), self.assertRaises(sync.SyncError):
                sync.bump_notes(broken, "v1-1", OLD, "v2-2", NEW, "2026-02-02")
        with self.assertRaises(sync.SyncError):
            sync.newest_changelog_label("let notes = []")

    def test_claude_md_keeps_the_private_block_exactly(self):
        block = b"@PrivateBuild/CLAUDE-private.md\nReference Player: read it first.\n\n"
        self.assertEqual(block + b"# New\n", sync.claude_md(block + b"# Old\n", b"# Old\n", b"# New\n"))
        real = (REPO / "CLAUDE.md").read_bytes()
        self.assertTrue(real.startswith(b"@PrivateBuild/CLAUDE-private.md\n"))
        with self.assertRaises(sync.SyncError):   # a private line inside upstream's text
            sync.claude_md(block + b"# Old\nprivate\n", b"# Old\n", b"# New\n")
        with self.assertRaises(sync.SyncError):   # the block is gone
            sync.claude_md(b"# Old\n", b"# Old\n", b"# New\n")

    def test_claude_md_refuses_a_carriage_return_instead_of_rewriting_it(self):
        block = b"@PrivateBuild/CLAUDE-private.md\n\n"
        for ours, old, new in ((block + b"# Old\r\n", b"# Old\r\n", b"# New\n"), (block + b"# Old\n", b"# Old\n", b"# New\r\n"),
                               (block + b"# Old\r", b"# Old\r", b"# New\r")):
            with self.subTest(new=new), self.assertRaises(sync.SyncError):
                sync.claude_md(ours, old, new)


class PreflightTests(unittest.TestCase):
    B = sync.CANDIDATE_BRANCH
    UPSTREAM = {"Rivulet/Localizable.xcstrings", "RivuletiOS/Localizable.xcstrings", "README.md"}

    def owned(self, path):
        return path in self.UPSTREAM

    def test_refusals(self):
        for branch, merging, lines in ((None, False, []), ("elsewhere", False, []), (self.B, True, []),
                                       (self.B, False, [" M README.md"]), (self.B, False, ["?? new.swift"]),
                                       (self.B, False, [" M Rivulet/Private.xcstrings"]),
                                       (self.B, False, [" M Rivulet/Localizable.xcstrings", " M README.md"]),
                                       (self.B, False, ["?? Rivulet/New.xcstrings"]),
                                       (self.B, False, [" M Other/Private.xcstrings"]),   # a catalog upstream does not have
                                       (self.B, False, [" M Rivulet/Localizable.xcstrings", " M Other/Private.xcstrings"])):
            with self.subTest(branch=branch, merging=merging, lines=lines), self.assertRaises(sync.SyncError):
                sync.preflight(branch, merging, lines, discard_drift=True, upstream_owned=self.owned)

    def test_clean_tree_and_catalog_drift(self):
        self.assertEqual([], sync.preflight(self.B, False, ["", ""], discard_drift=False, upstream_owned=self.owned))
        drift = [" M Rivulet/Localizable.xcstrings", "M  RivuletiOS/Localizable.xcstrings"]
        with self.assertRaises(sync.SyncError) as refused:
            sync.preflight(self.B, False, drift, discard_drift=False, upstream_owned=self.owned)
        self.assertIn("--discard-catalog-drift", str(refused.exception))
        self.assertEqual(["Rivulet/Localizable.xcstrings", "RivuletiOS/Localizable.xcstrings"],
                         sync.preflight(self.B, False, drift, discard_drift=True, upstream_owned=self.owned))


class SmallPartsTests(unittest.TestCase):
    PINS = {"engine": ("https://e", "1.0.0", "1" * 40), "lib": ("https://l", None, "2" * 40)}

    def lock(self, **changes):
        pins = [{"identity": "engine", "location": "https://e", "state": {"version": "1.0.0", "revision": "1" * 40}},
                {"identity": "lib", "location": "https://l", "state": {"revision": "2" * 40}}]
        for pin in pins:
            pin["state"].update(changes.get(pin["identity"], {}))
        return {"pins": pins + changes.get("extra", [])}

    def test_moved_pins(self):
        self.assertEqual([], sync.moved_pins(self.lock(), self.PINS))
        self.assertEqual([], sync.moved_pins(self.lock(), sync.pins_of(self.lock())))
        moved = sync.moved_pins(self.lock(engine={"version": "1.1.0", "revision": "3" * 40}), self.PINS)
        self.assertEqual(1, len(moved))
        self.assertIn("engine: 1.0.0 1111111 -> 1.1.0 3333333", moved[0])
        self.assertEqual(["new: added (https://n)"], sync.moved_pins(self.lock(extra=[{"identity": "new", "location": "https://n"}]), self.PINS))
        self.assertEqual(["lib: removed"], sync.moved_pins({"pins": self.lock()["pins"][:1]}, self.PINS))

    def test_undo_commands(self):
        self.assertTrue(sync.undo_commands("v1-2", None)[0].startswith("git merge --abort"))
        self.assertTrue(sync.undo_commands(None, "v1-2")[0].startswith("git reset --hard fallback/candidate-before-v1-2"))
        self.assertTrue(sync.undo_commands(None, None)[0].startswith("# nothing to undo"))
        self.assertEqual(2, sync.conflict_hunks("a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> t\n<<<<<<< HEAD\n"))


class ForecastTests(TempRepos):
    LOCK = "Rivulet.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"

    def setUp(self):
        super().setUp()
        resolved = json.dumps({"pins": [{"identity": "engine", "location": "https://e", "state": {"version": "1", "revision": "1" * 40}}]})
        self.up = self.repo("up", {"a.swift": "a\nb\nc\n", "hooked.swift": "x\ny\nz\n", self.LOCK: resolved})
        self.base = git(self.up, "rev-parse", "HEAD").strip()
        git(self.up, "tag", "v1-1")
        git(self.dir, "clone", "-q", str(self.up), "cand")
        self.cand = self.dir / "cand"
        git(self.cand, "remote", "rename", "origin", "upstream")
        self.commit(self.cand, {"hooked.swift": "x\nprivate\nz\n"}, "overlay")

    def forecast(self, ref):
        return sync.forecast(self.cand, ref, self.base, {"hooked.swift"}, {"pinned.swift"})

    def test_already_merged_is_clean(self):
        code, lines = self.forecast("v1-1")
        self.assertEqual(0, code)
        self.assertIn("Nothing new", lines[-1])

    def test_clean_release(self):
        self.commit(self.up, {"a.swift": "a\nB\nc\n"}, "upstream")
        git(self.up, "tag", "v1-2")
        code, lines = self.forecast("v1-2")
        self.assertEqual(0, code, lines)
        self.assertIn("Would merge with no conflict", lines)
        self.assertIn("Pins: upstream moves none", lines)
        self.assertEqual("", git(self.cand, "status", "--porcelain"), "no working-tree change")
        self.assertIn("refs/upstream-forecast/v1-2", git(self.cand, "for-each-ref"))
        self.assertNotIn("refs/tags/v1-2", git(self.cand, "for-each-ref"), "no tag fetched")

    def test_conflicting_release_with_a_moved_pin(self):
        resolved = json.dumps({"pins": [{"identity": "engine", "location": "https://e", "state": {"version": "2", "revision": "2" * 40}}]})
        self.commit(self.up, {"hooked.swift": "x\nupstream\nz\n", self.LOCK: resolved}, "upstream")
        code, lines = self.forecast("main")
        self.assertEqual(1, code, lines)
        self.assertIn("  hooked.swift   (has private hooks)", lines)
        self.assertTrue(any(line.startswith("Pins: engine: 1 1111111 -> 2 2222222") for line in lines), lines)
        self.assertIn("Touches files with private hooks: hooked.swift", lines)

    def test_errors_exit_2(self):
        with self.assertRaises(sync.SyncError):
            self.forecast("no-such-branch")
        with redirect_stdout(StringIO()):
            for bad in ("../x", "a..b", "x y"):
                with self.subTest(ref=bad):
                    self.assertEqual(2, sync.main(["--forecast", bad], root=self.cand))


class SyncRunTests(TempRepos):
    """The sync entry point end to end in temporary repositories: a synthetic upstream with two release
    tags and a candidate carrying a small overlay (the real verify.py, footprint.py and UPSTREAM.json)."""
    B = sync.CANDIDATE_BRANCH
    BLOCK = "@PrivateBuild/CLAUDE-private.md\nReference Player: read it first.\n\n"
    WHATSNEW = 'enum W {\n    static let changelogs: [(String, [String])] = [\n        ("{label}", ["notes"]),\n    ]\n}\n'

    def setUp(self):
        super().setUp()
        self.up = self.repo("up", {"CLAUDE.md": "# Upstream\n\nText.\n", "README.md": "readme\n",
                                   "Rivulet/Localizable.xcstrings": '{"strings" : {}}\n', "app.swift": "a\nb\nc\n",
                                   sync.WHATSNEW: self.WHATSNEW.replace("{label}", "1.0.0 (1)")})
        self.old = git(self.up, "rev-parse", "HEAD").strip()
        git(self.up, "tag", "-a", "-m", "one", "v1.0.0-1")
        git(self.dir, "clone", "-q", str(self.up), "cand")
        self.cand = self.dir / "cand"
        git(self.cand, "remote", "rename", "origin", "upstream")
        git(self.cand, "remote", "set-url", "--push", "upstream", "no-push://never")
        git(self.cand, "checkout", "-qB", self.B)
        verify_text = (HERE / "verify.py").read_text()
        old_line = sync.VERIFY_LINE.search(verify_text).group(0)
        self.commit(self.cand, {
            "PrivateBuild/verify.py": verify_text.replace(old_line, f'BASELINE = "{self.old}"  # upstream v1.0.0-1, '
                                                                   "the last upstream release merged into this fork"),
            "PrivateBuild/footprint.py": (HERE / "footprint.py").read_text(),
            "PrivateBuild/UPSTREAM.json": (HERE / "UPSTREAM.json").read_text(),
            sync.NOTES: f"# Notes\n\nThis tree is upstream Rivulet `v1.0.0-1` (`{self.old}`), merged 2026-01-01, plus the overlay.\n",
            "CLAUDE.md": self.BLOCK + "# Upstream\n\nText.\n", ".gitignore": "PrivateBuild/.build/\n__pycache__/\n",
            "app.swift": "a\nprivate\nb\nc\n", "Other/Private.xcstrings": '{"strings" : {}}\n'}, "overlay")
        self.release = self.commit(self.up, {"CLAUDE.md": "# Upstream\n\nText two.\n", "README.md": "readme two\n",
                                             "app.swift": "a\nb\nc\nd\n",
                                             sync.WHATSNEW: self.WHATSNEW.replace("{label}", "1.0.1 (2)")}, "release")
        git(self.up, "tag", "-a", "-m", "two", "v1.0.0-2")

    def run_sync(self, *argv):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sync.main(list(argv), root=self.cand)
        return code, out.getvalue() + err.getvalue()

    def state(self):
        """Every ref, HEAD, the tree's status, the repository's rerere setting and the tracked files' bytes."""
        files = {path: (self.cand / path).read_bytes() for path in git(self.cand, "ls-files").splitlines() if (self.cand / path).exists()}
        rerere = subprocess.run(["git", "-C", str(self.cand), "config", "--local", "--get", "rerere.enabled"],
                                capture_output=True, text=True).stdout
        return (git(self.cand, "for-each-ref", "--format=%(refname) %(objectname)"), git(self.cand, "rev-parse", "HEAD"),
                git(self.cand, "status", "--porcelain"), rerere, files)

    def assert_refused_unchanged(self, *argv, says):
        before = self.state()
        code, text = self.run_sync(*argv)
        self.assertEqual(2, code, text)
        self.assertIn(says, text)
        self.assertEqual(before, self.state(), "a refusal changed a ref, HEAD, the tree or the config")
        self.assertFalse((self.cand / ".git/MERGE_HEAD").exists())
        return text

    def test_refusals_change_nothing(self):
        cases = {
            "tag missing upstream": (lambda: None, ("v9.9.9-9",), "cannot fetch v9.9.9-9"),
            "tag already merged": (lambda: None, ("v1.0.0-1",), "already merged"),
            "dirty tree": (lambda: (self.cand / "app.swift").write_text("dirty\n"), ("v1.0.0-2",), "not clean"),
            "detached HEAD": (lambda: git(self.cand, "checkout", "-q", "--detach"), ("v1.0.0-2",), "detached HEAD"),
            "wrong branch": (lambda: git(self.cand, "checkout", "-qb", "elsewhere"), ("v1.0.0-2",), "on elsewhere"),
            "fallback tag elsewhere": (lambda: git(self.cand, "tag", "fallback/candidate-before-v1.0.0-2", "HEAD~1"),
                                       ("v1.0.0-2",), "git tag -d fallback/candidate-before-v1.0.0-2"),
            "second private catalog drift": (lambda: (self.cand / "Other/Private.xcstrings").write_text("{}\n"),
                                             ("v1.0.0-2", "--discard-catalog-drift"), "not clean"),
        }
        for name, (arrange, argv, says) in cases.items():
            with self.subTest(name):
                git(self.cand, "checkout", "-qf", self.B)
                git(self.cand, "clean", "-qfdx", "--", "app.swift", "Other")
                git(self.cand, "checkout", "-q", "--", ".")
                for tag in ("fallback/candidate-before-v1.0.0-2",):
                    subprocess.run(["git", "-C", str(self.cand), "tag", "-d", tag], capture_output=True)
                arrange()
                self.assert_refused_unchanged(*argv, says=says)
        self.assertEqual("{}\n", (self.cand / "Other/Private.xcstrings").read_text(), "the private catalog's edit is kept")

    def test_the_branch_is_a_setting(self):
        git(self.cand, "checkout", "-qb", "fork-elsewhere")
        self.assert_refused_unchanged("v1.0.0-2", says="not main")
        code, text = self.run_sync("v1.0.0-2", "--branch", "fork-elsewhere")
        self.assertEqual(1, code, text)   # past the branch check: merged (and blocked only by the missing lock)
        self.assertTrue((self.cand / ".git/MERGE_HEAD").exists())
        git(self.cand, "merge", "--abort")

    def test_a_private_change_in_a_must_equal_file_refuses_before_merging(self):
        self.commit(self.cand, {"README.md": "readme\nprivate line\n"}, "a private line in README")
        self.assert_refused_unchanged("v1.0.0-2", says="the overlay now modifies README.md")
        self.assertEqual("readme\nprivate line\n", (self.cand / "README.md").read_text())

    def test_claude_md_with_a_private_line_inside_upstreams_text_refuses_before_merging(self):
        self.commit(self.cand, {"CLAUDE.md": self.BLOCK + "# Upstream\n\nText.\nprivate\n"}, "private line")
        self.assert_refused_unchanged("v1.0.0-2", says="CLAUDE.md is no longer")

    def test_an_anchor_that_does_not_match_refuses_before_merging_with_no_file_written(self):
        self.commit(self.cand, {sync.NOTES: "> Upstream `v1.0.0-1`, merged long ago.\n"}, "anchor gone")
        old_report = self.cand / sync.REPORT_DIR / "sync-v1.0.0-2.txt"
        old_report.parent.mkdir(parents=True)
        old_report.write_text("OLD REPORT\n")
        text = self.assert_refused_unchanged("v1.0.0-2", says=f"{sync.NOTES}: expected one baseline line")
        report = old_report.read_text()
        self.assertNotIn("OLD REPORT", report, "the stale report is gone")
        self.assertIn("STOPPED: ", report, "the report carries the error")
        self.assertNotIn("Two ways out", text, "it stopped before the merge")

    def test_a_good_run_then_undo_rerun_and_the_second_sync_after_the_commit(self):
        head = git(self.cand, "rev-parse", "HEAD").strip()
        code, text = self.run_sync("v1.0.0-2")
        self.assertEqual(1, code, text)   # the synthetic tree has no Package.resolved: a blocker, as it should be
        self.assertTrue((self.cand / ".git/MERGE_HEAD").exists())
        self.assertEqual(head, git(self.cand, "rev-parse", "fallback/candidate-before-v1.0.0-2").strip())
        self.assertEqual(self.release, git(self.cand, "rev-parse", "upstream/v1.0.0-2^{commit}").strip())
        self.assertEqual("readme two\n", (self.cand / "README.md").read_text(), "git took upstream's bytes by itself")
        self.assertEqual(self.BLOCK + "# Upstream\n\nText two.\n", (self.cand / "CLAUDE.md").read_text())
        self.assertIn(f'BASELINE = "{self.release}"', (self.cand / "PrivateBuild/verify.py").read_text())
        self.assertIn("1.0.1 (2)", (self.cand / "PrivateBuild/UPSTREAM.json").read_text())
        self.assertIn("(created)", text)
        self.assertNotIn("counts as present", text, "the old caveat is gone: verify.py now fails on markers")
        self.assertEqual("true\n", git(self.cand, "config", "--local", "--get", "rerere.enabled"))
        self.assertTrue((self.cand / sync.REPORT_DIR / "sync-v1.0.0-2.txt").exists())
        code, text = self.run_sync("--undo")
        self.assertIn("git merge --abort", text)
        git(self.cand, "merge", "--abort")
        code, text = self.run_sync("v1.0.0-2")
        self.assertIn("(reused)", text, "the fallback at HEAD is reused, not moved")
        git(self.cand, "commit", "-qm", "Merge upstream v1.0.0-2")
        merged = git(self.cand, "rev-parse", "HEAD").strip()
        self.assert_refused_unchanged("v1.0.0-2", says="already merged")
        self.assertEqual(head, git(self.cand, "rev-parse", "fallback/candidate-before-v1.0.0-2").strip(), "never moved")
        code, text = self.run_sync("--undo")
        self.assertIn("git reset --hard fallback/candidate-before-v1.0.0-2", text)
        self.assertNotIn("WARNING", text)
        self.commit(self.cand, {"later.txt": "work\n"}, "work after the merge")
        code, text = self.run_sync("v1.0.0-2", "--undo")
        self.assertIn("WARNING: reset --hard also drops 1 commit(s) made after the merge", text)
        self.assertIn("work after the merge", text)
        self.assertIn(f"git revert -m 1 {merged[:7]}", text)

    def test_a_stop_after_the_merge_started_names_the_two_ways_out_and_the_abort_leaves_nothing(self):
        head = git(self.cand, "rev-parse", "HEAD").strip()
        (self.cand / sync.NOTES).chmod(0o444)   # the third baseline file cannot be written
        self.addCleanup((self.cand / sync.NOTES).chmod, 0o644)
        code, text = self.run_sync("v1.0.0-2")
        self.assertEqual(2, code, text)
        self.assertIn("stopped at step 4", text)
        self.assertIn("Two ways out", text)
        self.assertIn("git merge --abort", text)
        self.assertIn("STOPPED: PermissionError", (self.cand / sync.REPORT_DIR / "sync-v1.0.0-2.txt").read_text())
        git(self.cand, "merge", "--abort")
        self.assertEqual("", git(self.cand, "status", "--porcelain"), "the abort leaves no half-written baseline")
        self.assertEqual(head, git(self.cand, "rev-parse", "HEAD").strip())

    def test_forecast_of_a_tag_given_before_the_flag_changes_only_forecast_refs(self):
        before = git(self.cand, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
        code, text = self.run_sync("v1.0.0-2", "--forecast")
        self.assertIn("Forecast for upstream v1.0.0-2", text, "the tag given is the one forecast")
        code, text = self.run_sync("--forecast")
        self.assertIn("Forecast for upstream main", text)
        after = git(self.cand, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
        self.assertEqual(before, [line for line in after if not line.startswith("refs/upstream-forecast/")],
                         "no ref but refs/upstream-forecast/* changed (refs/remotes/upstream/main stays where it was)")
        self.assertEqual({"refs/upstream-forecast/v1.0.0-2", "refs/upstream-forecast/main"},
                         {line.split()[0] for line in after if line.startswith("refs/upstream-forecast/")})
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            sync.main(["--forecast", "--undo"], root=self.cand)


class FootprintTests(TempRepos):
    def test_compare(self):
        record = {"a.swift": [5, 1], "b.swift": [2, 0]}
        self.assertEqual(([], []), footprint.compare(record, {"a.swift": [5, 1], "b.swift": [2, 0]}))
        failures, _ = footprint.compare(record, {"a.swift": [6, 1], "b.swift": [2, 0]})
        self.assertEqual(["a.swift: +6 added lines, the record allows +5"], failures)
        failures, _ = footprint.compare(record, {"a.swift": [5, 1], "b.swift": [2, 0], "c.swift": [1, 0]})
        self.assertEqual(["new upstream file modified: c.swift (+1/-0)"], failures)
        failures, _ = footprint.compare(record, {"a.swift": [4, 3]})
        self.assertEqual(["a.swift: -3 removed lines, the record allows -1"], failures, "more removed lines fail too")
        failures, decreases = footprint.compare(record, {"a.swift": [4, 1]})
        self.assertEqual([], failures)
        self.assertEqual(["a.swift: +4/-1, recorded +5/-1", "b.swift: no longer modified"], decreases)
        failures, _ = footprint.compare(record, {"a.swift": [5, 1], "README.md": [1, 1]}, must_equal=("README.md",))
        self.assertEqual(1, len(failures))
        self.assertIn("README.md is on MUST_EQUAL_UPSTREAM", failures[0])
        failures, _ = footprint.compare({"README.md": [1, 1]}, {"README.md": [1, 1]}, must_equal=("README.md",))
        self.assertEqual(1, len(failures), "a record listing a must-equal file does not excuse it")
        rendered = json.loads(footprint.render(OLD, record))
        self.assertEqual({"files": 2, "added": 7, "removed": 1}, rendered["totals"])
        self.assertEqual(record, rendered["files"])

    def test_measure_and_not_checked(self):
        root = self.repo("r", {"up.swift": "1\n2\n3\n", "gone.swift": "g\n"})
        base = git(root, "rev-parse", "HEAD").strip()
        self.commit(root, {"up.swift": "1\nprivate\n2\n", "gone.swift": None, "new.swift": "mine\n"}, "overlay")
        self.assertEqual({"up.swift": [1, 1], "gone.swift": [0, 1]}, footprint.measure(root, base))
        self.assertIsNone(footprint.measure(root, "c" * 40), "baseline not present")
        self.assertIsNone(footprint.measure(self.dir, base), "not a git checkout")
        plain = self.dir / "zip"
        (plain / "PrivateBuild").mkdir(parents=True)
        shutil.copy(REPO / "PrivateBuild/verify.py", plain / "PrivateBuild/verify.py")
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, footprint.main([], root=plain))
        self.assertTrue(out.getvalue().startswith("NOT CHECKED"))

    def test_the_record_matches_this_checkout(self):
        current = footprint.measure(REPO, footprint.baseline(REPO))
        if current is None:
            self.skipTest("not a git checkout with the baseline commit")
        record = json.loads((REPO / footprint.RECORD).read_text())
        self.assertEqual([], footprint.compare(record["files"], current)[0])
        self.assertEqual(set(), set(footprint.MUST_EQUAL_UPSTREAM) & set(record["files"]), "the record and the list disagree")

    def test_sync_reads_the_one_list(self):
        self.assertNotIn("MUST_EQUAL_UPSTREAM = (", (HERE / "sync.py").read_text(), "one definition, in footprint.py")
        self.assertNotIn("Rivulet/Views/Player/PlayerContainerViewController.swift", footprint.MUST_EQUAL_UPSTREAM)


class CheckTableTests(unittest.TestCase):
    def run_stubbed(self, quick=False, everything=False, fail=(), bundle=True, found=True, summary=True, unmerged=()):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name)
        seen = []

        def runner(command, log):
            seen.append(command)
            if "test" in command and command[0] == "xcodebuild" and bundle:
                (out / "tvos-tests.xcresult").mkdir()
            name = " ".join(command)
            code = 1 if any(word in name for word in fail) else 0
            text = "Ran 3 tests in 0.1s\n\nOK (skipped=1)\n" if "unittest" in name else ("PASS: fine\n" if not code else "FAIL: broken\n")
            return code, text

        counts = {"result": "Passed", "passedTests": 10, "failedTests": 0, "skippedTests": 0, "expectedFailures": 0}
        with redirect_stdout(StringIO()):
            results = check.run_checks(REPO, out, quick, everything, runner, counts=lambda _: counts if summary else None,
                                       checkouts=lambda _: out if found else None, unmerged=lambda _: list(unmerged))
        return results, seen

    def test_full_run_passes_and_names_each_step(self):
        results, seen = self.run_stubbed(everything=True)
        self.assertEqual(0, check.exit_code(results))
        self.assertEqual(["passed"] * 11, [result.status for result in results], check.table(results))
        self.assertEqual("unmerged paths", results[0].name, "the first step")
        self.assertIn("10 of 10 passed", dict((r.name, r.detail) for r in results)["tvOS tests"])
        self.assertEqual(4, sum(command[0] == "xcodebuild" for command in seen))

    def test_refused_engine_patch_stops_before_building(self):
        results, seen = self.run_stubbed(fail=("engine.py",))
        self.assertEqual("failed", results[-1].status)
        self.assertEqual("engine patch", results[-1].name)
        self.assertFalse(any(command[0] == "xcodebuild" for command in seen))

    def test_quick_and_defaults_run_no_xcodebuild_where_they_should_not(self):
        results, seen = self.run_stubbed(quick=True)
        self.assertFalse(any(command[0] == "xcodebuild" for command in seen))
        self.assertEqual(0, check.exit_code(results))
        self.assertEqual(5, sum(result.status == "not run" for result in results))
        results, _ = self.run_stubbed(found=False)
        self.assertEqual("not checked", dict((r.name, r.status) for r in results)["verify.py --resolved-checkouts"])
        self.assertEqual(["not run", "not run"], [r.status for r in results if r.name in ("iOS build", "Mac build")])
        self.assertEqual(0, check.exit_code(results))

    def test_a_failure_fails_the_run_and_the_table_says_which(self):
        results, _ = self.run_stubbed(fail=("footprint.py",))
        self.assertEqual(1, check.exit_code(results))
        self.assertEqual(["footprint.py"], [r.name for r in results if r.status == "failed"])
        self.assertIn("footprint.py  ", check.table(results).replace(" " * 30, " "))
        results, _ = self.run_stubbed(bundle=False)
        self.assertEqual("failed", dict((r.name, r.status) for r in results)["tvOS tests"], "no result bundle is a failure")

    def test_unmerged_paths_fail_the_run_first(self):
        results, _ = self.run_stubbed(quick=True, unmerged=("Rivulet/A.swift",))
        self.assertEqual(("unmerged paths", "failed"), (results[0].name, results[0].status))
        self.assertEqual(1, check.exit_code(results))
        self.assertEqual("not checked", check.unmerged_result(None).status)

    def test_unmerged_paths_from_a_real_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init", "-q")
            (root / "a.txt").write_text("1\n")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "base")
            git(root, "checkout", "-qb", "side")
            (root / "a.txt").write_text("side\n")
            git(root, "commit", "-qam", "side")
            git(root, "checkout", "-q", "-")
            (root / "a.txt").write_text("main\n")
            git(root, "commit", "-qam", "main")
            subprocess.run(["git", "-C", str(root), "merge", "side"], capture_output=True)
            self.assertEqual(["a.txt"], check.unmerged_paths(root))
        with tempfile.TemporaryDirectory() as plain:
            self.assertIsNone(check.unmerged_paths(Path(plain)), "not a git checkout")

    def test_resolved_checkouts_run_after_the_build_that_resolves_packages(self):
        results, seen = self.run_stubbed()
        names = [r.name for r in results]
        self.assertGreater(names.index("verify.py --resolved-checkouts"), names.index("tvOS tests"))
        tests_at = next(i for i, c in enumerate(seen) if c[0] == "xcodebuild" and "test" in c)
        resolved_at = next(i for i, c in enumerate(seen) if "--resolved-checkouts" in c)
        self.assertGreater(resolved_at, tests_at)
        results, _ = self.run_stubbed(quick=True)
        self.assertIn("--quick", dict((r.name, r.detail) for r in results)["verify.py --resolved-checkouts"])

    def test_strings_are_not_checked_on_an_older_builds_data(self):
        results, seen = self.run_stubbed(summary=False)
        self.assertEqual("not checked", dict((r.name, r.status) for r in results)["check-private-strings.py"])
        self.assertFalse(any("check-private-strings.py" in " ".join(c) for c in seen), "not run at all")

    def test_the_commands_are_the_documented_ones(self):
        commands = check.xcodebuild_commands(Path("out"))
        tests = commands["tvOS tests"]
        self.assertIn("ReferencePlayer.xcworkspace", tests)
        self.assertNotIn("-project", tests)
        for argument in ("-parallel-testing-enabled", "-test-timeouts-enabled", "-default-test-execution-time-allowance",
                         "CODE_SIGN_IDENTITY=-", "-onlyUsePackageVersionsFromResolvedFile", "Reference Player"):
            self.assertIn(argument, tests)
        self.assertEqual(["CODE_SIGNING_ALLOWED=NO", "build"], commands["tvOS Release (device)"][-2:])
        notes = (REPO / "PrivateBuild/CLAUDE-private.md").read_text()
        self.assertNotIn("xcodebuild test", notes, "the notes point at check.py instead of repeating its commands")
        self.assertIn("check.py --print-commands", notes)


if __name__ == "__main__":
    unittest.main()
