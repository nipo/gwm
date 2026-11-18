import os

from .git import GitDir
from .propagate import Selection


class Bumper:
    """
    Records HEAD of checkouts of a shared repository in their
    superprojects: stages the gitlink, and commits it alone on request.

    Committing on a detached superproject HEAD is refused, unless the
    superproject is itself a checkout of a shared repository: detached
    is their normal state, and integration collects such commits.
    """

    def __init__(self, workspace, within=(), commit=False, message=None,
                 log=False, allow_detached=False, dry_run=False):
        self.ws = workspace
        self.within = within
        self.commit = commit
        self.message = message
        self.log = log
        self.allow_detached = allow_detached
        self.dry_run = dry_run

    def run(self, name):
        selection = Selection(self.ws, name, self.within)
        self.repo = selection.repo
        self.ws.reporter.line(
            f"{name}: {self.ws.reporter.short(self.repo.path)}")
        for checkout in selection.checkouts():
            self.bump(checkout)

    def say(self, checkout, sp, message):
        if sp is None:
            where = self.ws.reporter.short(checkout.path)
        else:
            where = (f"{self.ws.reporter.short(sp.toplevel)} "
                     f"{os.path.relpath(checkout.path, sp.toplevel)}")
        self.ws.reporter.line(f"  {where}: {message}")

    def refusal(self, checkout, sp):
        if not checkout.exists():
            return "missing, skipped"
        if checkout.main or sp is None:
            return "not a submodule, skipped"
        if checkout.head is None:
            return "no HEAD, skipped"
        if not self.commit:
            return None
        if any(os.path.exists(os.path.join(sp.gitdir(), n))
               for n in GitDir.IN_PROGRESS):
            return "operation in progress in superproject, skipped"
        if (not self.allow_detached
                and sp.git.value("symbolic-ref", "-q", "HEAD") is None
                and self.ws.owner(sp.toplevel) is None):
            return "superproject HEAD is detached, skipped"
        return None

    def bump(self, checkout):
        sp = checkout.superproject() if checkout.exists() else None
        reason = self.refusal(checkout, sp)
        if reason is not None:
            self.say(checkout, sp, reason)
            return
        state = checkout.recorded(sp)
        if state == "committed":
            self.say(checkout, sp, "up to date")
            return
        if state == "staged" and not self.commit:
            self.say(checkout, sp, "already staged")
            return

        path = os.path.relpath(checkout.path, sp.toplevel)
        old = sp.git.value("rev-parse", "-q", "--verify", f"HEAD:{path}")
        notes = [self.change(old, checkout.head)]
        if not self.repo.git.run("for-each-ref", "--count=1", "--contains",
                                 checkout.head, "refs/remotes/").strip():
            notes.append("not on any remote")
        owner = self.ws.owner(sp.toplevel)
        if owner is not None:
            notes.append(f"superproject is a checkout of {owner}")
        shown = f"{old[:10] if old else '-'} -> {checkout.head[:10]}"
        verb, done = ("commit", "committed") if self.commit \
            else ("stage", "staged")
        if self.dry_run:
            self.say(checkout, sp, f"would {verb} {shown} "
                     f"({', '.join(notes)})")
            return

        sp.git.run("update-index", "--cacheinfo",
                   f"160000,{checkout.head},{path}")
        if self.commit:
            sp.git.run("commit", "-q", "--only", "-F", "-", "--", path,
                       input=self.commit_message(path, old, checkout.head))
            commit = sp.git.run("rev-parse", "HEAD").strip()
            notes.append(f"as {commit[:10]}")
        self.say(checkout, sp, f"{done} {shown} ({', '.join(notes)})")

    def relation(self, old, new):
        """
        (kind, count) of the move of a gitlink from `old` to `new`,
        kind being "new", "unknown", "forward", "back" or "rewritten".
        """
        git = self.repo.git
        if old is None:
            return "new", 0
        if not self.repo.has_commit(old):
            return "unknown", 0
        if git.ok("merge-base", "--is-ancestor", old, new):
            return "forward", len(git.run("rev-list", new, f"^{old}").split())
        if git.ok("merge-base", "--is-ancestor", new, old):
            return "back", len(git.run("rev-list", old, f"^{new}").split())
        return "rewritten", 0

    def change(self, old, new):
        kind, count = self.relation(old, new)
        return {
            "new": "new",
            "unknown": "previous commit unknown",
            "forward": f"{count} commits",
            "back": f"{count} commits back",
            "rewritten": "rewritten",
        }[kind]

    def commit_message(self, path, old, new):
        if self.message is not None:
            return self.message.rstrip("\n") + "\n"
        kind, count = self.relation(old, new)
        body = {
            "forward": f"Pick up {count} commits.",
            "back": f"Go back {count} commits.",
            "rewritten": "Switch to a rewritten history.",
        }.get(kind)
        message = f"{path}: Update to {new[:10]}\n"
        if body:
            message += f"\n{body}\n"
        if self.log and kind in ("forward", "rewritten"):
            log = self.repo.git.run("log", "--no-merges", "--format=%h %s",
                                    new, f"^{old}")
            if log.strip():
                message += "\n" + log
        return message.rstrip("\n") + "\n"
