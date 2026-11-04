# gwm — git worktree manager

`git wm` manages submodules as worktrees of shared local repositories,
so that a library used by many projects is stored once per machine.

## Configuration

Mappings from repository URLs to shared repositories are written in
git-config syntax:

```
[gwm "nsl"]
    url = git@github.com:nipo/nsl.git
    url = https://github.com/nipo/nsl
    path = ~/projects/nsl
```

A shared repository is either a working copy or a bare repository.

Files named `.gwm` are looked up from the superproject top-level
directory up to the root, then `$XDG_CONFIG_HOME/gwm/config`.  The
nearest file mapping a URL wins.  Relative paths are resolved from the
file's directory.

URLs are compared regardless of scheme, user, port, trailing `.git`
and host case, after `url.<base>.insteadOf` rewriting.

Unmapped submodules are handled as plain git submodules.

## Commands

- `git wm update [--clone-missing] [paths]`: `git submodule update
  --init --recursive` equivalent.  Mapped submodules are checked out
  as detached, locked worktrees of their shared repository.
  `--clone-missing` creates missing shared repositories as bare
  repositories tracking the submodule URL.

- `git wm adopt [paths]`: converts checked out submodules (absorbed or
  embedded git directory) to worktrees, keeping working tree, index,
  HEAD and attached branch.  Branches and tags are created in the
  shared repository when missing; conflicting ones and stash entries
  are kept under `refs/gwm/import/<worktree id>/`.  The previous git
  directory is moved to `$XDG_STATE_HOME/gwm/backup/`.

- `git wm detach [--embedded] paths`: converts submodule worktrees back
  to standalone repositories, absorbed in the superproject git
  directory unless `--embedded` is given.

- `git wm status [paths]`: shows the state of submodules.

- `git wm resolve urls`: shows which mapping applies to URLs.

## Caveats

Native `git submodule deinit` and `git submodule absorbgitdirs` do not
handle worktree submodules.  Use `git wm detach` first.
