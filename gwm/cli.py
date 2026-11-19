import argparse
import os
import shlex
import sys

from .config import Config
from .git import Git, GwmError
from .bump import Bumper
from .fetch import Fetcher
from .heads import HeadsInspector
from .integrate import Integrator
from .propagate import Propagator
from .sync import Syncer
from .ops import (Adopter, Detacher, Inspector, Remover, Repairer, Updater,
                  Workspace)
from .repo import Superproject
from .url import RepoUrl


class Cli:
    @staticmethod
    def parser():
        parser = argparse.ArgumentParser(
            prog="git wm",
            description="Manage submodules as worktrees of shared "
            "repositories")
        parser.add_argument("-C", dest="directory", default=".",
                            help="run as if started in this directory")
        commands = parser.add_subparsers(dest="command", required=True)

        p = commands.add_parser(
            "update", help="initialize and check out submodules, using "
            "worktrees for mapped ones")
        p.add_argument("--no-recursive", dest="recursive",
                       action="store_false")
        p.add_argument("--clone-missing", action="store_true",
                       help="create missing shared repositories as bare "
                       "clones")
        p.add_argument("paths", nargs="*")

        p = commands.add_parser(
            "adopt", help="convert checked out submodules to worktrees "
            "of shared repositories")
        p.add_argument("--no-recursive", dest="recursive",
                       action="store_false")
        p.add_argument("paths", nargs="*")

        p = commands.add_parser(
            "detach", help="convert submodule worktrees to standalone "
            "repositories")
        p.add_argument("--embedded", dest="absorb", action="store_false",
                       help="keep the git directory inside the submodule")
        p.add_argument("paths", nargs="+")

        p = commands.add_parser(
            "remove", help="deinitialize submodules, removing worktree "
            "metadata of worktree ones")
        p.add_argument("-f", "--force", action="store_true",
                       help="discard local changes")
        p.add_argument("paths", nargs="+")

        p = commands.add_parser(
            "repair", help="fix links between submodule worktrees and "
            "shared repositories")
        p.add_argument("--no-recursive", dest="recursive",
                       action="store_false")
        p.add_argument("--prune", action="store_true",
                       help="drop metadata of worktrees whose checkout "
                       "is gone")
        p.add_argument("paths", nargs="*")

        p = commands.add_parser("status", help="show submodule states")
        p.add_argument("--no-recursive", dest="recursive",
                       action="store_false")
        p.add_argument("paths", nargs="*")

        p = commands.add_parser(
            "fetch", help="create missing shared repositories and fetch "
            "all of them")
        p.add_argument("names", nargs="*",
                       help="mapping names (default: all mappings)")

        p = commands.add_parser(
            "heads", help="show checkouts of shared repositories grouped "
            "by HEAD")
        p.add_argument("--base", help="compare heads to this revision "
                       "instead of the upstream branch")
        p.add_argument("-v", "--verbose", action="store_true",
                       help="list commits found in a single head")
        p.add_argument("names", nargs="*",
                       help="mapping names (default: all mappings)")

        p = commands.add_parser(
            "integrate", help="rebase heads of checkouts of a shared "
            "repository into a single history")
        p.add_argument("--onto", help="base revision (default: upstream "
                       "branch)")
        p.add_argument("-b", "--branch", help="branch pointing to the "
                       "result (default: gwm/integration)")
        p.add_argument("--include-branches", action="store_true",
                       help="also integrate checkouts on a branch")
        p.add_argument("--no-fetch", dest="fetch", action="store_false",
                       help="do not fetch the remote of the base")
        action = p.add_mutually_exclusive_group()
        action.add_argument("--continue", dest="resume",
                            action="store_true",
                            help="resume after resolving a conflict")
        action.add_argument("--abort", action="store_true",
                            help="drop an integration in progress")
        p.add_argument("name", help="mapping name")

        p = commands.add_parser(
            "propagate", help="move checkouts of a shared repository to "
            "a revision")
        p.add_argument("--rev", help="revision to move to (default: "
                       "gwm/integration)")
        p.add_argument("--in", dest="within", action="append", default=[],
                       metavar="DIR", help="only move checkouts below DIR "
                       "(repeatable)")
        p.add_argument("--include-branches", action="store_true",
                       help="also move checkouts on a branch, resetting "
                       "the branch")
        p.add_argument("-m", "--merge", action="store_true",
                       help="move dirty checkouts, merging local changes")
        p.add_argument("-f", "--force", action="store_true",
                       help="move checkouts whose changes are not all in "
                       "the revision")
        p.add_argument("-n", "--dry-run", action="store_true",
                       help="only show what would be done")
        p.add_argument("name", help="mapping name")

        p = commands.add_parser(
            "bump", help="record HEAD of checkouts of a shared repository "
            "in their superprojects")
        p.add_argument("--in", dest="within", action="append", default=[],
                       metavar="DIR", help="only bump checkouts below DIR "
                       "(repeatable)")
        p.add_argument("-c", "--commit", action="store_true",
                       help="commit each gitlink alone")
        p.add_argument("-m", "--message", help="commit message")
        p.add_argument("--log", action="store_true",
                       help="list picked up commits in the message")
        p.add_argument("--allow-detached", action="store_true",
                       help="commit on a detached superproject HEAD")
        p.add_argument("-n", "--dry-run", action="store_true",
                       help="only show what would be done")
        p.add_argument("name", help="mapping name")

        p = commands.add_parser(
            "sync", help="integrate, propagate and bump shared "
            "repositories, then those containing their checkouts")
        p.add_argument("--in", dest="within", action="append", default=[],
                       metavar="DIR", help="only move and bump checkouts "
                       "below DIR (repeatable)")
        p.add_argument("--no-cascade", dest="cascade", action="store_false",
                       help="only process the named shared repositories")
        p.add_argument("--include-branches", action="store_true",
                       help="also integrate and move checkouts on a branch")
        p.add_argument("--no-fetch", dest="fetch", action="store_false",
                       help="do not fetch upstream branches")
        p.add_argument("--log", action="store_true",
                       help="list picked up commits in commit messages")
        p.add_argument("-n", "--dry-run", action="store_true",
                       help="only show the order and current heads")
        p.add_argument("names", nargs="+", help="mapping names")

        p = commands.add_parser(
            "resolve", help="show the mapping applying to URLs")
        p.add_argument("urls", nargs="+")

        return parser

    @staticmethod
    def resolve(ws, urls):
        base = Git(ws.cwd).value("rev-parse", "--show-toplevel") or ws.cwd
        for raw in urls:
            url = RepoUrl.resolve(raw, base)
            mapping = ws.config.lookup(url, base)
            if mapping is None:
                ws.reporter.line(f"{url.key}: unmapped")
            else:
                ws.reporter.line(f"{url.key}: {mapping.path} "
                                 f"(gwm.{mapping.name} in {mapping.source})")

    @classmethod
    def main(cls, argv=None):
        if argv is None:
            argv = sys.argv[1:]
        args = cls.parser().parse_args(argv)
        try:
            Git.require_version(Git.MIN_VERSION, "gwm")
            ws = Workspace(os.path.abspath(args.directory), Config())
            if args.command == "resolve":
                cls.resolve(ws, args.urls)
                return 0
            if args.command == "fetch":
                Fetcher(ws).run(args.names)
                return 0
            if args.command == "heads":
                HeadsInspector(ws, args.base, args.verbose).run(args.names)
                return 0
            if args.command == "integrate":
                op = Integrator(ws, args.onto, args.branch,
                                args.include_branches, args.fetch)
                if args.resume:
                    op.resume(args.name)
                elif args.abort:
                    op.abort(args.name)
                else:
                    op.start(args.name)
                return 0
            if args.command == "sync":
                Syncer(ws, args.within, args.cascade, args.include_branches,
                       args.fetch, args.log, args.dry_run,
                       rerun=shlex.join(["git", "wm", *argv])).run(args.names)
                return 0
            if args.command == "bump":
                Bumper(ws, args.within, args.commit, args.message, args.log,
                       args.allow_detached, args.dry_run).run(args.name)
                return 0
            if args.command == "propagate":
                Propagator(ws, args.rev, args.within, args.include_branches,
                           args.merge, args.force, args.dry_run).run(
                               args.name)
                return 0
            sp = Superproject.find(ws.cwd)
            match args.command:
                case "update":
                    op = Updater(ws, args.recursive, args.clone_missing)
                case "adopt":
                    op = Adopter(ws, args.recursive)
                case "detach":
                    op = Detacher(ws, args.absorb)
                case "remove":
                    op = Remover(ws, args.force)
                case "repair":
                    op = Repairer(ws, args.recursive, args.prune)
                case "status":
                    op = Inspector(ws, args.recursive)
                case _:
                    raise AssertionError(args.command)
            op.run(sp, args.paths)
        except GwmError as e:
            print(f"git wm: {e}", file=sys.stderr)
            return 1
        return 0


def main():
    sys.exit(Cli.main())
