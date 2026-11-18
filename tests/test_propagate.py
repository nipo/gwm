import os

from .test_heads import Diverged
from .test_ops import Scenario


class PropagateTest(Diverged):
    """
    After integration, `c` (the longest tip) is kept as is, `b` is
    rewritten and `a` is fast-forwarded.
    """

    def setUp(self):
        super().setUp()
        self.commit(self.c, "c2.txt", "c2\n", "c second change")
        self.a = self.lib_of("a")
        self.gwm(self.root, "integrate", "lib")
        self.target = self.git(self.store, "rev-parse", "gwm/integration")

    def head_of(self, path):
        return self.git(path, "rev-parse", "HEAD")

    def kept_refs(self):
        return self.git(self.store, "for-each-ref", "--format=%(refname)",
                        "refs/gwm/propagate/").splitlines()

    def test_moves_all(self):
        old_b = self.head_of(self.b)
        out = self.gwm(self.root, "propagate", "lib")
        for path in (self.a, self.b, self.c):
            self.assertEqual(self.head_of(path), self.target)
            self.assertFalse(self.git_ok(path, "symbolic-ref", "-q", "HEAD"))
        self.assertIn("a deps/lib: ", out)
        self.assertIn("(fast-forward)", out)
        refs = self.kept_refs()
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0].endswith("/b-deps-lib"))
        self.assertEqual(self.git(self.store, "rev-parse", refs[0]), old_b)
        self.assertIn(f"rewritten, old head kept as {refs[0]}", out)
        status = self.git(self.work["a"], "status", "--porcelain")
        self.assertEqual(status, " M deps/lib")
        out = self.gwm(self.root, "propagate", "lib")
        self.assertEqual(out.count("up to date"), 3)

    def test_dry_run(self):
        before = [self.head_of(p) for p in (self.a, self.b, self.c)]
        out = self.gwm(self.root, "propagate", "-n", "lib")
        self.assertEqual(out.count("would move"), 3)
        self.assertEqual([self.head_of(p) for p in (self.a, self.b, self.c)],
                         before)
        self.assertEqual(self.kept_refs(), [])

    def test_within(self):
        self.gwm(self.root, "propagate", "--in", "a", "lib")
        self.assertEqual(self.head_of(self.a), self.target)
        self.assertNotEqual(self.head_of(self.b), self.target)
        self.gwm(self.root, "propagate", "--in", "nowhere", "lib", expect=1)

    def test_dirty(self):
        self.write(os.path.join(self.a, "lib.txt"), "local\n")
        out = self.gwm(self.root, "propagate", "--in", "a", "lib")
        self.assertIn("dirty, skipped", out)
        self.assertNotEqual(self.head_of(self.a), self.target)
        self.gwm(self.root, "propagate", "--in", "a", "--merge", "lib")
        self.assertEqual(self.head_of(self.a), self.target)
        self.assertEqual(self.read(os.path.join(self.a, "lib.txt")),
                         "local\n")

    def test_branch(self):
        self.git(self.b, "switch", "-q", "-c", "topic")
        out = self.gwm(self.root, "propagate", "--in", "b", "lib")
        self.assertIn("on topic, skipped", out)
        self.gwm(self.root, "propagate", "--in", "b", "--include-branches",
                 "lib")
        self.assertEqual(self.git(self.b, "symbolic-ref", "HEAD"),
                         "refs/heads/topic")
        self.assertEqual(self.head_of(self.b), self.target)

    def test_lost_changes_need_force(self):
        out = self.gwm(self.root, "propagate", "--rev", "origin/main",
                       "--in", "b", "lib")
        self.assertIn("2 changes not in", out)
        self.assertNotEqual(self.head_of(self.b), self.base)
        self.gwm(self.root, "propagate", "--rev", "origin/main", "--in", "b",
                 "--force", "lib")
        self.assertEqual(self.head_of(self.b), self.base)
        self.assertEqual(len(self.kept_refs()), 1)

    def test_published_gitlink(self):
        work = self.work["b"]
        self.git(work, "commit", "-q", "-am", "bump lib")
        self.git(work, "update-ref", "refs/remotes/origin/main", "HEAD")
        out = self.gwm(self.root, "propagate", "--in", "b", "lib")
        self.assertIn("recorded in origin/main", out)

    def test_not_a_submodule(self):
        self.git(self.store, "worktree", "add", "-q", "--detach",
                 self.path("loose"), self.base)
        out = self.gwm(self.root, "propagate", "lib")
        self.assertIn("loose: not a submodule, skipped", out)
        self.assertEqual(self.head_of(self.path("loose")), self.base)

    def test_refusals(self):
        self.gwm(self.root, "propagate", "--rev", "nope", "lib", expect=1)
        self.commit(self.c, "b.txt", "not b\n", "c version of b")
        self.gwm(self.root, "integrate", "lib", expect=1)
        self.gwm(self.root, "propagate", "lib", expect=1)


class Nested(Scenario):
    """
    `proj` is mapped as well and checked out in `top1` and `top2`, each
    containing a nested checkout of `lib`.  In `top1`, `lib` has a new
    commit recorded by `proj`.
    """

    def setUp(self):
        super().setUp()
        top = self.make_repo("top", [{"TOP": "top\n"}])
        self.add_submodule(top, self.proj, "proj")
        self.proj_store = self.bare_store("proj", self.proj)
        self.write_config(self.root, {"lib": (self.lib, self.store),
                                      "proj": (self.proj, self.proj_store)})
        self.tops = []
        for name in ("top1", "top2"):
            work = self.clone(top, name)
            self.gwm(work, "update")
            self.tops.append(work)
        self.git(self.store, "fetch", "-q", "origin",
                 "+refs/heads/*:refs/remotes/origin/*")
        self.git(self.proj_store, "fetch", "-q", "origin",
                 "+refs/heads/*:refs/remotes/origin/*")
        self.base = self.git(self.store, "rev-parse", "origin/main")
        lib1 = self.nested_lib(0)
        self.write(os.path.join(lib1, "new.txt"), "new\n")
        self.git(lib1, "add", "new.txt")
        self.git(lib1, "commit", "-q", "-m", "new lib change")
        self.new = self.git(lib1, "rev-parse", "HEAD")

    def nested_lib(self, index):
        return os.path.join(self.tops[index], "proj", "deps", "lib")

    def proj_of(self, index):
        return os.path.join(self.tops[index], "proj")


class PropagateNestedTest(Nested):
    def setUp(self):
        super().setUp()
        self.gwm(self.root, "bump", "-c", "--in", "top1", "lib")
        self.gwm(self.root, "integrate", "--no-fetch", "proj")

    def test_updates_nested(self):
        out = self.gwm(self.root, "propagate", "-n", "proj")
        self.assertIn("deps/lib: would check out", out)
        self.assertEqual(self.git(self.nested_lib(1), "rev-parse", "HEAD"),
                         self.base)
        self.gwm(self.root, "propagate", "proj")
        self.assertEqual(self.git(self.nested_lib(1), "rev-parse", "HEAD"),
                         self.new)
        self.assertEqual(self.git(self.proj_of(1), "status", "--porcelain"),
                         "")

    def test_moved_nested_left_alone(self):
        lib2 = self.nested_lib(1)
        self.write(os.path.join(lib2, "other.txt"), "other\n")
        self.git(lib2, "add", "other.txt")
        self.git(lib2, "commit", "-q", "-m", "other lib change")
        moved = self.git(lib2, "rev-parse", "HEAD")
        out = self.gwm(self.root, "propagate", "proj")
        self.assertIn("deps/lib: at ", out)
        self.assertIn("left alone", out)
        self.assertEqual(self.git(lib2, "rev-parse", "HEAD"), moved)
