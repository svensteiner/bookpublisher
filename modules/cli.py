from __future__ import annotations

import argparse
import sys
from pathlib import Path

from modules.config import ConfigError, load_config
from modules.pipeline import PublisherPipeline
from modules.run_logger import RunLogger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publisher Agent for Amazon KDP nonfiction books.")
    parser.add_argument("command", choices=["scan", "qa", "round", "review", "cover", "launch", "all"])
    parser.add_argument("--input-path", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--full-review", action="store_true", help="For round: also run LLM reviews and launch assets.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        input_path = args.input_path or config.default_input_path
        logger = RunLogger(config.project_root / "logs")
        pipeline = PublisherPipeline(config, logger)
        logger.log("run_started", command=args.command, input_path=str(input_path), read_only=config.read_only)

        if args.command == "scan":
            projects = pipeline.discover(input_path)
        elif args.command == "qa":
            projects = pipeline.run_qa(input_path)
        elif args.command == "round":
            summary = pipeline.run_round(input_path, full_review=args.full_review)
            logger.log("run_completed", command=args.command, project_count=summary["project_count"])
            print(f"Round: {summary['round_id']}")
            print(f"Mode: {summary['mode']}")
            print(f"Projects: {summary['project_count']}")
            print(f"Artifacts: {config.project_root / 'artifacts'}")
            print(f"Log: {logger.path}")
            return 0
        elif args.command == "review":
            projects = pipeline.run_review(input_path)
        elif args.command == "cover":
            projects = pipeline.run_cover(input_path)
        elif args.command == "launch":
            projects = pipeline.run_launch(input_path)
        else:
            projects = pipeline.run_all(input_path)

        logger.log("run_completed", command=args.command, project_count=len(projects))
        print(f"Done. Projects: {len(projects)}")
        print(f"Artifacts: {config.project_root / 'artifacts'}")
        print(f"Log: {logger.path}")
        return 0
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
