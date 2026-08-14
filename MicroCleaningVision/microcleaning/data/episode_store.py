"""Episode 持久化（成员 C 负责）。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from microcleaning.contracts import Episode


def write_episode(episode: Episode, output_dir: str | Path) -> Path:
    """以 JSON + SHA256 保存回合；已有同名回合绝不覆盖。"""
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{episode.episode_id}.json"
    digest_output = folder / f"{episode.episode_id}.sha256"
    if output.exists() or digest_output.exists():
        raise FileExistsError(f"拒绝覆盖既有回合：{episode.episode_id}")
    payload = json.dumps(episode.as_dict(), ensure_ascii=False, indent=2) + "\n"
    temporary = folder / f".{episode.episode_id}.{uuid4().hex}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, output)
    digest_output.write_text(hashlib.sha256(payload.encode("utf-8")).hexdigest() + "\n", encoding="ascii")
    return output

