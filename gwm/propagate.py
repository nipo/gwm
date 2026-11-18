import os
import time

from .git import Git, GitDir, GitLink, GwmError
from .integrate import Integration, Integrator
from .ops import Updater
from .repo import SharedRepo, Superproject


class Selection:
    """
    Checkouts of a shared repository, scratch worktree excluded,
    restricted to those below given directories when any.
    """

    def __init__(self, workspace, name, within=()):
        self.ws = workspace
        mapping = workspace.mappings([name])[0]
        self.name = name
        self.repo = SharedRepo(mapping.path)
        if not self.repo.exists():
            raise GwmError(f"{mapping.path}: shared repository for "
                           f"gwm.{name} does not exist")
        self.within = [os.path.realpath(os.path.join(workspace.cwd, d))
                       for d in within]

    def selected(self, path):
        if not self.within:
            return True
        return any(path == d or path.startswith(d + os.sep)
                   for d in self.within)

    def checkouts(self):
        scratch = Integration.worktree_path(self.repo)
        result = [c for c in self.repo.checkouts()
                  if c.path != scratch and self.selected(c.path)]
        if self.within and not result:
            raise GwmError(f"{self.name}: no checkout below "
                           + ", ".join(self.ws.reporter.short(d)
                                       for d in self.within))
        return result


class Propagator:
    """
    Moves checkouts of a shared repository to a revision, typically
    the result of an integration.
    """

    def __init__(self, workspace, rev=None, within=(), include_branches=False,
                 merge=False, force=False, dry_run=False):
        self.ws = workspace
        self.rev = rev or "refs/heads/gwm/integration"
        self.within = within
        self.include_branches = include_branches
        self.merge = merge
        self.force = force
        self.dry_run = dry_run

    def run(self, name):
        selection = Selection(self.ws, name, self.within)
        repo = selection.repo
        if Integration.load(repo) is not None:
            raise GwmError(f"{name}: integration in progress, use "
                           f"`git wm integrate --continue` or --abort")
        target = repo.git.value("rev-parse", "-q", "--verify",
                                f"{self.rev}^{{commit}}")
        if target is None:
            raise GwmError(f"{name}: unknown revision {self.rev}")
        self.repo = repo
        self.target = target
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        self.ws.reporter.line(
            f"{name}: {Integrator.shown(self.rev)} {target[:10]}")
        for checkout in selection.checkouts():
            self.propagate(checkout)

    def label(self, checkout, sp):
        if sp is None:
            return self.ws.reporter.short(checkout.path)
        return (f"{self.ws.reporter.short(sp.toplevel)} "
                f"{os.path.relpath(checkout.path, sp.toplevel)}")

    def say(self, checkout, sp, message):
        self.ws.reporter.line(f"  {self.label(checkout, sp)}: {message}")

    def refusal(self, checkout, sp):
        """
        Reason not to move a checkout, or None.
        """
        if not checkout.exists():
            return "missing, skipped"
        if checkout.head == self.target:
            return "up to date"
        if sp is None and not checkout.main:
            return "not a submodule, skipped"
        if checkout.branch and not self.include_branches:
            return (f"on {Integrator.shown(checkout.branch)}, skipped")
        gitdir = checkout.gitdir()
        if any(os.path.exists(os.path.join(gitdir, n))
               for n in GitDir.IN_PROGRESS):
            return "operation in progress, skipped"
        if not self.merge and checkout.is_dirty():
            return "dirty, skipped (use --merge)"
        if checkout.head is None:
            return None
        lost = self.lost(checkout.head)
        if lost and not self.force:
            return (f"{len(lost)} changes not in {self.target[:10]}, "
                    f"skipped (use --force)")
        return None

    def lost(self, head):
        out = self.repo.git.run("cherry", self.target, head)
        return [l[2:] for l in out.splitlines() if l.startswith("+")]

    def published(self, checkout, sp):
        """
        Upstream branch of the superproject recording `checkout` HEAD,
        if any.
        """
        if sp is None:
            return None
        upstream = sp.git.value("rev-parse", "--abbrev-ref",
                                "HEAD@{upstream}")
        if upstream is None:
            return None
        path = os.path.relpath(checkout.path, sp.toplevel)
        if sp.git.value("rev-parse", "-q", "--verify",
                        f"{upstream}:{path}") == checkout.head:
            return upstream
        return None

    def propagate(self, checkout):
        sp = checkout.superproject() if checkout.exists() else None
        reason = self.refusal(checkout, sp)
        if reason is not None:
            self.say(checkout, sp, reason)
            return
        old = checkout.head
        rewritten = old is not None and not self.repo.git.ok(
            "merge-base", "--is-ancestor", old, self.target)
        notes = []
        if old is None:
            notes.append("unborn")
        elif rewritten:
            notes.append("rewritten")
            upstream = self.published(checkout, sp)
            if upstream is not None:
                notes.append(f"recorded in {upstream}")
        else:
            notes.append("fast-forward")
        if self.dry_run:
            self.say(checkout, sp, f"{old[:10] if old else '-'} -> "
                     f"{self.target[:10]} would move ({', '.join(notes)})")
            self.update_nested(checkout, old)
            return
        if rewritten:
            ref = (f"refs/gwm/propagate/{self.stamp}/"
                   f"{checkout.worktree_id()}")
            self.repo.git.run("update-ref", ref, old, "")
            notes.append(f"old head kept as {ref}")
        git = checkout.git()
        if checkout.branch:
            git.run("reset", "-q", "--merge" if self.merge else "--keep",
                    self.target)
        else:
            git.run("checkout", "-q", *(["--merge"] if self.merge else []),
                    "--detach", self.target)
        self.say(checkout, sp, f"{old[:10] if old else '-'} -> "
                 f"{self.target[:10]} ({', '.join(notes)})")
        self.update_nested(checkout, old)

    def nested_changes(self, checkout, old):
        """
        (path, old commit or None, new commit) of gitlinks changed
        between `old` and the target.
        """
        out = checkout.git().run("diff-tree", "-r", "-z", "--no-renames",
                                 old, self.target)
        items = out.split("\0")
        result = []
        for meta, path in zip(items[0::2], items[1::2]):
            old_mode, new_mode, old_sha, new_sha, _ = meta[1:].split(" ")
            if new_mode != "160000":
                continue
            result.append((path, old_sha if old_mode == "160000" else None,
                           new_sha))
        return result

    def update_nested(self, checkout, old):
        """
        Checks out submodules of a moved checkout whose gitlink changed,
        when they were at the previous gitlink (or are new).  Others are
        reported and left alone.
        """
        if old is None:
            return
        changes = self.nested_changes(checkout, old)
        if not changes:
            return
        sp = Superproject(checkout.path)
        subs = {s.path: s for s in sp.submodules()} if not self.dry_run \
            else {}
        report = self.ws.reporter
        paths = []
        for path, before, after in changes:
            abspath = os.path.join(checkout.path, path)
            populated = GitLink.read(abspath) is not None
            head = Git(abspath).value("rev-parse", "HEAD") \
                if populated else None
            if head == after:
                continue
            if populated and head != before:
                report.line(f"    {path}: at {head[:10]}, not at "
                            f"{(before or '-')[:10]}, left alone")
            elif not populated and before is not None:
                continue
            elif self.dry_run:
                report.line(f"    {path}: would check out {after[:10]}")
            elif path in subs:
                paths.append(subs[path].abspath)
        if not paths:
            return
        try:
            Updater(self.ws, recursive=True, clone_missing=False).run(
                sp, paths)
        except GwmError as e:
            report.line(f"    nested update failed: {e}")
