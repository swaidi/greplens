import sys

from greplens.main import run

if __name__ == "__main__":
    run(open_browser="--browser" in sys.argv)
