import json
import os
import shutil

from .git import Git, GitError, GwmError
from .repo import SharedRepo


class Integration:
    """
    State of an integration of heads of a shared repository, kept in
    its common directory between runs when a rebase stops on a
    conflict.

    `current` is the integrated history so far, `pending` the tips
    still to rebase over it and `heads` all heads whose changes must be
    found in the result.
    """

    STATE = "gwm-integrate.json"
    WORKTREE = "gwm-integrate"

    def __init__(self, repo, name, base_ref, base, branch, heads, pending):
        self.repo = repo
        self.name = name
        self.base_ref = base_ref
        self.base = base
        self.branch = branch
        self.heads = heads
        self.pending = pending
        self.current = base

    @classmethod
    def state_path(cls, repo):
        return os.path.join(repo.common_dir(), cls.STATE)

    @classmethod
    def worktree_path(cls, repo):
        return os.path.join(repo.common_dir(), cls.WORKTREE)

    @classmethod
    def load(cls, repo):
        path = cls.state_path(repo)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            data = json.load(f)
        state = cls(repo, data["name"], data["base_ref"], data["base"],
                    data["branch"], data["heads"], data["pending"])
        state.current = data["current"]
        return state

    def save(self):
        data = {k: getattr(self, k) for k in (
            "name", "base_ref", "base", "branch", "heads", "pending",
            "current")}
        with open(self.state_path(self.repo), "w") as f:
            json.dump(data, f, indent=1)

    def drop(self):
        path = self.state_path(self.repo)
        if os.path.exists(path):
            os.remove(path)

    def worktree(self):
        """
        Scratch worktree where rebases happen, created on first use.
        """
        path = self.worktree_path(self.repo)
        if not os.path.isdir(path):
            self.repo.git.run("worktree", "add", "-q", "--detach", path,
                              self.current)
            self.repo.lock(path, f"gwm: integration of {self.name}")
        return Git(path)

    def remove_worktree(self):
        path = self.worktree_path(self.repo)
        if os.path.isdir(path):
            self.repo.git.run("worktree", "remove", "--force", "--force",
                              path)
        elif os.path.exists(path):
            shutil.rmtree(path)
        self.repo.git.run("worktree", "prune")

    def rebasing(self):
        path = self.worktree_path(self.repo)
        if not os.path.isdir(path):
            return False
        gitdir = Git(path).run("rev-parse", "--absolute-git-dir").strip()
        return any(os.path.exists(os.path.join(gitdir, n))
                   for n in ("rebase-merge", "rebase-apply"))


class Integrator:
    """
    Rebases heads of checkouts of a shared repository into a single
    history on top of a base, and points a branch to the result.

    Tips are taken in order: those containing the base first, so the
    first one is kept as is, then by decreasing number of commits.
    Each tip is rebased over the history integrated so far; rebase
    drops changes already there by patch-id.
    """

    REBASE = ("-c", "rebase.updateRefs=false", "-c",
              "rebase.autoSquash=false", "-c", "rebase.autoStash=false")

    def __init__(self, workspace, onto=None, branch=None,
                 include_branches=False, fetch=True):
        self.ws = workspace
        self.onto = onto
        self.branch = branch or "gwm/integration"
        self.include_branches = include_branches
        self.fetch = fetch

    def say(self, state_or_name, message):
        name = getattr(state_or_name, "name", state_or_name)
        self.ws.reporter.line(f"{name}: {message}")

    def open(self, name):
        mapping = self.ws.mappings([name])[0]
        repo = SharedRepo(mapping.path)
        if not repo.exists():
            raise GwmError(f"{mapping.path}: shared repository for "
                           f"gwm.{name} does not exist")
        return mapping, repo

    def start(self, name):
        mapping, repo = self.open(name)
        if Integration.load(repo) is not None:
            raise GwmError(f"{name}: integration in progress, use "
                           f"--continue or --abort")
        branch_ref = f"refs/heads/{self.branch}"
        holder = [p for p, b in repo.worktrees().items() if b == branch_ref]
        if holder:
            raise GwmError(f"{name}: {self.branch} is checked out in "
                           f"{self.ws.reporter.short(holder[0])}")

        base_ref = self.base_ref(name, mapping, repo)
        base = repo.git.value("rev-parse", "-q", "--verify",
                              f"{base_ref}^{{commit}}")
        if base is None:
            raise GwmError(f"{name}: unknown base {base_ref}")
        self.say(name, f"base {self.shown(base_ref)} {base[:10]}")

        heads = self.heads(name, repo)
        existing = repo.git.value("rev-parse", "-q", "--verify",
                                  f"{branch_ref}^{{commit}}")
        if existing is not None and existing not in heads:
            heads.append(existing)
        pending = self.order(repo, base, heads, existing)
        state = Integration(repo, name, base_ref, base, self.branch,
                            sorted(set(heads)), pending)
        self.proceed(state)

    def base_ref(self, name, mapping, repo):
        if self.onto is not None:
            ref = repo.git.value("rev-parse", "--symbolic-full-name",
                                 self.onto) or self.onto
        else:
            ref = repo.upstream(mapping.urls)
            if ref is None:
                raise GwmError(f"{name}: no upstream branch found, "
                               f"use --onto")
        if self.fetch and ref.startswith("refs/remotes/"):
            remote = ref[len("refs/remotes/"):].partition("/")[0]
            self.say(name, f"fetching {remote}")
            repo.git.run("fetch", "-q", remote, stream=True)
        return ref

    @staticmethod
    def shown(ref):
        return ref.removeprefix("refs/remotes/").removeprefix("refs/heads/")

    def heads(self, name, repo):
        heads = []
        for c in repo.checkouts():
            if not c.head or not c.exists():
                continue
            if c.branch and not self.include_branches:
                self.say(name, f"skipping {self.ws.reporter.short(c.path)} "
                         f"on {self.shown(c.branch)}")
                continue
            if c.head not in heads:
                heads.append(c.head)
        return heads

    @staticmethod
    def order(repo, base, heads, preferred):
        git = repo.git
        candidates = sorted(set(heads) | {base})
        tips = git.run("merge-base", "--independent", *candidates).split()
        tips = [t for t in tips if t != base]

        def key(tip):
            on_base = git.ok("merge-base", "--is-ancestor", base, tip)
            own = len(git.run("rev-list", tip, f"^{base}").split())
            return (not on_base, tip != preferred, -own, tip)
        return sorted(tips, key=key)

    def proceed(self, state):
        """
        Integrates pending tips until done or stopped by a conflict.
        """
        git = state.repo.git
        while state.pending:
            tip = state.pending[0]
            if git.ok("merge-base", "--is-ancestor", tip, state.current):
                pass
            elif git.ok("merge-base", "--is-ancestor", state.current, tip):
                self.say(state, f"{tip[:10]} taken as is")
                state.current = tip
            else:
                self.rebase(state, tip)
            state.pending.pop(0)
        self.finish(state)

    def rebase(self, state, tip):
        wt = state.worktree()
        wt.run("checkout", "-q", "--detach", tip)
        self.run_rebase(state, tip, wt, "-q", state.current)

    def run_rebase(self, state, tip, wt, *args):
        try:
            wt.run(*self.REBASE, "rebase", *args,
                   env={"GIT_EDITOR": "true"})
        except GitError as e:
            if not state.rebasing():
                raise
            state.save()
            conflicts = wt.run("diff", "--name-only",
                               "--diff-filter=U").split()
            lines = [f"{state.name}: rebase of {tip[:10]} onto "
                     f"{state.current[:10]} stopped"]
            lines += [f"  conflict in {f}" for f in conflicts]
            if not conflicts:
                lines += [f"  {l}" for l in e.stderr.strip().splitlines()]
            lines.append(
                f"resolve in {self.ws.reporter.short(wt.cwd)}, then run "
                f"`git wm integrate --continue {state.name}`")
            raise GwmError("\n".join(lines)) from None
        self.rebased(state, tip, wt)

    def rebased(self, state, tip, wt):
        git = state.repo.git
        head = wt.run("rev-parse", "HEAD").strip()
        if not git.ok("merge-base", "--is-ancestor", state.current, head):
            raise GwmError(
                f"{state.name}: {self.ws.reporter.short(wt.cwd)} is not "
                f"on top of {state.current[:10]}, was the rebase aborted? "
                f"Use `git wm integrate --abort {state.name}`")
        picked = len(git.run("rev-list", head, f"^{state.current}").split())
        own = len(git.run("rev-list", "--no-merges", tip,
                          f"^{state.current}").split())
        dropped = f", {own - picked} already applied" if own > picked else ""
        self.say(state, f"{tip[:10]} rebased, {picked} commits{dropped}")
        state.current = head

    def finish(self, state):
        git = state.repo.git
        lost = {}
        for head in state.heads:
            missing = state.repo.missing_changes(state.current, head)
            if missing:
                lost[head] = missing
        ref = f"refs/heads/{state.branch}"
        old = git.value("rev-parse", "-q", "--verify", ref)
        git.run("update-ref", "-m", "gwm: integrate", ref, state.current)
        state.remove_worktree()
        state.drop()
        ahead = len(git.run("rev-list", state.current,
                            f"^{state.base}").split())
        note = ""
        if old is not None and old != state.current:
            note = ", rewritten" if not git.ok(
                "merge-base", "--is-ancestor", old, state.current) \
                else ", fast-forward"
        self.say(state, f"{state.branch} at {state.current[:10]}, "
                 f"{ahead} commits on {self.shown(state.base_ref)}{note}")
        for head, commits in lost.items():
            self.say(state, f"changes of {head[:10]} without an equivalent "
                     f"in the result:")
            for c in commits:
                self.ws.reporter.line("  " + git.run(
                    "log", "-1", "--format=%h %s", c).strip())

    def resume(self, name):
        _, repo = self.open(name)
        state = Integration.load(repo)
        if state is None:
            raise GwmError(f"{name}: no integration in progress")
        tip = state.pending[0]
        wt = Git(Integration.worktree_path(repo))
        if state.rebasing():
            self.run_rebase(state, tip, wt, "--continue")
        else:
            self.rebased(state, tip, wt)
        state.pending.pop(0)
        self.proceed(state)

    def abort(self, name):
        _, repo = self.open(name)
        state = Integration.load(repo)
        if state is None:
            raise GwmError(f"{name}: no integration in progress")
        if state.rebasing():
            Git(Integration.worktree_path(repo)).run("rebase", "--abort",
                                                     check=False)
        state.remove_worktree()
        state.drop()
        self.say(name, "integration aborted")
