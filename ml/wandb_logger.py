"""Optional Weights & Biases integration for experiment tracking.

If wandb is installed and WANDB_API_KEY is set, logs metrics, model cards,
and artifacts. If not, all calls are silent no-ops. Import and call from
train.py / uplift.py without guarding.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_enabled = False
_run: Any = None

try:
    import wandb as _wandb
except ImportError:
    _wandb = None


def init(project: str, name: str, config: dict | None = None) -> bool:
    global _enabled, _run
    if _wandb is None or not os.environ.get("WANDB_API_KEY"):
        _enabled = False
        return False
    _run = _wandb.init(project=project, name=name, config=config or {})
    _enabled = True
    return True


def log_metrics(metrics: dict, step: int | None = None) -> None:
    if not _enabled or _run is None:
        return
    _run.log(metrics, step=step)


def log_model_card(card: dict, name: str = "model_card") -> None:
    if not _enabled or _run is None:
        return
    _run.summary.update(card.get("metrics", {}).get("test", {}))
    artifact = _wandb.Artifact(name, type="model-card")
    with artifact.new_file(f"{name}.json", mode="w") as f:
        json.dump(card, f, indent=2)
    _run.log_artifact(artifact)


def log_artifact_file(path: Path, artifact_name: str, artifact_type: str = "model") -> None:
    if not _enabled or _run is None:
        return
    artifact = _wandb.Artifact(artifact_name, type=artifact_type)
    artifact.add_file(str(path))
    _run.log_artifact(artifact)


def finish() -> None:
    global _enabled, _run
    if _enabled and _run is not None:
        _run.finish()
    _enabled = False
    _run = None
