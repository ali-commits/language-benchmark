"""Installed CLI; heavy execution modules are imported only by their commands."""
import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='bench', description='Portable HTTP benchmark queues')
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate', help='Validate a versioned YAML or JSON configuration')
    validate.add_argument('config', type=Path)
    validate.add_argument('--show-effective', action='store_true')
    args = parser.parse_args(argv)
    from .engine.config import ConfigError, load_config, resolve_jobs
    try:
        config = load_config(args.config)
        if args.show_effective:
            print(json.dumps([j.model_dump(mode='json', by_alias=True) for j in resolve_jobs(config)], indent=2))
        else:
            print(f'Valid configuration: {config.name} ({len(config.jobs)} jobs)')
        return 0
    except ConfigError as exc:
        print(f'Invalid configuration: {exc}', file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
