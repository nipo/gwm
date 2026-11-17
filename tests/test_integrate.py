import os

from .test_heads import Diverged


class IntegrateTest(Diverged):
    BRANCH = "refs/heads/gwm/integration"

    def result(self):
        return self.git(self.store, "rev-parse", self.BRANCH)

    def subjects(self, rev, base):
        return sorted(self.git(self.store, "log", "--format=%s",
                               rev, f"^{base}").splitlines())

    def files(self, rev):
        return sorted(self.git(self.store, "ls-tree", "--name-only",
                               rev).splitlines())

    def is_ancestor(self, commit, rev):
        return self.git_ok(self.store, "merge-base", "--is-ancestor",
                           commit, rev)

    def scratch(self):
        return os.path.join(self.store, "gwm-integrate")

    def test_diverged_heads(self):
        out = self.gwm(self.root, "integrate", "lib")
        result = self.result()
        self.assertEqual(self.subjects(result, self.base),
                         ["b change", "c change", "shared change"])
        self.assertEqual(self.files(result), [
            "b.txt", "c.txt", "lib.txt", "shared.txt"])
        kept = [h for h in (self.b, self.c) if self.is_ancestor(
            self.git(h, "rev-parse", "HEAD"), result)]
        self.assertEqual(len(kept), 1, out)
        self.assertIn("rebased, 1 commits, 1 already applied", out)
        self.assertIn("gwm/integration at", out)
        self.assertNotIn("without an equivalent", out)
        self.assertFalse(os.path.exists(self.scratch()))
        self.assertEqual(self.git(self.store, "worktree", "list").count(
            "gwm-integrate"), 0)

    def test_fast_forward(self):
        self.git(self.c, "checkout", "-q", "--detach", self.base)
        out = self.gwm(self.root, "integrate", "lib")
        self.assertEqual(self.result(), self.git(self.b, "rev-parse", "HEAD"))
        self.assertIn("taken as is", out)

    def test_up_to_date(self):
        for h in (self.b, self.c):
            self.git(h, "checkout", "-q", "--detach", self.base)
        out = self.gwm(self.root, "integrate", "lib")
        self.assertEqual(self.result(), self.base)
        self.assertIn("0 commits on origin/main", out)

    def test_rebases_on_fetched_upstream(self):
        self.write(os.path.join(self.lib, "up.txt"), "up\n")
        self.git(self.lib, "add", "up.txt")
        self.git(self.lib, "commit", "-q", "-m", "upstream change")
        out = self.gwm(self.root, "integrate", "lib")
        self.assertIn("fetching origin", out)
        upstream = self.git(self.lib, "rev-parse", "HEAD")
        self.assertEqual(self.subjects(self.result(), upstream),
                         ["b change", "c change", "shared change"])

    def test_no_fetch_and_onto(self):
        out = self.gwm(self.root, "integrate", "--no-fetch", "--onto",
                       self.git(self.b, "rev-parse", "HEAD"), "-b", "x",
                       "lib")
        self.assertNotIn("fetching", out)
        result = self.git(self.store, "rev-parse", "refs/heads/x")
        self.assertEqual(self.subjects(result, self.base),
                         ["b change", "c change", "shared change"])

    def test_branch_checkouts_are_skipped(self):
        self.git(self.c, "switch", "-q", "-c", "topic")
        out = self.gwm(self.root, "integrate", "lib")
        self.assertIn("skipping c/deps/lib on topic", out)
        self.assertEqual(self.result(), self.git(self.b, "rev-parse", "HEAD"))
        self.gwm(self.root, "integrate", "--include-branches", "-b", "all",
                 "lib")
        self.assertEqual(self.subjects("refs/heads/all", self.base),
                         ["b change", "c change", "shared change"])

    def test_existing_branch_is_integrated(self):
        self.git(self.store, "branch", "gwm/integration",
                 self.git(self.c, "rev-parse", "HEAD"))
        self.git(self.c, "checkout", "-q", "--detach", self.base)
        out = self.gwm(self.root, "integrate", "lib")
        self.assertIn("c change", self.subjects(self.result(), self.base))
        self.assertIn(", fast-forward", out)

    def test_branch_checked_out_is_refused(self):
        self.git(self.c, "switch", "-q", "-c", "topic")
        self.gwm(self.root, "integrate", "-b", "topic", "lib", expect=1)

    def make_conflict(self):
        """
        Makes `c` the longest tip, so `b` is rebased over it and its
        "b change" conflicts.
        """
        self.commit(self.c, "b.txt", "not b\n", "c version of b")

    def test_conflict_continue(self):
        self.make_conflict()
        self.gwm(self.root, "integrate", "lib", expect=1)
        self.assertTrue(os.path.isdir(self.scratch()))
        out = self.gwm(self.root, "heads", "lib")
        self.assertIn("integration in progress", out)
        self.assertNotIn("gwm-integrate", out)
        self.gwm(self.root, "integrate", "lib", expect=1)
        self.write(os.path.join(self.scratch(), "b.txt"), "merged\n")
        self.git(self.scratch(), "add", "b.txt")
        out = self.gwm(self.root, "integrate", "--continue", "lib")
        self.assertIn("gwm/integration at", out)
        result = self.result()
        self.assertEqual(self.git(self.store, "show", f"{result}:b.txt"),
                         "merged")
        self.assertFalse(os.path.exists(self.scratch()))

    def test_conflict_continued_by_hand(self):
        self.make_conflict()
        self.gwm(self.root, "integrate", "lib", expect=1)
        self.write(os.path.join(self.scratch(), "b.txt"), "merged\n")
        self.git(self.scratch(), "add", "b.txt")
        self.git(self.scratch(), "-c", "core.editor=true", "rebase",
                 "--continue")
        self.gwm(self.root, "integrate", "--continue", "lib")
        self.assertEqual(self.git(self.store, "show",
                                  f"{self.result()}:b.txt"), "merged")

    def test_conflict_aborted_by_hand(self):
        self.make_conflict()
        self.gwm(self.root, "integrate", "lib", expect=1)
        self.git(self.scratch(), "rebase", "--abort")
        self.gwm(self.root, "integrate", "--continue", "lib", expect=1)
        self.gwm(self.root, "integrate", "--abort", "lib")

    def test_abort(self):
        self.make_conflict()
        self.gwm(self.root, "integrate", "lib", expect=1)
        out = self.gwm(self.root, "integrate", "--abort", "lib")
        self.assertIn("integration aborted", out)
        self.assertFalse(os.path.exists(self.scratch()))
        self.assertEqual(self.git(self.store, "for-each-ref",
                                  self.BRANCH), "")
        self.gwm(self.root, "integrate", "--abort", "lib", expect=1)

    def test_skipped_change_is_reported(self):
        self.make_conflict()
        self.gwm(self.root, "integrate", "lib", expect=1)
        self.git(self.scratch(), "rebase", "--skip")
        out = self.gwm(self.root, "integrate", "--continue", "lib")
        self.assertIn("without an equivalent in the result", out)
        self.assertIn(" b change\n", out)
