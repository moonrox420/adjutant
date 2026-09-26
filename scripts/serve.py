"""Run one HTTP service from a private local configuration file."""

import argparse
import json
import os
from pathlib import Path

import uvicorn

from adjutant.api import create_app
from adjutant.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    config = json.loads(Path(os.environ["ADJUTANT_RUNNER_CONFIG"]).read_text(encoding="utf-8"))
    settings = Settings(**config)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
