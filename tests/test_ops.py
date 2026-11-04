import os

from .fixture import GitTestCase


class Scenario(GitTestCase):
    """
    Upstream `lib` (mapped to a bare store) and `other` (unmapped),
    both submodules of upstream `proj`.
    """

    def setUp(self):
        super().setUp()
        self.lib = self.make_repo("lib", [{"lib.txt": "lib 0\n"}])
        self.other = self.make_repo("other", [{"other.txt": "other 0\n"}])
        self.proj = self.make_repo("proj", [{"README": "proj\n"}])
        self.add_submodule(self.proj, self.lib, "deps/lib")
        self.add_submodule(self.proj, self.other, "other")
        self.store = self.bare_store("lib", self.lib)
        self.write_config(self.root, {"lib": (self.lib, self.store)})

    def assert_worktree_of(self, path, store):
        self.assertTrue(os.path.isfile(os.path.join(path, ".git")))
        self.assertEqual(self.common_dir(path), os.path.realpath(store))

    def assert_clean_submodules(self, proj):
        status = self.git(proj, "submodule", "status", "--recursive")
        for line in status.splitlines():
            self.assertEqual(line[0], " ", status)
        self.assertEqual(self.git(proj, "status", "--porcelain"), "")


class UpdateTest(Scenario):
    def test_fresh_clone(self):
        work = self.clone(self.proj, "work")
        self.gwm(work, "update")
        lib = os.path.join(work, "deps", "lib")
        self.assert_worktree_of(lib, self.store)
        self.assertEqual(self.read(os.path.join(lib, "lib.txt")), "lib 0\n")
        self.assertEqual(self.git(lib, "status", "--porcelain"), "")
        self.assertTrue(os.path.isdir(
            os.path.join(work, ".git", "modules", "other")))
        gitdir = self.git(lib, "rev-parse", "--absolute-git-dir")
        self.assertTrue(os.path.exists(os.path.join(gitdir, "locked")))
        self.assertEqual(os.path.basename(gitdir), "work-deps-lib")
        self.assert_clean_submodules(work)

    def test_follows_gitlink(self):
        work = self.clone(self.proj, "work")
        self.gwm(work, "update")
        self.write(os.path.join(self.lib, "lib.txt"), "lib 1\n")
        self.git(self.lib, "commit", "-q", "-am", "lib 1")
        upstream_lib = os.path.join(self.proj, "deps", "lib")
        self.git(upstream_lib, "pull", "-q")
        self.git(self.proj, "commit", "-q", "-am", "bump lib")
        self.git(work, "pull", "-q")
        self.gwm(work, "update")
        lib = os.path.join(work, "deps", "lib")
        self.assertEqual(self.read(os.path.join(lib, "lib.txt")), "lib 1\n")
        self.assert_clean_submodules(work)

    def test_nested(self):
        mid = self.make_repo("mid", [{"mid.txt": "mid\n"}])
        self.add_submodule(mid, self.lib, "lib")
        top = self.make_repo("top", [{"top.txt": "top\n"}])
        self.add_submodule(top, mid, "mid")
        mid_store = self.bare_store("mid", mid)
        self.write_config(self.root, {"lib": (self.lib, self.store),
                                      "mid": (mid, mid_store)})
        work = self.clone(top, "work")
        self.gwm(work, "update")
        self.assert_worktree_of(os.path.join(work, "mid"), mid_store)
        self.assert_worktree_of(os.path.join(work, "mid", "lib"), self.store)
        self.assert_clean_submodules(work)

    def test_clone_missing(self):
        os.rename(self.store, self.path("elsewhere"))
        work = self.clone(self.proj, "work")
        self.gwm(work, "update", expect=1)
        self.gwm(work, "update", "--clone-missing")
        self.assert_worktree_of(os.path.join(work, "deps", "lib"),
                                self.store)
        self.assertEqual(self.git(self.store, "config", "core.bare"), "true")


class AdoptTest(Scenario):
    def test_absorbed_with_local_state(self):
        work = self.clone(self.proj, "work")
        self.git(work, "submodule", "update", "--init", "-q")
        lib = os.path.join(work, "deps", "lib")
        self.git(lib, "switch", "-q", "-c", "feature")
        self.write(os.path.join(lib, "feature.txt"), "feature\n")
        self.git(lib, "add", "feature.txt")
        self.git(lib, "commit", "-q", "-m", "feature")
        self.write(os.path.join(lib, "lib.txt"), "stashed\n")
        self.git(lib, "stash", "-q")
        self.write(os.path.join(lib, "lib.txt"), "dirty\n")
        self.write(os.path.join(lib, "staged.txt"), "staged\n")
        self.git(lib, "add", "staged.txt")
        self.write(os.path.join(lib, "untracked.txt"), "untracked\n")
        before = self.git(lib, "status", "--porcelain")
        head = self.git(lib, "rev-parse", "HEAD")

        out = self.gwm(work, "adopt")

        self.assert_worktree_of(lib, self.store)
        self.assertEqual(self.git(lib, "status", "--porcelain"), before)
        self.assertEqual(self.git(lib, "rev-parse", "HEAD"), head)
        self.assertEqual(self.git(lib, "symbolic-ref", "HEAD"),
                         "refs/heads/feature")
        self.assertEqual(self.git(self.store, "rev-parse", "feature"), head)
        stash = self.git(self.store, "for-each-ref", "--format=%(refname)",
                         "refs/gwm/import/")
        self.assertEqual(stash, "refs/gwm/import/work-deps-lib/stash/0")
        self.assertFalse(os.path.exists(
            os.path.join(work, ".git", "modules", "deps", "lib")))
        self.assertIn("previous git directory", out)
        backups = os.listdir(self.path("xdg-state", "gwm", "backup"))
        self.assertEqual(len(backups), 1)
        self.assertTrue(os.path.isdir(
            os.path.join(work, ".git", "modules", "other")))

    def test_conflicting_branch_is_kept_aside(self):
        self.git(self.store, "branch", "main", "origin/main")
        work = self.clone(self.proj, "work")
        self.git(work, "submodule", "update", "--init", "-q")
        lib = os.path.join(work, "deps", "lib")
        self.git(lib, "switch", "-q", "main")
        self.write(os.path.join(lib, "lib.txt"), "diverged\n")
        self.git(lib, "commit", "-q", "-am", "diverged")
        head = self.git(lib, "rev-parse", "HEAD")
        self.gwm(work, "adopt")
        self.assertEqual(self.git(lib, "rev-parse", "HEAD"), head)
        self.assertNotEqual(self.git(self.store, "rev-parse", "main"), head)
        self.assertNotEqual(self.git(lib, "rev-parse", "--abbrev-ref", "HEAD"),
                            "main")
        self.assertEqual(
            self.git(self.store, "rev-parse",
                     "refs/gwm/import/work-deps-lib/heads/main"), head)
        self.assertEqual(self.git(lib, "status", "--porcelain"), "")

    def test_embedded(self):
        work = self.clone(self.proj, "work")
        self.git(work, "submodule", "init", "-q")
        lib = os.path.join(work, "deps", "lib")
        os.rmdir(lib)
        self.git(work, "clone", "-q", self.lib, lib)
        self.gwm(work, "adopt")
        self.assert_worktree_of(lib, self.store)
        self.assertEqual(self.git(lib, "status", "--porcelain"), "")

    def test_nested_unmapped_survives(self):
        mid = self.make_repo("mid", [{"mid.txt": "mid\n"}])
        self.add_submodule(mid, self.other, "other")
        top = self.make_repo("top", [{"top.txt": "top\n"}])
        self.add_submodule(top, mid, "mid")
        mid_store = self.bare_store("mid", mid)
        self.write_config(self.root, {"mid": (mid, mid_store)})
        work = self.clone(top, "work")
        self.git(work, "submodule", "update", "--init", "--recursive", "-q")
        nested = os.path.join(work, "mid", "other")
        self.write(os.path.join(nested, "other.txt"), "dirty\n")

        self.gwm(work, "adopt")

        mid_wt = os.path.join(work, "mid")
        self.assert_worktree_of(mid_wt, mid_store)
        gitdir = self.git(mid_wt, "rev-parse", "--absolute-git-dir")
        self.assertEqual(
            os.path.realpath(self.git(nested, "rev-parse",
                                      "--absolute-git-dir")),
            os.path.realpath(os.path.join(gitdir, "modules", "other")))
        self.assertEqual(self.git(nested, "status", "--porcelain"),
                         " M other.txt")
        self.assertEqual(self.git(nested, "rev-parse", "--show-toplevel"),
                         os.path.realpath(nested))

    def test_already_adopted_is_noop(self):
        work = self.clone(self.proj, "work")
        self.gwm(work, "update")
        self.assertEqual(self.gwm(work, "adopt"), "")


class DetachTest(Scenario):
    def test_absorbed(self):
        work = self.clone(self.proj, "work")
        self.gwm(work, "update")
        lib = os.path.join(work, "deps", "lib")
        self.write(os.path.join(lib, "lib.txt"), "dirty\n")
        gitdir = self.git(lib, "rev-parse", "--absolute-git-dir")

        self.gwm(work, "detach", "deps/lib")

        self.assertEqual(
            os.path.realpath(self.git(lib, "rev-parse", "--absolute-git-dir")),
            os.path.realpath(os.path.join(work, ".git", "modules",
                                          "deps", "lib")))
        self.assertFalse(os.path.exists(gitdir))
        self.assertNotIn(os.path.realpath(lib),
                         self.git(self.store, "worktree", "list"))
        self.assertEqual(self.git(lib, "status", "--porcelain"),
                         " M lib.txt")
        self.assertEqual(self.git(lib, "remote", "get-url", "origin"),
                         self.lib)
        self.git(lib, "rev-parse", "refs/remotes/origin/main")
        self.git(lib, "checkout", "-q", "lib.txt")
        self.assert_clean_submodules(work)
        self.git(work, "submodule", "update", "-q")

    def test_embedded_then_adopt_again(self):
        work = self.clone(self.proj, "work")
        self.gwm(work, "update")
        lib = os.path.join(work, "deps", "lib")
        self.gwm(work, "detach", "--embedded", "deps/lib")
        self.assertTrue(os.path.isdir(os.path.join(lib, ".git")))
        self.assertEqual(self.git(lib, "status", "--porcelain"), "")
        self.gwm(work, "adopt")
        self.assert_worktree_of(lib, self.store)
        self.assert_clean_submodules(work)

    def test_refuses_non_worktree(self):
        work = self.clone(self.proj, "work")
        self.gwm(work, "update")
        self.gwm(work, "detach", "other", expect=1)


class StatusTest(Scenario):
    def test_states(self):
        work = self.clone(self.proj, "work")
        out = self.gwm(work, "status")
        self.assertIn("deps/lib uninitialized", out)
        self.gwm(work, "update")
        out = self.gwm(work, "status")
        lines = {l.split()[1]: l for l in out.splitlines()}
        self.assertIn("worktree (mapped to", lines["deps/lib"])
        self.assertTrue(lines["deps/lib"].startswith(" "))
        self.assertIn("absorbed", lines["other"])
        self.write(os.path.join(work, "deps", "lib", "lib.txt"), "x\n")
        out = self.gwm(work, "status")
        self.assertIn("dirty", out)


class RemoveTest(Scenario):
    def setUp(self):
        super().setUp()
        self.work = self.clone(self.proj, "work")
        self.gwm(self.work, "update")
        self.lib = os.path.join(self.work, "deps", "lib")

    def test_clean(self):
        gitdir = self.git(self.lib, "rev-parse", "--absolute-git-dir")
        self.gwm(self.work, "remove", "deps/lib")
        self.assertEqual(os.listdir(self.lib), [])
        self.assertFalse(os.path.exists(gitdir))
        self.assertEqual(len(self.git(self.store, "worktree",
                                      "list").splitlines()), 1)
        status = self.git(self.work, "submodule", "status", "deps/lib")
        self.assertTrue(status.startswith("-"), status)
        self.assertEqual(self.git(self.store, "for-each-ref",
                                  "refs/gwm/"), "")
        self.gwm(self.work, "update")
        self.assert_worktree_of(self.lib, self.store)

    def test_dirty_needs_force(self):
        self.write(os.path.join(self.lib, "untracked"), "x\n")
        self.gwm(self.work, "remove", "deps/lib", expect=1)
        self.assertTrue(os.path.exists(os.path.join(self.lib, "lib.txt")))
        self.gwm(self.work, "remove", "--force", "deps/lib")
        self.assertEqual(os.listdir(self.lib), [])

    def test_orphan_head_is_kept(self):
        self.git(self.lib, "commit", "-q", "--allow-empty", "-m", "orphan")
        head = self.git(self.lib, "rev-parse", "HEAD")
        out = self.gwm(self.work, "remove", "deps/lib")
        refs = self.git(self.store, "for-each-ref",
                        "--format=%(refname) %(objectname)",
                        "refs/gwm/removed/")
        ref, commit = refs.split()
        self.assertEqual(commit, head)
        self.assertIn(ref, out)

    def test_unmapped_is_deinitialized(self):
        self.gwm(self.work, "remove", "other")
        self.assertEqual(os.listdir(os.path.join(self.work, "other")), [])
        self.assertTrue(os.path.isdir(
            os.path.join(self.work, ".git", "modules", "other")))


class RemoveNestedTest(Scenario):
    def setUp(self):
        super().setUp()
        self.mid = self.make_repo("mid", [{"mid.txt": "mid\n"}])
        self.add_submodule(self.mid, self.lib, "lib")
        self.add_submodule(self.mid, self.other, "other")
        top = self.make_repo("top", [{"top.txt": "top\n"}])
        self.add_submodule(top, self.mid, "mid")
        self.mid_store = self.bare_store("mid", self.mid)
        self.write_config(self.root, {"lib": (self.lib, self.store),
                                      "mid": (self.mid, self.mid_store)})
        self.work = self.clone(top, "work")
        self.gwm(self.work, "update")

    def test_nested_absorbed_needs_force(self):
        self.gwm(self.work, "remove", "mid", expect=1)
        self.assertTrue(os.path.exists(
            os.path.join(self.work, "mid", "other", "other.txt")))
        self.gwm(self.work, "remove", "--force", "mid")
        self.assertEqual(os.listdir(os.path.join(self.work, "mid")), [])
        for store in (self.store, self.mid_store):
            self.assertEqual(
                len(self.git(store, "worktree", "list").splitlines()), 1)

    def test_nested_worktrees_only(self):
        self.gwm(os.path.join(self.work, "mid"), "remove", "other")
        self.gwm(self.work, "remove", "mid")
        for store in (self.store, self.mid_store):
            self.assertEqual(
                len(self.git(store, "worktree", "list").splitlines()), 1)


class RepairTest(Scenario):
    def setUp(self):
        super().setUp()
        self.work = self.clone(self.proj, "work")
        self.gwm(self.work, "update")

    def lib_path(self, work):
        return os.path.join(work, "deps", "lib")

    def assert_healthy(self, work):
        lib = self.lib_path(work)
        self.assert_worktree_of(lib, self.store)
        self.assertEqual(self.git(lib, "status", "--porcelain"), "")
        self.assertIn(os.path.realpath(lib),
                      self.git(self.store, "worktree", "list"))
        out = self.gwm(work, "status")
        self.assertNotIn("broken", out)
        self.assertNotIn("unlocked", out)
        self.assert_clean_submodules(work)

    def test_healthy_is_noop(self):
        self.assertEqual(self.gwm(self.work, "repair"), "")

    def test_project_moved(self):
        moved = self.path("moved")
        os.rename(self.work, moved)
        self.assertIn("broken backlink", self.gwm(moved, "status"))
        self.gwm(moved, "repair")
        self.assert_healthy(moved)

    def test_submodule_git_mv(self):
        self.git(self.work, "mv", "deps/lib", "lib")
        self.git(self.work, "commit", "-q", "-m", "move lib")
        self.gwm(self.work, "repair")
        lib = os.path.join(self.work, "lib")
        self.assert_worktree_of(lib, self.store)
        self.assertTrue(os.path.isabs(
            self.read(os.path.join(lib, ".git")).split(":", 1)[1].strip()))
        gitdir = self.git(lib, "rev-parse", "--absolute-git-dir")
        self.assertFalse(os.path.exists(os.path.join(gitdir, "config")))
        self.assertEqual(self.gwm(self.work, "repair"), "")
        self.assertIn(os.path.realpath(lib),
                      self.git(self.store, "worktree", "list"))
        self.assert_clean_submodules(self.work)

    def test_store_moved(self):
        moved = self.path("store", "lib-moved.git")
        os.rename(self.store, moved)
        self.store = moved
        self.write_config(self.root, {"lib": (self.lib, moved)})
        self.gwm(self.work, "repair")
        self.assert_healthy(self.work)

    def test_both_moved(self):
        moved_store = self.path("store", "lib-moved.git")
        os.rename(self.store, moved_store)
        self.store = moved_store
        self.write_config(self.root, {"lib": (self.lib, moved_store)})
        moved = self.path("moved")
        os.rename(self.work, moved)
        self.gwm(moved, "repair")
        self.assert_healthy(moved)

    def test_copy_is_refused(self):
        import shutil
        copy = self.path("copy")
        shutil.copytree(self.work, copy, symlinks=True)
        self.gwm(copy, "repair", expect=1)
        self.assert_healthy(self.work)

    def test_relock(self):
        self.git(self.store, "worktree", "unlock", self.lib_path(self.work))
        self.assertIn("locked", self.gwm(self.work, "repair"))
        self.assert_healthy(self.work)

    def test_prune_after_native_deinit(self):
        self.git(self.work, "submodule", "deinit", "-q", "-f", "deps/lib")
        out = self.gwm(self.work, "repair")
        self.assertIn("stale worktree work-deps-lib", out)
        self.assertEqual(
            len(self.git(self.store, "worktree", "list").splitlines()), 2)
        self.gwm(self.work, "repair", "--prune")
        self.assertEqual(
            len(self.git(self.store, "worktree", "list").splitlines()), 1)
        self.gwm(self.work, "update")
        self.assert_healthy(self.work)
