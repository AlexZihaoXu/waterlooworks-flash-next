"""Generate/check the public OpenAPI artifact without opening a database or login."""

import argparse
import json
from pathlib import Path

from waterlooworks_flash_next.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not args.path.exists() or json.loads(
            args.path.read_text(encoding="utf-8")
        ) != json.loads(content):
            raise SystemExit("OpenAPI artifact is out of date; regenerate it.")
    else:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
