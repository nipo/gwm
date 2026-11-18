import os

from .test_heads import Diverged


class BumpTest(Diverged):
    """
    After integration and propagation, all checkouts of `lib` are at
    the integration result, not recorded by their superprojects.
    """

    def setUp(self):
        super().setUp()
        self.a = self.lib_of("a")
        self.gwm(self.root, "integrate", "lib")
        self.gwm(self.root, "propagate", "lib")
        self.target = self.git(self.store, "rev-parse", "gwm/integration")

    def staged(self, work):
        return self.git(work, "diff", "--cached", "--name-only")

    def test_stage(self):
        heads = {n: self.git(w, "rev-parse", "HEAD")
                 for n, w in self.work.items()}
        out = self.gwm(self.root, "bump", "lib")
        self.assertEqual(out.count("staged "), 3)
        self.assertIn("a deps/lib: staged ", out)
        self.assertIn("(3 commits, not on any remote)", out)
        for name, work in self.work.items():
            self.assertEqual(self.staged(work), "deps/lib")
            self.assertEqual(self.git(work, "rev-parse", "HEAD"),
                             heads[name])
            self.assertEqual(self.git(work, "rev-parse", ":deps/lib"),
                             self.target)
        out = self.gwm(self.root, "bump", "lib")
        self.assertEqual(out.count("already staged"), 3)

    def test_commit_only_gitlink(self):
        work = self.work["a"]
        self.write(os.path.join(work, "README"), "changed\n")
        self.git(work, "add", "README")
        out = self.gwm(self.root, "bump", "--in", "a", "--commit", "lib")
        self.assertIn("committed ", out)
        self.assertEqual(self.git(work, "show", "--name-only", "--format=",
                                  "HEAD"), "deps/lib")
        self.assertEqual(self.staged(work), "README")
        self.assertEqual(self.git(work, "log", "-1", "--format=%B"),
                         f"deps/lib: Update to {self.target[:10]}\n\n"
                         f"Pick up 3 commits.")
        out = self.gwm(self.root, "bump", "--in", "a", "lib")
        self.assertIn("up to date", out)

    def test_commit_staged_gitlink(self):
        work = self.work["a"]
        self.gwm(self.root, "bump", "--in", "a", "lib")
        self.gwm(self.root, "bump", "--in", "a", "-c", "lib")
        self.assertEqual(self.git(work, "rev-parse", "HEAD:deps/lib"),
                         self.target)

    def test_log_and_message(self):
        self.gwm(self.root, "bump", "--in", "a", "-c", "--log", "lib")
        body = self.git(self.work["a"], "log", "-1", "--format=%b")
        self.assertIn(" b change", body)
        self.assertIn(" c change", body)
        self.gwm(self.root, "bump", "--in", "b", "-c", "-m", "Bump lib",
                 "lib")
        self.assertEqual(self.git(self.work["b"], "log", "-1",
                                  "--format=%B"), "Bump lib")

    def test_detached_superproject(self):
        work = self.work["a"]
        self.git(work, "checkout", "-q", "--detach")
        out = self.gwm(self.root, "bump", "--in", "a", "-c", "lib")
        self.assertIn("superproject HEAD is detached, skipped", out)
        self.assertEqual(self.staged(work), "")
        self.gwm(self.root, "bump", "--in", "a", "-c", "--allow-detached",
                 "lib")
        self.assertEqual(self.git(work, "rev-parse", "HEAD:deps/lib"),
                         self.target)

    def test_dry_run(self):
        out = self.gwm(self.root, "bump", "-n", "-c", "lib")
        self.assertEqual(out.count("would commit"), 3)
        for work in self.work.values():
            self.assertEqual(self.staged(work), "")

    def test_on_remote(self):
        self.gwm(self.root, "propagate", "--rev", "origin/main", "--in",
                 "a", "--force", "lib")
        self.write(os.path.join(self.lib, "up.txt"), "up\n")
        self.git(self.lib, "add", "up.txt")
        self.git(self.lib, "commit", "-q", "-m", "upstream change")
        self.git(self.store, "fetch", "-q", "origin",
                 "+refs/heads/*:refs/remotes/origin/*")
        self.gwm(self.root, "propagate", "--rev", "origin/main", "--in",
                 "a", "lib")
        out = self.gwm(self.root, "bump", "--in", "a", "lib")
        self.assertIn("(1 commits)", out)

    def test_nested_superproject(self):
        top = self.make_repo("top", [{"TOP": "top\n"}])
        self.add_submodule(top, self.proj, "proj")
        proj_store = self.bare_store("proj", self.proj)
        self.write_config(self.root, {"lib": (self.lib, self.store),
                                      "proj": (self.proj, proj_store)})
        work = self.clone(top, "top")
        self.gwm(work, "update")
        nested = os.path.join(work, "proj", "deps", "lib")
        self.git(nested, "checkout", "-q", "--detach", self.target)
        out = self.gwm(self.root, "bump", "--in", "top", "-c", "lib")
        self.assertIn("superproject is a checkout of proj", out)
        self.assertEqual(self.git(os.path.join(work, "proj"), "rev-parse",
                                  "HEAD:deps/lib"), self.target)
