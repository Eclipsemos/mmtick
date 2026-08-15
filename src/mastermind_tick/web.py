"""Command-line entrypoint for the paper trading service."""

from __future__ import annotations

import argparse

import uvicorn

from mastermind_tick.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the mastermind:tick paper console")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8100, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    if args.reload:
        raise SystemExit("--reload is unavailable in the research-only service")
    uvicorn.run(create_app(start_engine=False), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
