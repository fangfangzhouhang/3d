# 恢复说明（2026-11-09 晚）

本目录于 2026-11-09 20:09–20:16 被 Muse 在游戏本远程操作中误删（原 `exp/probe-annotation`
分支未推送、本地唯一）。当晚按以下来源重建，并已提交回同名分支：

- **源码与报告（51 个文件）**：由当日 Codex 会话日志中的 apply_patch 记录与 FileChange
  快照双路重放重建，两路结果逐字节比对。
- **对账证据**：`results/stage2/summary.json` 内运行时记录的逐文件 SHA256 表 21 项中
  20 项与重建逐字节一致；`probes/comparison.py` 运行时后有一次修改，验收包副本与
  日志重放两路一致（dc1b88a614…），以该版为准。另有 38 个文件与三阶段验收包
  （mcv-stage0/1/2-verify）逐字节一致。
- **inputs 两张原图**：按 metrics.json 记录的 SHA256 在 Muse 侧存档中找回，
  哈希逐位一致（img1 d4ec9de3…、img2 591cfdad…）。
- **dataset/seed_v0 的掩码、候选、Labelme 与清单，results/ 与 logs/**：直接取自
  三阶段验收包，未改动。
- **未恢复（需重建）**：`.venv`（按 requirements.txt/setup_env.ps1 重建）、
  `weights/sam2.1_hiera_tiny.pt`（按 sources.json 的 URL 与 SHA256 7402e0d8… 重新下载）、
  `vendor/sam2`（按 sources.json 钉住的 commit 重新克隆）。
