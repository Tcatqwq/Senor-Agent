import argparse
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT_DIR)
from src.local_setup import prepare_local_files

os.environ["CONFIG_PATH"] = str(prepare_local_files(ROOT_DIR))
# Keep Rich log formatting independent of the terminal that launched the server.
os.environ["COLUMNS"] = "100"

import torch

try:
    torch.multiprocessing.set_start_method("spawn")
except RuntimeError:
    pass

from frontend.app import create_app


def parse_args():
    parser = argparse.ArgumentParser(description="Senor Agent web frontend")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8080, help="Bind port")
    return parser.parse_args()


def main():
    args = parse_args()
    import uvicorn

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
