import os

from .git import Git, GwmError
from .url import RepoUrl


class Mapping:
    """
    Association of repository URLs to a shared local repository.
    """

    def __init__(self, name, path, urls, source):
        self.name = name
        self.path = path
        self.urls = urls
        self.source = source

    def __repr__(self):
        return f"Mapping({self.name!r}, {self.path!r}, from {self.source})"


class ConfigFile:
    """
    A gwm configuration file, in git-config syntax:

        [gwm "nsl"]
            url = git@github.com:nipo/nsl.git
            url = https://github.com/nipo/nsl
            path = ~/projects/nsl

    Relative paths and URLs are resolved from the file's directory.
    """

    def __init__(self, path):
        self.path = path
        self.directory = os.path.dirname(path)
        self.mappings = {}
        self.__load()

    def __load(self):
        out = Git(self.directory).run(
            "config", "--file", self.path, "--includes", "--null", "--list")
        entries = {}
        for item in out.split("\0"):
            if not item:
                continue
            key, _, value = item.partition("\n")
            section, _, rest = key.partition(".")
            if section != "gwm":
                continue
            subsection, dot, variable = rest.rpartition(".")
            if not dot:
                raise GwmError(f"{self.path}: unknown option gwm.{rest}")
            entry = entries.setdefault(subsection, {"url": [], "path": []})
            if variable not in entry:
                raise GwmError(f"{self.path}: unknown option {key}")
            entry[variable].append(value)

        for name, entry in entries.items():
            if len(entry["path"]) != 1:
                raise GwmError(
                    f"{self.path}: gwm.{name} needs exactly one path")
            if not entry["url"]:
                raise GwmError(f"{self.path}: gwm.{name} has no url")
            path = os.path.realpath(os.path.join(
                self.directory, os.path.expanduser(entry["path"][0])))
            urls = [RepoUrl.resolve(u, self.directory) for u in entry["url"]]
            mapping = Mapping(name, path, urls, self.path)
            for url in urls:
                other = self.mappings.get(url)
                if other is not None and other is not mapping:
                    raise GwmError(
                        f"{self.path}: {url.key} is mapped by both "
                        f"gwm.{other.name} and gwm.{name}")
                self.mappings[url] = mapping


class Config:
    """
    Hierarchical configuration.  Files named `.gwm` are looked up from
    a directory up to the root, then the global file in
    `$XDG_CONFIG_HOME/gwm/config`.  For a given URL, the nearest file
    mapping it wins.
    """

    FILE_NAME = ".gwm"

    def __init__(self):
        self.files = {}

    @staticmethod
    def global_path():
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser(
            "~/.config")
        return os.path.join(base, "gwm", "config")

    def file(self, path):
        if path not in self.files:
            self.files[path] = ConfigFile(path)
        return self.files[path]

    def chain(self, directory):
        """
        Configuration files applying to `directory`, nearest first.
        """
        result = []
        directory = os.path.realpath(directory)
        while True:
            candidate = os.path.join(directory, self.FILE_NAME)
            if os.path.isfile(candidate):
                result.append(self.file(candidate))
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
        global_path = self.global_path()
        if os.path.isfile(global_path):
            result.append(self.file(global_path))
        return result

    def lookup(self, url, directory):
        for f in self.chain(directory):
            mapping = f.mappings.get(url)
            if mapping is not None:
                return mapping
        return None

    def mappings(self, directory):
        """
        Mappings visible from `directory`, by name.  The nearest file
        defining a name wins.
        """
        result = {}
        for f in self.chain(directory):
            for mapping in f.mappings.values():
                result.setdefault(mapping.name, mapping)
        return result
