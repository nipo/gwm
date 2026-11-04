import os
import unittest

from gwm.config import Config
from gwm.git import GwmError
from gwm.url import RepoUrl

from .fixture import GitTestCase


class RepoUrlTest(unittest.TestCase):
    def key(self, url, base="/base"):
        return RepoUrl.parse(url, base).key

    def test_remote_forms_are_equivalent(self):
        forms = [
            "git@github.com:nipo/nsl.git",
            "github.com:nipo/nsl",
            "ssh://git@github.com/nipo/nsl.git",
            "ssh://git@GitHub.com:22/nipo/nsl/",
            "https://github.com/nipo/nsl",
            "https://user@github.com/nipo/nsl.git",
            "git://github.com/nipo/nsl.git",
        ]
        for url in forms:
            self.assertEqual(self.key(url), "github.com/nipo/nsl", url)

    def test_path_case_is_kept(self):
        self.assertEqual(self.key("https://host/Org/Repo"), "host/Org/Repo")

    def test_local(self):
        base = os.path.realpath("/tmp")
        self.assertEqual(self.key("../x/repo.git", base),
                         "file:" + os.path.join(os.path.dirname(base),
                                                "x", "repo.git"))
        self.assertEqual(self.key("file:///srv/repo"), "file:/srv/repo")
        self.assertEqual(self.key("./a:b", base),
                         "file:" + os.path.join(base, "a:b"))


class ConfigTest(GitTestCase):
    def test_nearest_wins(self):
        self.write(self.path(".gwm"),
                   '[gwm "a"]\n\turl = https://h/a\n\tpath = outer-a\n'
                   '[gwm "b"]\n\turl = https://h/b\n\tpath = /abs/b\n')
        self.write(self.path("sub", "dir", ".gwm"),
                   '[gwm "a"]\n\turl = git@h:a.git\n\tpath = ../inner-a\n')
        self.write(self.path("xdg-config", "gwm", "config"),
                   '[gwm "c"]\n\turl = https://h/c\n\tpath = ~/c\n')
        config = Config()
        where = self.path("sub", "dir")
        a = config.lookup(RepoUrl.parse("ssh://h/a", "/"), where)
        self.assertEqual(a.path, self.path("sub", "inner-a"))
        b = config.lookup(RepoUrl.parse("https://h/b.git", "/"), where)
        self.assertEqual(b.path, os.path.realpath("/abs/b"))
        c = config.lookup(RepoUrl.parse("https://h/c", "/"), where)
        self.assertEqual(c.path, os.path.realpath(os.path.expanduser("~/c")))
        self.assertIsNone(
            config.lookup(RepoUrl.parse("https://h/d", "/"), where))
        outer = config.lookup(RepoUrl.parse("https://h/a", "/"), self.root)
        self.assertEqual(outer.path, self.path("outer-a"))

    def test_insteadof_applies(self):
        self.write(self.path(".gwm"),
                   '[gwm "a"]\n\turl = gh:nipo/a\n\tpath = a\n')
        self.git(self.root, "config", "--global",
                 "url.https://github.com/.insteadOf", "gh:")
        found = Config().lookup(
            RepoUrl.parse("git@github.com:nipo/a.git", "/"), self.root)
        self.assertEqual(found.path, self.path("a"))

    def test_duplicate_url_in_file(self):
        self.write(self.path(".gwm"),
                   '[gwm "a"]\n\turl = https://h/a\n\tpath = a\n'
                   '[gwm "b"]\n\turl = ssh://h/a\n\tpath = b\n')
        with self.assertRaises(GwmError):
            Config().chain(self.root)

    def test_missing_path(self):
        self.write(self.path(".gwm"), '[gwm "a"]\n\turl = https://h/a\n')
        with self.assertRaises(GwmError):
            Config().chain(self.root)
