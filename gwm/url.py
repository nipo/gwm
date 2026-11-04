import os
import re
import urllib.parse

from .git import Git


class RepoUrl:
    """
    Normalized identity of a repository URL.

    Scheme, user and port are not part of the identity: the same
    repository reached over ssh, https or git protocols maps to the
    same key.  Host is compared case-insensitively, a trailing `.git`
    and slashes are ignored.  Local repositories map to their real
    path.
    """

    SCP_LIKE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.*)$")
    SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

    def __init__(self, key):
        self.key = key

    def __eq__(self, other):
        return isinstance(other, RepoUrl) and self.key == other.key

    def __hash__(self):
        return hash(self.key)

    def __repr__(self):
        return f"RepoUrl({self.key!r})"

    @staticmethod
    def clean_path(path):
        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-len(".git")]
        return path.rstrip("/")

    @classmethod
    def parse(cls, url, base):
        """
        Normalizes `url`.  Relative local paths are resolved from
        directory `base`.
        """
        if cls.SCHEME.match(url):
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme == "file":
                return cls.local(urllib.parse.unquote(parsed.path), base)
            host = (parsed.hostname or "").lower()
            return cls(f"{host}/{cls.clean_path(parsed.path)}")
        m = cls.SCP_LIKE.match(url)
        # A colon after a slash means a local path (as git does)
        if m is not None and "/" not in url.split(":", 1)[0]:
            return cls(f"{m['host'].lower()}/{cls.clean_path(m['path'])}")
        return cls.local(url, base)

    @classmethod
    def local(cls, path, base):
        path = os.path.realpath(os.path.join(base, os.path.expanduser(path)))
        return cls(f"file:{path}")

    @classmethod
    def resolve(cls, url, repo_dir):
        """
        Normalizes `url` as seen from repository at `repo_dir`, applying
        its `url.<base>.insteadOf` rewrites.
        """
        rewritten = Git(repo_dir).run("ls-remote", "--get-url", url).strip()
        return cls.parse(rewritten, repo_dir)
