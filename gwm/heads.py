import os

from .git import GwmError
from .integrate import Integration
from .repo import SharedRepo


class History:
    """
    Relations between heads of a shared repository and a base commit.

    Tips are heads no other head contains.  Commits of a head are
    compared by patch-id, so rebased or cherry-picked copies of a
    change count as the same change.
    """

    def __init__(self, repo, heads, base):
        self.git = repo.git
        self.heads = sorted(set(heads))
        self.base = base
        self.tips = self.independent(self.heads)
        self.own = {h: self.rev_list(h, base) for h in self.heads}
        self.behind = {h: self.count(base, h) if base else 0
                       for h in self.heads}
        self.ids = self.patch_ids()

    def rev_list(self, head, *excluded):
        args = [f"^{e}" for e in excluded if e]
        return self.git.run("rev-list", head, *args).split()

    def count(self, head, *excluded):
        return len(self.rev_list(head, *excluded))

    def independent(self, heads):
        if len(heads) < 2:
            return list(heads)
        out = self.git.run("merge-base", "--independent", *heads)
        return sorted(out.split())

    def contains(self, tip, head):
        return self.git.ok("merge-base", "--is-ancestor", head, tip)

    def container(self, head, order):
        """
        Nearest tip containing a non-tip head, the first one in `order`
        among equally near ones.
        """
        tips = [t for t in order if self.contains(t, head)]
        return min(tips, key=lambda t: self.count(t, head))

    def patch_ids(self):
        """
        Map of commit to patch-id for commits of heads and of the base
        since their common ancestor.  Merges map to themselves.
        """
        refs = self.heads + ([self.base] if self.base else [])
        stop = self.git.value("merge-base", "--octopus", *refs) \
            if len(refs) > 1 else None
        excluded = [f"^{stop}"] if stop else []
        ids = {c: c for c in self.git.run(
            "rev-list", *refs, *excluded).split()}
        if not ids:
            return ids
        diff = self.git.run(
            "log", "-p", "--no-merges", "--no-color", "--no-ext-diff",
            "--no-textconv", "--format=commit %H", *refs, *excluded)
        out = self.git.run("patch-id", "--stable", input=diff)
        for line in out.splitlines():
            pid, _, commit = line.partition(" ")
            ids[commit] = pid
        return ids

    def changes(self, head):
        return {self.ids.get(c, c) for c in self.own[head]}

    def upstream(self):
        if not self.base:
            return set()
        result = set()
        for head in self.heads:
            result |= {self.ids.get(c, c)
                       for c in self.rev_list(self.base, head)}
        return result

    def unique(self, tip):
        """
        Commits of a tip, in reverse topological order, whose change
        is neither in the base nor in another tip.
        """
        others = self.upstream()
        for t in self.tips:
            if t != tip:
                others |= self.changes(t)
        return [c for c in self.own[tip] if self.ids.get(c, c) not in others]


class HeadsInspector:
    """
    Shows checkouts of shared repositories grouped by HEAD, relative
    to the upstream branch of each repository.
    """

    def __init__(self, workspace, base=None, verbose=False):
        self.ws = workspace
        self.base = base
        self.verbose = verbose

    def run(self, names=()):
        for mapping in self.ws.mappings(names):
            repo = SharedRepo(mapping.path)
            if not repo.exists():
                if names:
                    self.ws.reporter.line(
                        f"{mapping.name}: missing "
                        f"{self.ws.reporter.short(mapping.path)}")
                continue
            self.inspect(mapping, repo, bool(names))

    def inspect(self, mapping, repo, explicit):
        report = self.ws.reporter
        scratch = Integration.worktree_path(repo)
        checkouts = [c for c in repo.checkouts()
                     if c.head and c.path != scratch]
        if not checkouts and not explicit:
            return
        base = self.base or repo.upstream(mapping.urls)
        if base is not None:
            commit = repo.git.value("rev-parse", "-q", "--verify",
                                    f"{base}^{{commit}}")
            if commit is None:
                raise GwmError(f"{repo.path}: unknown base {base}")
            shown = base.removeprefix("refs/remotes/").removeprefix(
                "refs/heads/")
            header = f"base {commit[:10]}" if commit.startswith(shown) \
                else f"base {shown} {commit[:10]}"
        else:
            commit = None
            header = "no base"
        if Integration.load(repo) is not None:
            header += ", integration in progress"
        report.line(f"{mapping.name} {self.ws.reporter.short(repo.path)} "
                    f"({header})")
        if not checkouts:
            report.line("  no checkouts")
            return

        history = History(repo, [c.head for c in checkouts], commit)
        by_head = {}
        for c in checkouts:
            by_head.setdefault(c.head, []).append(c)
        order = sorted(history.tips, key=lambda t: (
            -len(history.own[t]), by_head[t][0].path))
        nested = {t: [] for t in order}
        for head in history.heads:
            if head not in nested:
                nested[history.container(head, order)].append(head)

        for tip in order:
            unique = history.unique(tip)
            notes = []
            if len(history.tips) > 1 or history.own[tip]:
                notes.append(f"{len(unique)} unique")
            self.head_line(history, tip, notes, by_head, 1)
            if self.verbose:
                for c in unique:
                    report.line("      " + repo.git.run(
                        "log", "-1", "--format=%h %s", c).strip())
            for head in sorted(nested[tip], key=lambda h: (
                    history.count(tip, h), by_head[h][0].path)):
                self.head_line(history, head, [f"in {tip[:10]}"],
                               by_head, 2)

    def head_line(self, history, head, notes, by_head, depth):
        report = self.ws.reporter
        indent = "  " * depth
        counts = ""
        if history.base:
            counts = f" +{len(history.own[head])} -{history.behind[head]}"
        extra = f" ({', '.join(notes)})" if notes else ""
        report.line(f"{indent}{head[:10]}{counts}{extra}")
        for c in by_head[head]:
            report.line(f"{indent}    {self.checkout_line(c)}")

    def checkout_line(self, checkout):
        notes = []
        if checkout.branch:
            notes.append(checkout.branch.removeprefix("refs/heads/"))
        if not checkout.exists():
            notes.append("missing")
            return self.describe(checkout, None, notes)
        if checkout.is_dirty():
            notes.append("dirty")
        sp = checkout.superproject()
        if checkout.main:
            notes.append("shared repository")
        elif sp is None:
            notes.append("not a submodule")
        else:
            state = checkout.recorded(sp)
            if state != "committed":
                notes.append(state or "not recorded")
        return self.describe(checkout, sp, notes)

    def describe(self, checkout, sp, notes):
        if sp is not None:
            where = (f"{self.ws.reporter.short(sp.toplevel)} "
                     f"{os.path.relpath(checkout.path, sp.toplevel)}")
        else:
            where = self.ws.reporter.short(checkout.path)
        extra = f" ({', '.join(notes)})" if notes else ""
        return f"{where}{extra}"
