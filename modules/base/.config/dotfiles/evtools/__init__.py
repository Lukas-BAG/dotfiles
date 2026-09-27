"""evtools: tooling for Everything dirs (~/Main/Everything-style numbered entry dirs).

Shared pieces live in discovery.py (find every Everything dir) and
entries.py (parse "<yy><seq>[-suffix]" entry and sidecar names); cli.py
holds the subcommands behind the `everything` command on PATH.

The package deliberately isn't named "everything": discovery matches any dir
whose name contains that word, so it would list its own source dir.
"""
