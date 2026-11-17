import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest

from gwm.cli import Cli


class GitTestCase(unittest.TestCase):
    """
    Runs each test in a temporary directory, with git and gwm global
    configuration isolated from the user's.
    """

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="gwm-test-"))
        self.saved_env = dict(os.environ)
        gitconfig = os.path.join(self.root, "gitconfig")
        with open(gitconfig, "w") as f:
            f.write("[user]\n\tname = Test\n\temail = test@example.com\n"
                    "[init]\n\tdefaultBranch = main\n"
                    "[protocol \"file\"]\n\tallow = always\n"
                    "[advice]\n\tdetachedHead = false\n")
        for name in list(os.environ):
            if name.startswith("GIT_"):
                del os.environ[name]
        os.environ["GIT_CONFIG_GLOBAL"] = gitconfig
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.root, "xdg-config")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.root, "xdg-state")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.root, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def git(self, cwd, *args):
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True)
        if proc.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} in {cwd}: {proc.stderr}")
        return proc.stdout.rstrip("\n")

    def git_ok(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd,
                              capture_output=True).returncode == 0

    def gwm(self, cwd, *args, expect=0):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = Cli.main(["-C", cwd, *args])
        if code != expect:
            raise AssertionError(
                f"gwm {' '.join(args)} returned {code}: {err.getvalue()}"
                f"{out.getvalue()}")
        return out.getvalue()

    def write(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def make_repo(self, name, files):
        """
        Creates an upstream repository with one commit per entry of
        `files` (a list of {path: content} dicts).
        """
        path = self.path("upstream", name)
        os.makedirs(path)
        self.git(path, "init", "-q")
        for index, content in enumerate(files):
            for f, data in content.items():
                self.write(os.path.join(path, f), data)
            self.git(path, "add", "-A")
            self.git(path, "commit", "-q", "-m", f"{name} {index}")
        return path

    def add_submodule(self, repo, url, path):
        self.git(repo, "submodule", "add", "-q", url, path)
        self.git(repo, "commit", "-q", "-m", f"add {path}")

    def clone(self, url, name):
        dest = self.path(name)
        self.git(self.root, "clone", "-q", url, dest)
        return dest

    def bare_store(self, name, url):
        dest = self.path("store", f"{name}.git")
        os.makedirs(dest)
        self.git(dest, "init", "-q", "--bare")
        self.git(dest, "remote", "add", "origin", url)
        self.git(dest, "fetch", "-q", "origin")
        return dest

    def write_config(self, directory, mappings):
        content = "".join(
            f'[gwm "{name}"]\n\turl = {url}\n\tpath = {path}\n'
            for name, (url, path) in mappings.items())
        self.write(os.path.join(directory, ".gwm"), content)

    def common_dir(self, worktree):
        return os.path.realpath(self.git(
            worktree, "rev-parse", "--path-format=absolute",
            "--git-common-dir"))
