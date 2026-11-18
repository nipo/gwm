from .bump import Bumper
from .git import GwmError
from .heads import HeadsInspector
from .integrate import Integration, Integrator
from .propagate import Propagator, Selection


class Syncer:
    """
    Integrates, propagates and bumps shared repositories, then the
    shared repositories containing their checkouts, and so on up.

    Mappings are processed inner first.  Only checkouts moved to the
    integration result are recorded, so that containing repositories
    integrate cleanly.  Each step is idempotent, so after a conflict,
    running again resumes where it stopped.
    """

    def __init__(self, workspace, within=(), cascade=True,
                 include_branches=False, fetch=True, log=False,
                 dry_run=False, rerun=None):
        self.ws = workspace
        self.within = within
        self.cascade = cascade
        self.include_branches = include_branches
        self.fetch = fetch
        self.log = log
        self.dry_run = dry_run
        self.rerun = rerun or "git wm sync"

    def containers(self, name):
        """
        Mappings whose checkouts contain checkouts of `name`.
        """
        result = set()
        for checkout in Selection(self.ws, name).checkouts():
            if not checkout.exists():
                continue
            sp = checkout.superproject()
            if sp is None:
                continue
            owner = self.ws.owner(sp.toplevel)
            if owner is not None and owner != name:
                result.add(owner)
        return result

    def plan(self, names):
        """
        Mappings to process, inner first.
        """
        edges = {}
        todo = list(dict.fromkeys(names))
        while todo:
            name = todo.pop()
            if name in edges:
                continue
            edges[name] = self.containers(name) if self.cascade else set()
            todo += [n for n in edges[name] if n not in edges]
        if not self.cascade:
            return list(dict.fromkeys(names))
        inner = {n: {m for m in edges if n in edges[m]} for n in edges}
        order = []
        ready = sorted(n for n in edges if not inner[n])
        while ready:
            name = ready.pop(0)
            order.append(name)
            for outer in sorted(edges[name]):
                inner[outer].discard(name)
                if not inner[outer] and outer not in order \
                        and outer not in ready:
                    ready.append(outer)
        if len(order) != len(edges):
            cycle = sorted(set(edges) - set(order))
            raise GwmError(f"checkouts contain each other: "
                           f"{', '.join(cycle)}")
        return order

    def run(self, names):
        self.ws.mappings(names)
        order = self.plan(names)
        report = self.ws.reporter
        report.line(f"sync order: {', '.join(order)}")
        if self.dry_run:
            HeadsInspector(self.ws).run(order)
            return
        for name in order:
            report.line(f"== {name}")
            try:
                self.sync(name)
            except GwmError as e:
                raise GwmError(f"{e}\nthen run `{self.rerun}` again") \
                    from None

    def sync(self, name):
        integrator = Integrator(self.ws, include_branches=self.include_branches,
                                fetch=self.fetch)
        repo = Selection(self.ws, name).repo
        if Integration.load(repo) is not None:
            integrator.resume(name)
        else:
            integrator.start(name)
        Propagator(self.ws, within=self.within,
                   include_branches=self.include_branches).run(name)
        target = repo.git.run("rev-parse", "refs/heads/gwm/integration")
        Bumper(self.ws, within=self.within, commit=True, log=self.log,
               target=target.strip()).run(name)
