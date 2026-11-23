from pathlib import Path

PACKAGE = Path(__file__).resolve().parent

ROOT = PACKAGE.parents[1]
ASSETS = PACKAGE / "assets" 
DATA = ROOT / "data"            # everything the engine learns or downloads, and the logs
LOGS = DATA / "logs"

DATA.mkdir(exist_ok=True)