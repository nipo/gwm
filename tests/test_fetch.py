import os

from .fixture import GitTestCase


class FetchTest(GitTestCase):
    """
    Upstream repositories `lib` (default branch `main`) and `tool`
    (default branch `trunk`), mapped to shared repositories not created
    yet.
    """

    def setUp(self):
        super().setUp()
        self.lib = self.make_repo("lib", [{"lib.txt": "lib\n"}])
        self.tool = self.make_repo("tool", [{"tool.txt": "tool\n"}])
        self.git(self.tool, "branch", "-m", "trunk")
        self.lib_store = self.path("store", "lib.git")
        self.tool_store = self.path("store", "tool.git")
        self.write_config(self.root, {
            "lib": (self.lib, self.lib_store),
            "tool": (self.tool, self.tool_store),
        })

    def ref(self, repo, name):
        return self.git(repo, "rev-parse", "-q", "--verify", name)

    def test_creates_missing(self):
        out = self.gwm(self.root, "fetch")
        self.assertIn(f"lib: cloning {self.lib} to store/lib.git\n", out)
        for store, upstream, branch in (
                (self.lib_store, self.lib, "main"),
                (self.tool_store, self.tool, "trunk")):
            self.assertEqual(self.git(store, "config", "core.bare"), "true")
            self.assertEqual(self.git(store, "remote", "get-url", "origin"),
                             upstream)
            self.assertEqual(self.ref(store, f"origin/{branch}"),
                             self.ref(upstream, "HEAD"))
            self.assertEqual(self.git(store, "symbolic-ref",
                                      "refs/remotes/origin/HEAD"),
                             f"refs/remotes/origin/{branch}")
        out = self.gwm(self.root, "heads", "tool")
        self.assertIn("(base origin/trunk ", out)

    def test_fetches_existing(self):
        self.gwm(self.root, "fetch", "lib")
        self.assertFalse(os.path.exists(self.tool_store))
        self.write(os.path.join(self.lib, "lib.txt"), "lib 1\n")
        self.git(self.lib, "commit", "-q", "-am", "lib 1")
        out = self.gwm(self.root, "fetch", "lib")
        self.assertEqual(out, "lib: fetching origin\n")
        self.assertEqual(self.ref(self.lib_store, "origin/main"),
                         self.ref(self.lib, "HEAD"))

    def test_bare_clone_gets_tracking(self):
        self.git(self.root, "clone", "-q", "--bare", self.lib,
                 self.lib_store)
        self.write(os.path.join(self.lib, "lib.txt"), "lib 1\n")
        self.git(self.lib, "commit", "-q", "-am", "lib 1")
        out = self.gwm(self.root, "fetch", "lib")
        self.assertIn("lib: tracking branches of origin in "
                      "refs/remotes/origin/\n", out)
        self.assertEqual(self.ref(self.lib_store, "refs/remotes/origin/main"),
                         self.ref(self.lib, "HEAD"))
        out = self.gwm(self.root, "fetch", "lib")
        self.assertNotIn("tracking", out)

    def test_adds_origin(self):
        os.makedirs(self.lib_store)
        self.git(self.lib_store, "init", "-q", "--bare")
        out = self.gwm(self.root, "fetch", "lib")
        self.assertIn(f"lib: added remote origin for {self.lib}\n", out)
        self.assertEqual(self.ref(self.lib_store, "origin/main"),
                         self.ref(self.lib, "HEAD"))

    def test_matching_remote_with_other_name(self):
        os.makedirs(self.lib_store)
        self.git(self.lib_store, "init", "-q", "--bare")
        self.git(self.lib_store, "remote", "add", "up", self.lib)
        self.gwm(self.root, "fetch", "lib")
        self.assertEqual(self.ref(self.lib_store, "up/main"),
                         self.ref(self.lib, "HEAD"))
        self.assertEqual(self.git(self.lib_store, "remote"), "up")

    def test_errors_do_not_stop_others(self):
        os.makedirs(self.lib_store)
        self.git(self.lib_store, "init", "-q", "--bare")
        self.git(self.lib_store, "remote", "add", "origin", self.tool)
        os.makedirs(self.tool_store)
        self.write(os.path.join(self.tool_store, "junk"), "x\n")
        out = self.gwm(self.root, "fetch", expect=1)
        self.assertIn(f"lib: error: no remote fetches a mapped URL, and "
                      f"origin is {self.tool}\n", out)
        self.assertIn("tool: error: ", out)
        self.assertIn("exists and is not empty", out)

    def test_first_url_and_relative_path(self):
        self.write(os.path.join(self.root, ".gwm"),
                   '[gwm "lib"]\n'
                   '\turl = upstream/lib\n'
                   '\turl = https://example.com/lib\n'
                   '\tpath = store/lib.git\n')
        self.gwm(self.root, "fetch", "lib")
        self.assertEqual(self.git(self.lib_store, "remote", "get-url",
                                  "origin"), self.lib)

    def test_unknown_mapping(self):
        self.gwm(self.root, "fetch", "nope", expect=1)
