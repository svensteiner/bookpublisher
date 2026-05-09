from pathlib import Path
from uuid import uuid4


def runtime_dir(name: str) -> Path:
    root = Path(__file__).resolve().parents[1] / "artifacts" / "test_runtime" / f"{name}_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root
