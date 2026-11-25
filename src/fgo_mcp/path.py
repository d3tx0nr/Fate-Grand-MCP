from pathlib import Path

PACKAGE = Path(__file__).resolve().parent

ROOT = PACKAGE.parents[1]
ASSETS = PACKAGE / "assets" 
DATA = ROOT / "data"            # everything the engine learns or downloads, and the logs
LOGS = DATA / "logs"

KEEP_RUNS = 6                    # older run folders are deleted when a new one starts

DATA.mkdir(exist_ok=True)

def prune(folder: Path, prefix: str, keep: int = KEEP_RUNS) -> None:
    """deletes all but the newest keep-1 folders named prefix-<time>, making room for the one about to start"""
    import shutil

    if not folder.exists():
        return
    runs = sorted(p for p in folder.iterdir() if p.is_dir() and p.name.startswith(prefix + "-"))
    for old in runs[:max(0, len(runs) - (keep - 1))]:
        shutil.rmtree(old, ignore_errors=True)