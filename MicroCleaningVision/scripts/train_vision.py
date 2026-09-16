"""训练/对照入口。默认在终端逐步提示，只改一轮后停下。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from microcleaning.data_learning.train_entry import main

if __name__ == "__main__":
    raise SystemExit(main())
