import importlib.util
import json
import argparse
import os
import importlib
from pathlib import Path
import sys

DSL_FOLDER = os.path.join(os.path.dirname(__file__), '..')
TARGET_FILE_PATH = os.path.join(os.path.dirname(__file__), '..', 'dist', 'mounts.yml')

from api import API

def finish_data(data):
    os.makedirs(os.path.dirname(TARGET_FILE_PATH), exist_ok=True)
    with open(TARGET_FILE_PATH, 'w', encoding='utf-8') as fp:
        # JSON is safe subset of YAML at least with our no-number and unique keys use case.
        json.dump(data, fp, indent='  ')
        fp.write('\n')

def cmd_generate(args: argparse.Namespace):
    api = API()
    for item in os.listdir(DSL_FOLDER):
        if not item.endswith('.mount.py'):
            continue
        print('=== Executing DSL script: ' + item)
        path = Path(os.path.join(DSL_FOLDER, item))
        module_name = item[:-len('.py')]
        spec = importlib.util.spec_from_file_location(
            name=module_name, location=path,
            submodule_search_locations=[str(path.parent)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load a module from {item!r}")
        module = importlib.util.module_from_spec(spec)

        sys.modules[module_name] = module  # Some module may expect to look up themselves
        try:
            spec.loader.exec_module(module)
            define_mounts = getattr(module, "define_mounts", None)
            if callable(define_mounts):
                define_mounts(api)
                print('Successfully added mounts.')
            else:
                print('WARNING: No callable define_mounts found in this module. Skipping.')
        except Exception:
            raise
        finally:
            sys.modules.pop(module_name, None)
    finish_data(api.get_compose_data())

def cmd_clear(args: argparse.Namespace):
    api = API()
    finish_data(api.get_compose_data())

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mounthouse/script/main.py",
        description="Generate complicated mount lists as a includable Docker Compose snippet.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate",
        help="Generate an the Docker Compose snippet to mounthouse/dist/mounts.yml.",
    )
    generate.set_defaults(func=cmd_generate)

    clear = subparsers.add_parser(
        "clear",
        help="Overwrite a mounthouse/dist/mounts.yml with an empty snippet. For use before `docker compose down`, so a failed generation does not prevent stopping the services.",
    )
    clear.set_defaults(func=cmd_clear)

    return parser

def main():
    args = build_parser().parse_args()
    args.func(args)
    return 0

if __name__ == '__main__':
    SystemExit(main())
