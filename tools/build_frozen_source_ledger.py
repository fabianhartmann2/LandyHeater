"""Generate a deterministic frozen-source SHA-256 ledger."""

import argparse
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def render(candidate):
    modules = candidate / "FROZEN_MODULES.txt"
    names = modules.read_text(encoding="utf-8").splitlines()
    if not names or len(names) != len(set(names)):
        raise ValueError("frozen module list is empty or contains duplicates")
    lines = []
    for name in names:
        path = ROOT / name
        if not path.is_file() or path.resolve() == modules.resolve():
            raise ValueError("frozen source does not exist: {}".format(name))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append("{}  {}".format(digest, name))
    return ("\n".join(lines) + "\n").encode("ascii")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    if candidate.parent != (ROOT / "firmware").resolve():
        raise ValueError("candidate must be one direct firmware directory")
    (candidate / "CURRENT_FROZEN_SOURCES.sha256").write_bytes(
        render(candidate)
    )


if __name__ == "__main__":
    main()
