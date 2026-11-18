import contextlib
import io
import os

from gwm.cli import Cli

from .test_propagate import Nested


class SyncTest(Nested):
    def test_cascade(self):
        out = self.gwm(self.root, "sync", "--no-fetch", "lib")
        self.assertIn("sync order: lib, proj\n", out)
        proj = self.git(self.proj_store, "rev-parse", "gwm/integration")
        self.assertEqual(self.git(self.proj_store, "rev-parse",
                                  f"{proj}:deps/lib"), self.new)
        for index, top in enumerate(self.tops):
            self.assertEqual(self.git(self.nested_lib(index), "rev-parse",
                                      "HEAD"), self.new)
            self.assertEqual(self.git(self.proj_of(index), "rev-parse",
                                      "HEAD"), proj)
            self.assertEqual(self.git(top, "rev-parse", "HEAD:proj"), proj)
            self.assertEqual(self.git(top, "log", "-1", "--format=%s"),
                             f"proj: Update to {proj[:10]}")
            self.assertEqual(self.git(top, "status", "--porcelain"), "")
        self.assertEqual(self.git(self.proj_store, "rev-list", "--count",
                                  proj, "^origin/main"), "1")
        out = self.gwm(self.root, "sync", "--no-fetch", "lib")
        self.assertNotIn("committed", out)
        self.assertNotIn(" -> ", out)

    def test_no_cascade(self):
        out = self.gwm(self.root, "sync", "--no-fetch", "--no-cascade",
                       "lib")
        self.assertIn("sync order: lib\n", out)
        self.assertEqual(self.git(self.nested_lib(1), "rev-parse", "HEAD"),
                         self.new)
        self.assertNotEqual(self.git(self.tops[0], "status", "--porcelain"),
                            "")
        self.assertEqual(self.git(self.proj_store, "for-each-ref",
                                  "refs/heads/gwm/integration"), "")

    def test_dry_run(self):
        out = self.gwm(self.root, "sync", "-n", "lib")
        self.assertIn("sync order: lib, proj\n", out)
        self.assertIn("lib store/lib.git", out)
        self.assertEqual(self.git(self.nested_lib(1), "rev-parse", "HEAD"),
                         self.base)
        self.assertEqual(self.git(self.store, "for-each-ref",
                                  "refs/heads/gwm/integration"), "")

    def test_within(self):
        self.gwm(self.root, "sync", "--no-fetch", "--in", "top1", "lib")
        self.assertEqual(self.git(self.nested_lib(1), "rev-parse", "HEAD"),
                         self.base)
        self.assertEqual(self.git(self.tops[1], "log", "-1", "--format=%s"),
                         "add proj")
        self.assertEqual(self.git(self.tops[0], "rev-parse", "HEAD:proj"),
                         self.git(self.proj_of(0), "rev-parse", "HEAD"))
        self.assertEqual(self.git(self.proj_of(0), "rev-parse",
                                  "HEAD:deps/lib"), self.new)

    def make_conflict(self):
        lib2 = self.nested_lib(1)
        self.write(os.path.join(lib2, "new.txt"), "other\n")
        self.git(lib2, "add", "new.txt")
        self.git(lib2, "commit", "-q", "-m", "other new")

    def test_conflict_then_rerun(self):
        self.make_conflict()
        self.gwm(self.root, "sync", "--no-fetch", "lib", expect=1)
        scratch = os.path.join(self.store, "gwm-integrate")
        self.write(os.path.join(scratch, "new.txt"), "merged\n")
        self.git(scratch, "add", "new.txt")
        out = self.gwm(self.root, "sync", "--no-fetch", "lib")
        self.assertIn("== proj", out)
        lib = self.git(self.store, "rev-parse", "gwm/integration")
        self.assertEqual(self.git(self.store, "show", f"{lib}:new.txt"),
                         "merged")
        for index, top in enumerate(self.tops):
            self.assertEqual(self.git(self.nested_lib(index), "rev-parse",
                                      "HEAD"), lib)
            self.assertEqual(self.git(top, "status", "--porcelain"), "")

    def test_rerun_hint(self):
        self.make_conflict()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            Cli.main(["-C", self.root, "sync", "--no-fetch", "lib"])
        self.assertIn("then run `git wm -C", err.getvalue())
        self.assertIn("sync --no-fetch lib` again", err.getvalue())

    def test_skipped_checkout_is_not_recorded(self):
        lib2 = self.nested_lib(1)
        self.write(os.path.join(lib2, "other.txt"), "other\n")
        self.git(lib2, "add", "other.txt")
        self.git(lib2, "commit", "-q", "-m", "other change")
        self.write(os.path.join(lib2, "lib.txt"), "dirty\n")
        own = self.git(lib2, "rev-parse", "HEAD")
        out = self.gwm(self.root, "sync", "--no-fetch", "lib")
        self.assertIn("top2/proj deps/lib: dirty, skipped", out)
        self.assertIn("top2/proj deps/lib: not at ", out)
        self.assertIn("deps/lib: at ", out)
        self.assertEqual(self.git(lib2, "rev-parse", "HEAD"), own)
        lib = self.git(self.store, "rev-parse", "gwm/integration")
        self.assertEqual(self.git(self.proj_of(1), "rev-parse",
                                  "HEAD:deps/lib"), lib)
