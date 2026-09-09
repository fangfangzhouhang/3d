"""开发图与留出图冻结名单。

调参只看 develop。holdout 只在改完后看一次，不能回头为了分数去改参数。
不要再从网上临时抓一批图：成像条件不同，分数不可比。
"""

from __future__ import annotations

HOLD_OUT_STEMS = frozenset({"public_002", "public_011", "M9", "M12"})


def eval_split_for(stem: str) -> str:
    return "holdout" if stem in HOLD_OUT_STEMS else "develop"
