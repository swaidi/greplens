"""CI helper: run the app's update step as the installed back end would, with a given installer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from greplens import updates  # noqa: E402

sys.frozen = True  # behave like the installed back end
sys.executable = sys.argv[1]
updates._run_installer_after_exit(Path(sys.argv[2]))
print("update helper started")
