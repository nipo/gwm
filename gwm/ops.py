import os
import shutil
import sys
import time

from .config import Config
from .git import Git, GitDir, GitLink, GwmError
from .repo import Relocator, SharedRepo, Superproject
from .url import RepoUrl


class Reporter:
    def __init__(self, cwd, stream=None):
        self.cwd = cwd
        self.stream = stream or sys.stdout

    def path(self, path):
        return os.path.relpath(path, self.cwd)

    def say(self, path, message):
        print(f"{self.path(path)}: {message}", file=self.stream)

    def line(self, text):
        print(text, file=self.stream)


class Workspace:
    """
    Context shared by all operations: configuration and reporting.
    """

    def __init__(self, cwd, config=None, reporter=None):
        self.cwd = os.path.realpath(cwd)
        self.config = config or Config()
        self.reporter = reporter or Reporter(self.cwd)

    def mapping(self, sub):
        """
        (raw URL, normalized URL, mapping or None) of an initialized
        submodule.
        """
        raw = sub.url()
        url = RepoUrl.resolve(raw, sub.superproject.toplevel)
        return raw, url, self.config.lookup(url, sub.superproject.toplevel)

    def shared(self, mapping, raw_url, create):
        repo = SharedRepo(mapping.path)
        if repo.exists():
            return repo
        if not create:
            raise GwmError(
                f"{mapping.path}: shared repository for gwm.{mapping.name} "
                f"({mapping.source}) does not exist")
        self.reporter.say(mapping.path, f"cloning {raw_url}")
        return SharedRepo.create(mapping.path, raw_url)

    @staticmethod
    def is_worktree_of(gitdir, repo):
        return GitDir.common_dir(gitdir) == repo.common_dir()

    @staticmethod
    def worktree_hint(sub):
        return f"{os.path.basename(sub.superproject.toplevel)}-{sub.path}"

    @staticmethod
    def lock_reason(sub):
        return (f"gwm: submodule {sub.path} of "
                f"{sub.superproject.toplevel}")

    @staticmethod
    def backup_path(name):
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser(
            "~/.local/state")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return os.path.join(base, "gwm", "backup", f"{stamp}-{name}")

    def select(self, superproject, paths):
        subs = superproject.submodules()
        if not paths:
            return subs
        wanted = {os.path.realpath(os.path.join(self.cwd, p)) for p in paths}
        selected = [s for s in subs
                    if os.path.realpath(s.abspath) in wanted]
        found = {os.path.realpath(s.abspath) for s in selected}
        missing = wanted - found
        if missing:
            raise GwmError(
                "not submodules of "
                f"{self.reporter.path(superproject.toplevel)}: "
                + ", ".join(self.reporter.path(p) for p in sorted(missing)))
        return selected


class Updater:
    """
    Equivalent of `git submodule update --init`, where mapped
    submodules are checked out as worktrees of their shared repository.
    """

    def __init__(self, workspace, recursive, clone_missing):
        self.ws = workspace
        self.recursive = recursive
        self.clone_missing = clone_missing

    def run(self, superproject, paths=()):
        for sub in self.ws.select(superproject, paths):
            self.update(sub)

    def update(self, sub):
        report = self.ws.reporter
        if sub.init() is None:
            report.say(sub.abspath, "inactive, skipped")
            return
        mode = sub.update_mode()
        if mode == "none":
            report.say(sub.abspath, "update mode is none, skipped")
            return

        raw, url, mapping = self.ws.mapping(sub)
        repo = None
        if mapping is not None:
            repo = self.ws.shared(mapping, raw, self.clone_missing)
        gitdir = sub.gitdir()

        if repo is None or (gitdir is not None
                            and not self.ws.is_worktree_of(gitdir, repo)):
            sub.superproject.git.run(
                "submodule", "update", "--init", "--", sub.path, stream=True)
        else:
            if mode != "checkout":
                raise GwmError(
                    f"{sub.abspath}: update mode {mode} is not supported")
            repo.ensure_commit(sub.commit, url, raw)
            if gitdir is None:
                self.create(sub, repo)
                report.say(sub.abspath, f"worktree of {repo.path} "
                           f"at {sub.commit[:10]}")
            elif sub.head() != sub.commit:
                sub.git().run("checkout", "-q", "--detach", sub.commit)
                report.say(sub.abspath, f"checked out {sub.commit[:10]}")

        if self.recursive and sub.is_populated():
            self.run(Superproject(sub.abspath))

    def create(self, sub, repo):
        os.makedirs(sub.abspath, exist_ok=True)
        if os.listdir(sub.abspath):
            raise GwmError(f"{sub.abspath}: not populated but not empty")
        wt_id = repo.worktree_id(self.ws.worktree_hint(sub))
        repo.attach_worktree(sub.abspath, sub.commit, wt_id,
                             prepare=lambda gitdir: None)
        sub.git().run("reset", "-q", "--hard")
        repo.lock(sub.abspath, self.ws.lock_reason(sub))


class Adopter:
    """
    Converts checked out submodules to worktrees of their shared
    repository, keeping working tree, index and HEAD as they are.

    Local branches and tags are pushed to the shared repository, under
    their own name when it does not exist there yet.  Otherwise, as
    well as for stash entries, they are kept under
    `refs/gwm/import/<worktree id>/`.  The previous git directory is
    moved to a backup location.
    """

    def __init__(self, workspace, recursive):
        self.ws = workspace
        self.recursive = recursive

    def run(self, superproject, paths=()):
        for sub in self.ws.select(superproject, paths):
            self.adopt(sub)

    def adopt(self, sub):
        report = self.ws.reporter
        if sub.url() is None:
            report.say(sub.abspath, "not initialized, skipped")
            return
        gitdir = sub.gitdir()
        if gitdir is None:
            report.say(sub.abspath, "not populated, skipped")
            return

        if self.recursive:
            self.run(Superproject(sub.abspath))

        raw, url, mapping = self.ws.mapping(sub)
        if mapping is None:
            return
        repo = self.ws.shared(mapping, raw, create=False)
        common = GitDir.common_dir(gitdir)
        if common == repo.common_dir():
            return
        if common is not None:
            raise GwmError(f"{sub.abspath}: worktree of {common}, "
                           f"expected {repo.path}")
        if gitdir == repo.common_dir():
            raise GwmError(f"{sub.abspath}: is the shared repository "
                           f"itself")
        self.convert(sub, gitdir, repo, url)

    @staticmethod
    def remote_urls(git):
        out = git.value("config", "--get-regexp", r"^remote\..*\.url$")
        return [line.partition(" ")[2] for line in (out or "").splitlines()]

    def convert(self, sub, gitdir, repo, url):
        report = self.ws.reporter
        GitDir.check_movable(gitdir)
        worktrees = os.path.join(gitdir, "worktrees")
        if os.path.isdir(worktrees) and os.listdir(worktrees):
            raise GwmError(f"{gitdir}: has linked worktrees")

        old = sub.git()
        head = sub.head()
        if head is None:
            raise GwmError(f"{sub.abspath}: HEAD is unborn")
        branch = sub.branch()

        wt_id = repo.worktree_id(self.ws.worktree_hint(sub))
        prefix = f"refs/gwm/import/{wt_id}"

        refspecs = [f"+refs/heads/*:{prefix}/heads/*",
                    f"+refs/tags/*:{prefix}/tags/*",
                    f"+{head}:{prefix}/HEAD"]
        if old.ok("rev-parse", "-q", "--verify", "refs/stash"):
            stashes = old.run("log", "-g", "--format=%H",
                              "refs/stash").split()
            for index, commit in enumerate(stashes):
                refspecs.append(f"+{commit}:{prefix}/stash/{index}")
        old.run("push", "-q", "--no-verify", repo.path, *refspecs)

        promoted, kept = self.promote(repo, prefix)

        attach_branch = None
        if branch is not None:
            if (repo.git.value("rev-parse", "-q", "--verify", branch) == head
                    and not repo.is_checked_out(branch)):
                attach_branch = branch
            else:
                report.say(sub.abspath,
                           f"{branch} cannot be checked out here, "
                           f"HEAD detached")

        lost_remotes = [
            u for u in self.remote_urls(old)
            if not repo.remotes_for(RepoUrl.resolve(u, sub.abspath))]

        backup = self.ws.backup_path(wt_id)
        embedded = GitLink.is_embedded(sub.abspath)

        def prepare(wt_gitdir):
            index = os.path.join(gitdir, "index")
            if os.path.exists(index):
                shutil.copy2(index, os.path.join(wt_gitdir, "index"))
            modules = os.path.join(gitdir, "modules")
            if os.path.isdir(modules):
                Relocator.move(modules, os.path.join(wt_gitdir, "modules"))
            if not embedded:
                os.remove(GitLink.entry(sub.abspath))
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.move(gitdir, backup)

        repo.attach_worktree(sub.abspath, head, wt_id, prepare)
        git = sub.git()
        if attach_branch is not None:
            git.run("symbolic-ref", "HEAD", attach_branch)
        git.run("update-index", "-q", "--refresh", check=False)
        repo.lock(sub.abspath, self.ws.lock_reason(sub))
        repo.git.run("update-ref", "-d", f"{prefix}/HEAD")
        if sub.head() != head:
            raise GwmError(f"{sub.abspath}: HEAD moved during adoption")

        report.say(sub.abspath, f"now a worktree of {repo.path}")
        for ref in promoted:
            report.say(sub.abspath, f"  created {ref}")
        for ref in kept:
            report.say(sub.abspath, f"  kept {ref}")
        for u in lost_remotes:
            report.say(sub.abspath, f"  remote {u} not in shared repository")
        report.say(sub.abspath, f"  previous git directory in {backup}")

    @staticmethod
    def promote(repo, prefix):
        """
        Moves imported branches and tags to their plain name when
        possible.  Returns (promoted, kept) lists of refs.
        """
        out = repo.git.run("for-each-ref",
                           "--format=%(refname) %(objectname)",
                           f"{prefix}/heads/", f"{prefix}/tags/")
        promoted = []
        kept = []
        for line in out.splitlines():
            ref, _, commit = line.partition(" ")
            name = "refs/" + ref[len(prefix) + 1:]
            existing = repo.git.value("rev-parse", "-q", "--verify", name)
            if existing is None:
                repo.git.run("update-ref", name, commit, "")
                promoted.append(name)
            elif existing != commit:
                kept.append(ref)
                continue
            repo.git.run("update-ref", "-d", ref)
        kept += repo.git.run("for-each-ref", "--format=%(refname)",
                             f"{prefix}/stash/").split()
        return promoted, kept


class Detacher:
    """
    Converts a submodule worktree to a standalone repository, then
    absorbs it in the superproject's git directory unless asked to keep
    it embedded.
    """

    def __init__(self, workspace, absorb):
        self.ws = workspace
        self.absorb = absorb

    def run(self, superproject, paths):
        if not paths:
            raise GwmError("detach needs explicit submodule paths")
        for sub in self.ws.select(superproject, paths):
            self.detach(sub)

    def detach(self, sub):
        gitdir = sub.gitdir()
        common = GitDir.common_dir(gitdir) if gitdir else None
        if common is None:
            raise GwmError(f"{sub.abspath}: not a worktree")
        raw = sub.url()
        if raw is None:
            raise GwmError(f"{sub.abspath}: not initialized")
        GitDir.check_movable(gitdir)
        if self.absorb:
            modules = os.path.join(sub.superproject.gitdir(), "modules",
                                   sub.name)
            if os.path.exists(modules):
                raise GwmError(
                    f"{modules} exists, remove it or keep embedded")
        new = os.path.join(sub.abspath, ".git.gwm-new")
        if os.path.exists(new):
            raise GwmError(f"{new} exists")

        repo = SharedRepo(common)
        url = RepoUrl.resolve(raw, sub.superproject.toplevel)
        head = sub.head()
        branch = sub.branch()
        git = sub.git()

        Git(sub.abspath).run("init", "-q", "--bare", new)
        ng = Git(sub.abspath, git_dir=new)
        ng.run("remote", "add", "origin", raw)
        refspecs = [f"+{head}:refs/gwm/head"]
        if branch is not None:
            refspecs.append(f"+{branch}:{branch}")
        for remote in repo.remotes_for(url):
            tracking = repo.tracking_refs(remote)
            if tracking:
                refspecs.append(f"+{tracking[0]}:refs/remotes/origin/*")
                break
        git.run("push", "-q", "--no-verify", new, *refspecs)
        ng.run("config", "core.bare", "false")
        if branch is not None:
            ng.run("symbolic-ref", "HEAD", branch)
        else:
            ng.run("update-ref", "--no-deref", "HEAD", head)
        ng.run("update-ref", "-d", "refs/gwm/head")
        index = os.path.join(gitdir, "index")
        if os.path.exists(index):
            shutil.copy2(index, os.path.join(new, "index"))

        repo.unlock(gitdir, sub.abspath)
        entry = GitLink.entry(sub.abspath)
        os.remove(entry)
        os.rename(new, entry)
        modules = os.path.join(gitdir, "modules")
        if os.path.isdir(modules):
            Relocator.move(modules, os.path.join(entry, "modules"))
        shutil.rmtree(gitdir)
        if sub.head() != head:
            raise GwmError(f"{sub.abspath}: HEAD moved during detach")

        if self.absorb:
            sub.superproject.git.run(
                "submodule", "absorbgitdirs", "--", sub.path)
        self.ws.reporter.say(sub.abspath, f"detached from {repo.path}")


class Inspector:
    def __init__(self, workspace, recursive):
        self.ws = workspace
        self.recursive = recursive

    def run(self, superproject, paths=()):
        for sub in self.ws.select(superproject, paths):
            self.inspect(sub)

    def describe(self, sub):
        """
        (state, notes) of a submodule.
        """
        if sub.url() is None:
            return "uninitialized", []
        raw, url, mapping = self.ws.mapping(sub)
        notes = []
        expected = None
        if mapping is not None:
            repo = SharedRepo(mapping.path)
            if repo.exists():
                expected = repo.common_dir()
                notes.append(f"mapped to {mapping.path}")
            else:
                notes.append(f"mapped to missing {mapping.path}")
        gitdir = sub.gitdir()
        if gitdir is None:
            return "unpopulated", notes
        common = GitDir.common_dir(gitdir)
        if common is None:
            state = "embedded" if GitLink.is_embedded(sub.abspath) \
                else "absorbed"
            if expected is not None:
                notes.append("adoptable")
            return state, notes
        if expected is not None and common != expected:
            notes.append(f"worktree of {common}")
        elif expected is None:
            notes.append(f"worktree of {common} (unmapped)")
        backlink = os.path.join(gitdir, "gitdir")
        if (not os.path.isfile(backlink)
                or os.path.realpath(open(backlink).read().strip())
                != os.path.realpath(GitLink.entry(sub.abspath))):
            notes.append("broken backlink")
        if not os.path.exists(os.path.join(gitdir, "locked")):
            notes.append("unlocked")
        return "worktree", notes

    def inspect(self, sub):
        state, notes = self.describe(sub)
        head = sub.head() if sub.is_populated() else None
        if head is None:
            flag, shown = "-", sub.commit
        else:
            flag = " " if head == sub.commit else "+"
            shown = head
            if sub.is_dirty():
                notes.insert(0, "dirty")
        branch = sub.branch() if head is not None else None
        if branch is not None:
            notes.insert(0, branch[len("refs/heads/"):])
        extra = f" ({', '.join(notes)})" if notes else ""
        self.ws.reporter.line(
            f"{flag}{shown[:10]} {self.ws.reporter.path(sub.abspath)} "
            f"{state}{extra}")
        if self.recursive and head is not None:
            self.run(Superproject(sub.abspath))
