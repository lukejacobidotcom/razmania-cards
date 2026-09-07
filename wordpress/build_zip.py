#!/usr/bin/env python3
"""
Zip a WordPress plugin folder the way WordPress needs it.

    python wordpress/build_zip.py razmania-exhibitors
    -> wordpress/dist/razmania-exhibitors.zip

Why not Compress-Archive: PowerShell writes backslash entry paths, and the
plugin then lands on the server as one file literally named
"razmania-exhibitors\\razmania-exhibitors.php" (see DEPLOY.md, "Two things
that bit"). Python's zipfile writes forward slashes.
"""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: build_zip.py <plugin-folder-name>")
    name = sys.argv[1].strip("/\\")
    src = os.path.join(HERE, name)
    if not os.path.isfile(os.path.join(src, name + ".php")):
        sys.exit(f"{src} is not a plugin folder (no {name}.php)")
    dist = os.path.join(HERE, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, name + ".zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in sorted(files):
                if f in SKIP_FILES:
                    continue
                full = os.path.join(root, f)
                arc = name + "/" + os.path.relpath(full, src).replace(os.sep, "/")
                z.write(full, arc)
                n += 1
    print(f"{out}: {n} files, {os.path.getsize(out):,} bytes")


if __name__ == "__main__":
    main()
