import os
import re
import shutil
import tempfile
import time

from .git import Git, GitDir, GitLink, GwmError
from .url import RepoUrl


class Submodule:
    def __init__(self, superproject, name, path, commit):
        self.superproject = superproject
        self.name = name
        self.path = path
        self.commit = commit
        self.abspath = os.path.join(superproject.toplevel, path)

    def __repr__(self):
        return f"Submodule({self.name!r}, {self.path!r})"

    def url(self):
        """
        Resolved URL from superproject config, None when not
        initialized.
        """
        return self.superproject.git.config_get(f"submodule.{self.name}.url")

    def init(self):
        if self.url() is None:
            self.superproject.git.run("submodule", "init", "--", self.path)
        return self.url()

    def update_mode(self):
        key = f"submodule.{self.name}.update"
        mode = self.superproject.git.config_get(key)
        if mode is None:
            mode = self.superproject.git.value(
                "config", "--file", ".gitmodules", "--get", key)
        return mode or "checkout"

    def gitdir(self):
        return GitLink.read(self.abspath)

    def is_populated(self):
        return self.gitdir() is not None

    def git(self):
        return Git(self.abspath)

    def head(self):
        return self.git().value("rev-parse", "--verify", "-q", "HEAD")

    def branch(self):
        return self.git().value("symbolic-ref", "-q", "HEAD")

    def is_dirty(self):
        out = self.git().run("status", "--porcelain", "--ignore-submodules")
        return bool(out.strip())


class Superproject:
    def __init__(self, toplevel):
        self.toplevel = os.path.realpath(toplevel)
        self.git = Git(self.toplevel)

    @classmethod
    def find(cls, directory):
        toplevel = Git(directory).value("rev-parse", "--show-toplevel")
        if not toplevel:
            raise GwmError(f"{directory}: not in a git working tree")
        return cls(toplevel)

    def gitdir(self):
        return os.path.realpath(
            self.git.run("rev-parse", "--absolute-git-dir").strip())

    def submodule_names(self):
        if not os.path.isfile(os.path.join(self.toplevel, ".gitmodules")):
            return {}
        out = self.git.run("config", "--file", ".gitmodules", "--null",
                           "--list")
        names = {}
        for item in out.split("\0"):
            key, _, value = item.partition("\n")
            if not key.startswith("submodule.") or not key.endswith(".path"):
                continue
            names[value.strip("/")] = key[len("submodule."):-len(".path")]
        return names

    def submodules(self):
        out = self.git.run("ls-files", "--stage", "-z")
        names = self.submodule_names()
        result = []
        for item in out.split("\0"):
            if not item:
                continue
            meta, _, path = item.partition("\t")
            mode, commit, _ = meta.split(" ")
            if mode != "160000":
                continue
            if path not in names:
                raise GwmError(
                    f"{self.toplevel}: gitlink {path} has no .gitmodules "
                    f"entry")
            result.append(Submodule(self, names[path], path, commit))
        return result


class Checkout:
    """
    A working tree of a shared repository: its main working tree or a
    linked worktree, as listed by `git worktree list`.
    """

    def __init__(self, repo, path, head, branch, main, locked, prunable):
        self.repo = repo
        self.path = path
        self.head = head
        self.branch = branch
        self.main = main
        self.locked = locked
        self.prunable = prunable

    def __repr__(self):
        return f"Checkout({self.path!r}, {self.head!r})"

    def exists(self):
        return not self.prunable and os.path.isdir(self.path)

    def git(self):
        return Git(self.path)

    def gitdir(self):
        return os.path.realpath(
            self.git().run("rev-parse", "--absolute-git-dir").strip())

    def worktree_id(self):
        return "main" if self.main else os.path.basename(self.gitdir())

    def is_dirty(self):
        out = self.git().run("status", "--porcelain", "--ignore-submodules")
        return bool(out.strip())

    def superproject(self):
        """
        Superproject this checkout is a submodule of, or None.
        """
        if self.main:
            return None
        toplevel = self.git().value(
            "rev-parse", "--show-superproject-working-tree")
        return Superproject(toplevel) if toplevel else None

    def recorded(self, superproject):
        """
        How the superproject records HEAD of this checkout: "committed"
        in its HEAD, "staged" in its index only, or None.
        """
        path = os.path.relpath(self.path, superproject.toplevel)
        if superproject.git.value(
                "rev-parse", "-q", "--verify",
                f"HEAD:{path}") == self.head:
            return "committed"
        out = superproject.git.run("ls-files", "--stage", "-z", "--", path)
        meta = out.split("\0")[0].partition("\t")[0].split(" ")
        if len(meta) == 3 and meta[0] == "160000" and meta[1] == self.head:
            return "staged"
        return None


class SharedRepo:
    """
    A repository whose object store and refs are shared by worktrees
    standing for submodules.  It is either a bare repository or a
    working copy.
    """

    def __init__(self, path):
        self.path = os.path.realpath(path)
        self.git = Git(self.path)
        self.common_dir_cache = None

    def exists(self):
        return os.path.isdir(self.path) and self.git.ok(
            "rev-parse", "--git-dir")

    @classmethod
    def create(cls, path, url):
        """
        Creates a bare repository tracking `url` as `origin`.
        """
        os.makedirs(path)
        Git(path).run("init", "-q", "--bare")
        repo = cls(path)
        repo.git.run("remote", "add", "origin", url)
        repo.git.run("fetch", "origin", stream=True)
        return repo

    def common_dir(self):
        if self.common_dir_cache is None:
            self.common_dir_cache = os.path.realpath(self.git.run(
                "rev-parse", "--path-format=absolute",
                "--git-common-dir").strip())
        return self.common_dir_cache

    def has_commit(self, commit):
        return self.git.ok("cat-file", "-e", f"{commit}^{{commit}}")

    def remotes_for(self, url):
        out = self.git.value("config", "--get-regexp", r"^remote\..*\.url$")
        result = []
        for line in (out or "").splitlines():
            key, _, value = line.partition(" ")
            if RepoUrl.resolve(value, self.path) == url:
                result.append(key[len("remote."):-len(".url")])
        return result

    def upstream(self, urls):
        """
        Full name of the upstream of the branch HEAD points to, or of
        the default branch of a remote fetching one of `urls` (any
        remote when none does).  None when nothing matches.
        """
        rev = self.git.value("rev-parse", "--symbolic-full-name",
                             "HEAD@{upstream}")
        if rev:
            return rev
        remotes = []
        for url in urls:
            remotes += [r for r in self.remotes_for(url) if r not in remotes]
        if not remotes:
            remotes = (self.git.value("remote") or "").split()
        for remote in remotes:
            for branch in ("HEAD", "main", "master"):
                ref = f"refs/remotes/{remote}/{branch}"
                full = self.git.value("rev-parse", "--symbolic-full-name",
                                      ref)
                if full:
                    return full
        return None

    def tracking_refs(self, remote):
        """
        Patterns of local refs where branches of `remote` are fetched.
        """
        out = self.git.value("config", "--get-all", f"remote.{remote}.fetch")
        result = []
        for spec in (out or "").split():
            src, _, dst = spec.lstrip("+").partition(":")
            if src == "refs/heads/*" and dst.endswith("/*"):
                result.append(dst)
        return result

    def ensure_commit(self, commit, url, raw_url):
        """
        Fetches `commit` if missing, from remotes matching `url`, then
        from `raw_url` directly.
        """
        if self.has_commit(commit):
            return
        for remote in self.remotes_for(url):
            self.git.run("fetch", remote, stream=True)
            if self.has_commit(commit):
                return
        self.git.run("fetch", "--no-tags", "--no-write-fetch-head",
                     raw_url, commit, stream=True)
        if not self.has_commit(commit):
            raise GwmError(f"{self.path}: cannot fetch {commit} "
                           f"from {raw_url}")

    def worktrees(self):
        """
        Map of worktree path to checked out branch (None if detached).
        """
        out = self.git.run("worktree", "list", "--porcelain")
        result = {}
        path = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                path = os.path.realpath(line[len("worktree "):])
                result[path] = None
            elif line.startswith("branch "):
                result[path] = line[len("branch "):]
        return result

    def checkouts(self):
        """
        Working trees of this repository, bare main one excluded.
        """
        out = self.git.run("worktree", "list", "--porcelain", "-z")
        result = []
        for index, record in enumerate(out.split("\0\0")):
            fields = {}
            for item in record.split("\0"):
                key, _, value = item.partition(" ")
                if key:
                    fields[key] = value
            if "worktree" not in fields or "bare" in fields:
                continue
            branch = fields.get("branch")
            if fields.get("HEAD") == "0" * len(fields.get("HEAD", "")):
                fields["HEAD"] = None
            result.append(Checkout(
                self, os.path.realpath(fields["worktree"]),
                fields.get("HEAD"), branch, main=index == 0,
                locked="locked" in fields, prunable="prunable" in fields))
        return result

    def is_checked_out(self, branch):
        return branch in self.worktrees().values()

    def worktree_id(self, hint):
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", hint).strip("-.") or "wt"
        candidate = base
        index = 2
        while os.path.exists(
                os.path.join(self.common_dir(), "worktrees", candidate)):
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def attach_worktree(self, target, commit, wt_id, prepare):
        """
        Makes existing directory `target` a worktree of this repository
        with detached HEAD at `commit`, without touching its files.
        The index of the new worktree is empty.

        `prepare(gitdir)` is called once the worktree git directory
        exists.  On return, `target` must have no `.git` entry.

        Returns the worktree git directory.
        """
        staging = tempfile.mkdtemp(prefix="gwm-")
        try:
            temp = os.path.join(staging, wt_id)
            self.git.run("worktree", "add", "-q", "--detach",
                         "--no-checkout", temp, commit)
            gitdir = GitLink.read(temp)
            if os.path.basename(gitdir) != wt_id:
                raise GwmError(f"{gitdir}: unexpected worktree id")
            try:
                prepare(gitdir)
                shutil.move(GitLink.entry(temp), GitLink.entry(target))
            except BaseException:
                shutil.rmtree(gitdir, ignore_errors=True)
                raise
            os.rmdir(temp)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self.git.run("worktree", "repair", target)
        if GitLink.read(target) != gitdir:
            raise GwmError(f"{target}: worktree link is inconsistent")
        return gitdir

    def lock(self, target, reason):
        self.git.run("worktree", "lock", "--reason", reason, target)

    def unlock(self, gitdir, target):
        if os.path.exists(os.path.join(gitdir, "locked")):
            self.git.run("worktree", "unlock", target)


    def worktree_entries(self):
        """
        Map of linked worktree git directory to the `.git` entry path
        it links back to.
        """
        base = os.path.join(self.common_dir(), "worktrees")
        if not os.path.isdir(base):
            return {}
        result = {}
        for name in sorted(os.listdir(base)):
            gitdir = os.path.join(base, name)
            backlink = GitDir.backlink(gitdir)
            if backlink is not None:
                result[gitdir] = backlink
        return result

    def preserve_head(self, wt_gitdir):
        """
        Saves HEAD of a worktree under `refs/gwm/removed/` when no ref
        contains it.  Returns the created ref, if any.
        """
        head = Git("/", git_dir=wt_gitdir).value(
            "rev-parse", "--verify", "-q", "HEAD")
        if head is None:
            return None
        if self.git.run("for-each-ref", "--count=1", "--contains", head,
                        "--format=%(refname)").strip():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        ref = f"refs/gwm/removed/{os.path.basename(wt_gitdir)}-{stamp}"
        self.git.run("update-ref", ref, head, "")
        return ref

    def drop_worktree(self, wt_gitdir):
        """
        Removes metadata of a linked worktree, leaving its files alone.
        Returns the ref keeping its HEAD, if one was needed.
        """
        if os.path.dirname(wt_gitdir) != os.path.join(
                self.common_dir(), "worktrees"):
            raise GwmError(f"{wt_gitdir}: not a worktree of {self.path}")
        ref = self.preserve_head(wt_gitdir)
        shutil.rmtree(wt_gitdir)
        return ref


class Relocator:
    """
    Moves a tree of submodule git directories, keeping links with
    their working trees (gitfile and core.worktree) consistent.
    """

    @classmethod
    def scan(cls, root):
        """
        Git directories under `root`, `root` included if it is one.
        """
        if GitDir.is_repository(root):
            yield root
            root = os.path.join(root, "modules")
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isdir(path):
                yield from cls.scan(path)

    @staticmethod
    def worktree_of(gitdir):
        value = Git("/").value(
            "config", "--file", os.path.join(gitdir, "config"),
            "--get", "core.worktree")
        if value is None:
            return None
        return os.path.realpath(os.path.join(gitdir, value))

    @classmethod
    def move(cls, source, destination):
        source = os.path.realpath(source)
        links = []
        for gitdir in cls.scan(source):
            links.append((os.path.relpath(gitdir, source), gitdir,
                          cls.worktree_of(gitdir)))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(source, destination)
        destination = os.path.realpath(destination)
        for relative, old, worktree in links:
            if worktree is None:
                continue
            new = os.path.normpath(os.path.join(destination, relative))
            Git("/").run(
                "config", "--file", os.path.join(new, "config"),
                "core.worktree", os.path.relpath(worktree, new))
            if (not GitLink.is_embedded(worktree)
                    and GitLink.read(worktree) == old):
                GitLink.write(worktree, new, relative=True)
