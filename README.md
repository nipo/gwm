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

- `git wm remove [--force] paths`: `git submodule deinit` equivalent.
  Worktree submodules have their files and worktree metadata removed;
  their HEAD is kept under `refs/gwm/removed/` when no ref contains
  it.  Worktrees nested in removed submodules are removed as well.

- `git wm repair [--prune] [paths]`: restores links between submodule
  worktrees and shared repositories after a project or a shared
  repository moved (update the configuration first in the latter
  case), or after `git mv` of a submodule.  Relocks unlocked worktree
  submodules.  Reports worktrees of known shared repositories whose
  checkout in the project is gone (e.g. after a native `git submodule
  deinit`); `--prune` drops their metadata.

- `git wm status [paths]`: shows the state of submodules.

- `git wm heads [-v] [--base rev] [names]`: shows checkouts of shared
  repositories (all mappings visible from the current directory, or
  the named ones), grouped by HEAD.  Each head is compared to `--base`,
  or to the upstream of the shared repository branch, or to the
  default branch of a remote fetching a mapped URL.  Heads contained in
  another one are listed below it; for the others, changes not found
  in the base nor in another head are counted, comparing patch-ids so
  rebased copies match.  `-v` lists them.  Checkouts are flagged when
  dirty, on a branch, or when their superproject does not record their
  HEAD in its HEAD commit.

- `git wm integrate [--onto rev] [-b branch] [--include-branches]
  [--no-fetch] name`: rebases heads of checkouts of a shared repository
  into a single history on top of `--onto` (default: the upstream used
  by `heads`, fetched first), and points `branch` (default:
  `gwm/integration`) to it.  Checkouts on a branch are left out unless
  `--include-branches` is given; the current value of `branch` is
  integrated as well.  A tip containing the base is kept as is, the
  others are rebased over it, dropping changes already applied.
  Rebases happen in a scratch worktree, `gwm-integrate` in the common
  git directory of the shared repository.  On conflict, resolve there
  and run `git wm integrate --continue name`, or drop everything with
  `--abort`.  Changes of a head without an equivalent in the result
  (e.g. skipped during a rebase) are listed at the end.

- `git wm propagate [--rev rev] [--in dir]... [--include-branches]
  [-m] [-f] [-n] name`: moves checkouts of a shared repository (those
  below `--in` directories when given) to `rev` (default:
  `gwm/integration`), as detached HEADs.  Checkouts on a branch are
  skipped unless `--include-branches` is given, which resets the branch.
  Dirty checkouts are skipped unless `-m` merges local changes.
  Checkouts with changes not found in `rev` (compared by patch-id) are
  skipped unless `-f` is given.  Worktrees that are not submodules are
  left alone.  Old heads not contained in `rev` are kept under
  `refs/gwm/propagate/`, and flagged when the upstream branch of their
  superproject records them.  `-n` only shows what would be done.

- `git wm bump [--in dir]... [-c] [-m message] [--log]
  [--allow-detached] [-n] name`: stages HEAD of checkouts of a shared
  repository (those below `--in` directories when given) as the gitlink
  of their superproject, leaving the rest of its index alone.  With
  `-c`, commits each gitlink alone, as `<path>: Update to <commit>` by
  default, or with `-m` message; `--log` lists picked up commits.
  Superprojects with a detached HEAD are skipped unless
  `--allow-detached` is given or they are themselves checkouts of a
  shared repository.  Heads not on any remote of the shared
  repository are flagged, as are superprojects that are themselves
  checkouts of a mapped repository.  `-n` only shows what would be
  done.

- `git wm resolve urls`: shows which mapping applies to URLs.

## Caveats

Native `git submodule deinit` and `git submodule absorbgitdirs` do not
handle worktree submodules.  Use `git wm remove` and `git wm detach`.

Copying a project duplicates the links to worktree metadata of the
original one.  `git wm repair` refuses to steal them.
