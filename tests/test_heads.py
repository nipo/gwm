import os

from .test_ops import Scenario


class Diverged(Scenario):
    """
    Three checkouts of `lib`: `a` at the gitlink, `b` with two commits
    on top, `c` diverging with a commit of its own and a copy of one
    of `b`.
    """

    def setUp(self):
        super().setUp()
        self.work = {}
        for name in ("a", "b", "c"):
            work = self.clone(self.proj, name)
            self.gwm(work, "update")
            self.work[name] = work
        self.git(self.store, "fetch", "-q", "origin",
                 "+refs/heads/*:refs/remotes/origin/*")
        self.base = self.git(self.store, "rev-parse", "origin/main")
        self.b = self.lib_of("b")
        self.c = self.lib_of("c")
        self.commit(self.b, "shared.txt", "shared\n", "shared change")
        self.commit(self.b, "b.txt", "b\n", "b change")
        self.commit(self.c, "c.txt", "c\n", "c change")
        self.git(self.c, "cherry-pick",
                 self.git(self.b, "rev-parse", "HEAD~1"))

    def lib_of(self, name):
        return os.path.join(self.work[name], "deps", "lib")

    def commit(self, repo, path, content, message):
        self.write(os.path.join(repo, path), content)
        self.git(repo, "add", path)
        self.git(repo, "commit", "-q", "-m", message)

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD")[:10]



class HeadsTest(Diverged):
    def test_groups_and_counts(self):
        out = self.gwm(self.root, "heads", "lib")
        lines = out.splitlines()
        self.assertEqual(lines[0], f"lib store/lib.git (base origin/main "
                         f"{self.base[:10]})")
        self.assertEqual(lines[1:], [
            f"  {self.head(self.b)} +2 -0 (1 unique)",
            "      b deps/lib (not recorded)",
            f"    {self.base[:10]} +0 -0 (in {self.head(self.b)})",
            "        a deps/lib",
            f"  {self.head(self.c)} +2 -0 (1 unique)",
            "      c deps/lib (not recorded)",
        ])

    def test_verbose_lists_unique_commits(self):
        out = self.gwm(self.root, "heads", "-v", "lib")
        self.assertIn(" b change\n", out)
        self.assertIn(" c change\n", out)
        self.assertNotIn("shared change", out)

    def test_recorded_states(self):
        self.write(os.path.join(self.c, "dirt"), "x\n")
        self.git(self.work["b"], "add", "deps/lib")
        out = self.gwm(self.root, "heads", "lib")
        self.assertIn("      b deps/lib (staged)\n", out)
        self.assertIn("      c deps/lib (dirty, not recorded)\n", out)
        self.git(self.work["b"], "commit", "-q", "-m", "bump")
        out = self.gwm(self.root, "heads", "lib")
        self.assertIn("      b deps/lib\n", out)

    def test_explicit_base(self):
        out = self.gwm(self.root, "heads", "--base", self.head(self.b),
                       "lib")
        self.assertIn(f"  {self.head(self.c)} +2 -2 (1 unique)\n", out)
        self.assertIn(f"(base {self.head(self.b)})\n", out)
        self.assertIn(f"  {self.head(self.b)} +0 -0 (0 unique)\n", out)

    def test_branch_and_missing(self):
        self.git(self.c, "switch", "-q", "-c", "topic")
        self.git(self.store, "worktree", "add", "-q", "--detach",
                 self.path("loose"), self.base)
        os.rename(self.path("loose"), self.path("gone"))
        out = self.gwm(self.root, "heads", "lib")
        self.assertIn("      c deps/lib (topic, not recorded)\n", out)
        self.assertIn("        loose (missing)\n", out)

    def test_all_mappings_skip_missing(self):
        self.write_config(self.root, {
            "lib": (self.lib, self.store),
            "ghost": ("https://example.com/ghost", self.path("ghost.git")),
        })
        out = self.gwm(self.root, "heads")
        self.assertTrue(out.startswith("lib "))
        self.assertNotIn("ghost", out)
        out = self.gwm(self.root, "heads", "ghost")
        self.assertEqual(out, "ghost: missing ghost.git\n")

    def test_unknown_mapping(self):
        self.gwm(self.root, "heads", "nope", expect=1)
