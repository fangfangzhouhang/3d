@AGENTS.md

# Claude entrypoint: MicroCleaningVision

Before work, read in order:

1. `project_state.yaml`
2. `README.md`
3. `AGENTS.md`
4. `说明文档/总流程说明/项目作战总表.md` when planning hardware or sprint work
5. This migration package's `HANDOFF.md` if present in the project
6. Only the directly relevant files under `说明文档/`

The project goal is a closed-loop microscopic-surface treatment platform:

```text
image/data → contamination measurement → target/path/action request
→ deterministic control simulation or approved hardware interface
→ post-action reinspection → Episode evidence
```

Current three-person software scope is vision and host-computer software. MCV1 firmware source lives in-repo; that is not burn/PONG evidence. Do not silently expand into mechanical design, unapproved 12V spray, or XY/MOVE.

Evidence levels are not interchangeable:

- Software components and tests do not prove camera/MCU/hardware operation.
- A single real image running through the pipeline does not validate the vision method.
- FakeSerial/FakeVideoCapture are simulations.
- Never claim real cleaning effectiveness without a physical closed-loop receipt.

Ownership:

- A — data/model: `microcleaning/data_learning/`
- B — vision/measurement: `microcleaning/vision/`
- C — planning/control simulation: `microcleaning/control_system/`
- Shared interfaces: `contracts.py` and `ports.py`; changes require cross-role review.

Before any edit, present the exact design, files, interface impact, risks, tests, and rollback; wait for user approval. Keep an HSV baseline until a stable, labeled, independent-set failure justifies added model complexity. No LLM may directly issue hazardous hardware commands.

