from .git import GwmError
from .repo import SharedRepo


class Fetcher:
    """
    Creates missing shared repositories as bare clones of the first URL
    of their mapping, and fetches existing ones from remotes matching
    a mapped URL, making sure those remotes fetch to remote-tracking
    branches.
    """

    def __init__(self, workspace):
        self.ws = workspace

    def say(self, mapping, message):
        self.ws.reporter.line(f"{mapping.name}: {message}")

    def run(self, names=()):
        failed = []
        for mapping in self.ws.mappings(names):
            try:
                self.fetch(mapping)
            except GwmError as e:
                self.say(mapping, f"error: {e}")
                failed.append(mapping.name)
        if failed:
            raise GwmError(f"failed: {', '.join(failed)}")

    def fetch(self, mapping):
        repo = SharedRepo(mapping.path)
        if not repo.exists():
            url = mapping.clone_url()
            self.say(mapping, f"cloning {url} to "
                     f"{self.ws.reporter.short(mapping.path)}")
            SharedRepo.create(mapping.path, url)
            return
        for remote in self.remotes(mapping, repo):
            if repo.enable_tracking(remote):
                self.say(mapping, f"tracking branches of {remote} in "
                         f"refs/remotes/{remote}/")
            self.say(mapping, f"fetching {remote}")
            repo.fetch(remote)

    def remotes(self, mapping, repo):
        """
        Remotes of `repo` fetching a mapped URL, adding `origin` when
        there is none.
        """
        result = []
        for url in mapping.urls:
            result += [r for r in repo.remotes_for(url) if r not in result]
        if result:
            return result
        if "origin" in repo.remotes():
            other = repo.git.value("remote", "get-url", "origin")
            raise GwmError(f"no remote fetches a mapped URL, and origin "
                           f"is {other}")
        url = mapping.clone_url()
        repo.git.run("remote", "add", "origin", url)
        self.say(mapping, f"added remote origin for {url}")
        return ["origin"]
