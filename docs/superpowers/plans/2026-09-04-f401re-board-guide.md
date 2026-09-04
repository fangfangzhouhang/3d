# F401RE Board Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a short Chinese guide explaining the currently flashed NUCLEO-F401RE board and its safe USB-only use.

**Architecture:** Create one focused Markdown file beside the existing hardware documentation. It summarizes verified board behavior and links to the detailed MCV1 protocol instead of copying source-level material. The guide treats physical 12 V and pump tests as unfinished work.

**Tech Stack:** Markdown, GitHub, NUCLEO-F401RE, STM32 ST-LINK Virtual COM Port.

## Global Constraints

- Use the verified MCV1 commands: `MCV1|PING`, `MCV1|STATUS`, `MCV1|PUMP|<action_id>|<duration_ms>`, and `MCV1|STOP`.
- State the serial settings exactly as `115200 8N1`, ASCII, over the board's USB virtual serial port.
- State that `PING/PONG` and `STATUS` were physically tested on `COM5`; do not state that the 12 V pump was tested.
- State that STM32 GPIO controls a MOSFET or driver and must not directly power a pump.
- Identify legacy `ARM`, `CLEAR`, and `PUMP n` text in the firmware README as not being the current MCV1 operating instructions.

---

### Task 1: Create the F401RE board guide

**Files:**
- Create: `MicroCleaningVision/说明文档/硬件组/F401RE板子说明.md`
- Reference: `MicroCleaningVision/说明文档/硬件组/STM32最小串口协议_v0.1.md`
- Reference: `MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/README.md`

**Interfaces:**
- Consumes: The MCV1 USB serial protocol and physical-test evidence recorded in the project.
- Produces: A human-readable single-file guide for collaborators handling the board.

- [ ] **Step 1: Add the board identity and verified status**

Write a “current status” section naming `NUCLEO-F401RE`, board USB, the MCV1 Stage 1 firmware, and only these verified facts: F401RE target build, drag-and-drop flash, `MCV1|PING`/`MCV1|PONG`, and `MCV1|STATUS` over `COM5`.

- [ ] **Step 2: Add the connection and terminal instructions**

Document that Tera Term is a serial terminal, not the normal Windows command prompt. Give the exact settings `115200`, `8 data bits`, `no parity`, `1 stop bit`, and show these three USB-only commands:

```text
MCV1|PING
MCV1|STATUS
MCV1|STOP
```

- [ ] **Step 3: Add the protocol table and safety boundary**

List every verified MCV1 command with its response and simple Chinese meaning. Explain that `PUMP` is an implemented command but must not be used until the 12 V supply, MOSFET/driver, flyback diode, emergency stop, and pump wiring have been reviewed. State that `ESTOP=1` is a protective state, not permission to bypass the emergency stop.

- [ ] **Step 4: Add scope and source links**

Add an “unfinished” section for actual pump validation, camera-triggered automatic commands, and XY movement. Link to `STM32最小串口协议_v0.1.md` for the full message contract and to the F401RE firmware README for pin mapping and build information.

- [ ] **Step 5: Review the guide against the actual flashed behavior**

Run:

```powershell
rg -n "MCV1\\|PING|MCV1\\|STATUS|MCV1\\|PUMP|MCV1\\|STOP|115200|12 V|MOSFET|COM5" "MicroCleaningVision/说明文档/硬件组/F401RE板子说明.md"
git diff --check
```

Expected: every required fact is present and `git diff --check` prints no whitespace errors.

- [ ] **Step 6: Commit and push the documentation branch**

Run:

```powershell
git add "MicroCleaningVision/说明文档/硬件组/F401RE板子说明.md"
git commit -m "docs: add F401RE board guide"
git push origin feat/firmware-f401re-mcv1
```

Expected: the feature branch contains the guide without modifying `main` directly.

