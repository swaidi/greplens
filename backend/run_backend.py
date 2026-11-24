"""Entry point for the frozen back end (PyInstaller)."""
import multiprocessing
import sys

from greplens.main import run

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run(open_browser="--browser" in sys.argv)
