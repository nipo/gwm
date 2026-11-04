import os
import re
import subprocess


class GwmError(Exception):
    pass


class GitError(GwmError):
    def __init__(self, command, cwd, returncode, stderr):
        self.command = command
        self.cwd = cwd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"`{' '.join(command)}` in {cwd} failed ({returncode}): "
            f"{stderr.strip()}")


class Git:
    """
    Runs git commands in a given directory, isolated from repository
    selection variables of the calling environment.
    """

    MIN_VERSION = (2, 31)

    LOCATION_VARIABLES = (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
    )

    version_cache = None

    def __init__(self, cwd, git_dir=None):
        self.cwd = cwd
        self.git_dir = git_dir

    @classmethod
    def environment(cls):
        env = dict(os.environ)
        for name in cls.LOCATION_VARIABLES:
            env.pop(name, None)
        return env

    def command(self, args):
        command = ["git"]
        if self.git_dir is not None:
            command += ["--git-dir", self.git_dir]
        return command + list(args)

    def run(self, *args, check=True, stream=False):
        """
        Runs a command and returns its stdout.  When `stream` is set,
        stderr goes to the terminal (progress of fetches, etc.).
        """
        command = self.command(args)
        proc = subprocess.run(
            command, cwd=self.cwd, env=self.environment(),
            stdout=subprocess.PIPE,
            stderr=None if stream else subprocess.PIPE,
            text=True)
        if check and proc.returncode != 0:
            raise GitError(command, self.cwd, proc.returncode,
                           proc.stderr or "")
        return proc.stdout

    def ok(self, *args):
        proc = subprocess.run(
            self.command(args), cwd=self.cwd, env=self.environment(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0

    def value(self, *args):
        """
        Single-line output of a command, or None when it fails.
        """
        command = self.command(args)
        proc = subprocess.run(
            command, cwd=self.cwd, env=self.environment(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def config_get(self, key):
        return self.value("config", "--get", key)

    @classmethod
    def version(cls):
        if cls.version_cache is None:
            out = Git(os.getcwd()).run("version")
            m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
            if m is None:
                raise GwmError(f"Cannot parse git version from {out!r}")
            cls.version_cache = tuple(int(x or 0) for x in m.groups())
        return cls.version_cache

    @classmethod
    def require_version(cls, minimum, feature):
        current = cls.version()
        if current < tuple(minimum):
            raise GwmError(
                f"{feature} requires git >= {'.'.join(map(str, minimum))}, "
                f"found {'.'.join(map(str, current))}")


class GitLink:
    """
    The `.git` entry at the root of a working tree: either a git
    directory or a gitfile pointing to one.
    """

    @staticmethod
    def entry(worktree):
        return os.path.join(worktree, ".git")

    @classmethod
    def is_embedded(cls, worktree):
        return os.path.isdir(cls.entry(worktree))

    @classmethod
    def target(cls, worktree):
        """
        Path written in the gitfile of a working tree, or None when its
        `.git` entry is not a gitfile.
        """
        entry = cls.entry(worktree)
        if not os.path.isfile(entry):
            return None
        with open(entry) as f:
            content = f.read().strip()
        if not content.startswith("gitdir:"):
            raise GwmError(f"{entry}: not a gitfile")
        return content[len("gitdir:"):].strip()

    @classmethod
    def read(cls, worktree):
        """
        Real path of the git directory of a working tree, or None when
        there is no `.git` entry.
        """
        entry = cls.entry(worktree)
        if os.path.isdir(entry):
            return os.path.realpath(entry)
        target = cls.target(worktree)
        if target is None:
            return None
        return os.path.realpath(os.path.join(worktree, target))

    @classmethod
    def write(cls, worktree, gitdir, relative):
        target = os.path.relpath(gitdir, worktree) if relative else gitdir
        with open(cls.entry(worktree), "w") as f:
            f.write(f"gitdir: {target}\n")


class GitDir:
    """
    Inspection helpers on a git directory, without running git.
    """

    IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                   "BISECT_LOG", "rebase-merge", "rebase-apply")

    @staticmethod
    def is_repository(path):
        return (os.path.isfile(os.path.join(path, "HEAD"))
                and os.path.isdir(os.path.join(path, "objects")))

    @staticmethod
    def common_dir(gitdir):
        """
        Common directory of a linked worktree git directory, or None if
        the git directory is not a linked worktree one.
        """
        path = os.path.join(gitdir, "commondir")
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return os.path.realpath(os.path.join(gitdir, f.read().strip()))

    @staticmethod
    def backlink(gitdir):
        """
        Real path of the `.git` entry a linked worktree git directory
        refers to, or None if it has no backlink.
        """
        path = os.path.join(gitdir, "gitdir")
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return os.path.realpath(os.path.join(gitdir, f.read().strip()))

    @staticmethod
    def is_locked(gitdir):
        return os.path.exists(os.path.join(gitdir, "locked"))

    @classmethod
    def check_movable(cls, gitdir):
        """
        Raises if the state in a git directory cannot be carried over by
        moving its HEAD and index around.
        """
        for name in cls.IN_PROGRESS:
            if os.path.exists(os.path.join(gitdir, name)):
                raise GwmError(
                    f"{gitdir}: operation in progress ({name}), "
                    f"finish or abort it first")
        if any(n.startswith("sharedindex.") for n in os.listdir(gitdir)):
            raise GwmError(f"{gitdir}: split index is not supported")
        if os.path.exists(os.path.join(gitdir, "info", "sparse-checkout")):
            raise GwmError(f"{gitdir}: sparse checkout is not supported")
        if os.path.exists(os.path.join(gitdir, "config.worktree")):
            raise GwmError(f"{gitdir}: per-worktree config is not supported")
