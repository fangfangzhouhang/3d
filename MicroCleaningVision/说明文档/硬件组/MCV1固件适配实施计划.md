# MCV1 固件适配实施计划

> **执行要求：** 每项任务均先写失败测试，再写最小实现；每项完成后进行独立审查。

**目标：** 将 NUCLEO-F401RE 阶段一固件以独立目录加入本仓库，遵守 MCV1 串口协议，并完成真实目标编译、烧录与不接 12 V 的 PING/STATUS 证据。

**架构：** Python 端继续使用 `microcleaning/control_system/stm32_protocol.py` 的 MCV1 文本合同；固件新增独立的 MCV1 会话层，将受限文本命令翻译为既有安全状态机动作。主循环负责定时更新和异步回传 `DONE`，安全状态机仍是唯一可以驱动 PB5 的位置。

**技术栈：** C11 主机测试、STM32CubeMX 6.18.1、STM32CubeIDE 2.2.0、STM32CubeProgrammer 2.23.0、NUCLEO-F401RE、USART2/ST-LINK VCP、Python/pyserial 探测脚本。

## 全局约束

- 固件目录只新增在 `MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/`，不修改视觉、数据或规划业务模块。
- 串口固定为 `115200 8N1`、ASCII、每行以 LF 结束；固件同时接受 CRLF。
- MCV1 最大单行长度 128 字节；`action_id` 为 1-32 个 ASCII 字母、数字、下划线或连字符。
- 固件仅接受 100-2000 ms 脉冲；第一轮电脑端默认不超过 500 ms。
- PB5 默认低；PB12 急停有效时必须关泵并锁存；急停解除不得自动续跑。
- 未经完成硬件检查和人工关卡，禁止连接 12 V；第一次实机联调只接 NUCLEO USB。
- 不提交 `Debug/`、`Release/`、`.elf`、`.hex`、`.bin`、IDE 缓存、安装包或本机绝对路径。

---

### 任务 1：迁入可编译的 F401RE 工程并建立忽略规则

**文件：**
- 创建：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/` 下的真实 `.ioc`、`Core/`、`Drivers/`、`app/`、`platform/`、`tests/`、`scripts/`。
- 创建：`MicroCleaningVision/firmware/nucleo_f401re/README.md`。
- 修改：仓库 `.gitignore`。

**接口：**
- 输入：已经通过主机测试的阶段一安全状态机、串口收行器、UART 守卫与 F401RE CubeMX 配置。
- 输出：可由 CubeIDE 导入和构建的工程；PB5、PB12、PA2/PA3 配置可追溯。

- [ ] **步骤 1：写失败的仓库结构测试**

在 `MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/tests/test_repository_layout.mjs` 断言以下真实文件存在，且 `.gitignore` 忽略构建产物：

```javascript
assert.ok(existsSync("f401re-stage1.ioc"));
assert.ok(existsSync("Core/Src/main.c"));
assert.ok(existsSync("Core/Src/stm32f4xx_hal_msp.c"));
assert.ok(gitignore.includes("Debug/"));
assert.ok(gitignore.includes("*.elf"));
```

- [ ] **步骤 2：运行并确认失败**

运行：

```powershell
node MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/tests/test_repository_layout.mjs
```

预期：因工程目录尚未进入仓库而失败。

- [ ] **步骤 3：迁入工程并最小化说明**

复制已验证的源文件与 CubeMX 生成的 `Drivers/`、启动文件、链接脚本、`.project`、`.cproject`，但不复制构建输出。README 必须写清：板型、PA2/PA3、PB5、PB12、生成/编译命令和无 12 V 初测边界。

- [ ] **步骤 4：运行结构测试与固件主机测试**

运行：

```powershell
node MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/tests/test_repository_layout.mjs
powershell -ExecutionPolicy Bypass -File MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/scripts/run-host-tests.ps1
```

预期：结构测试通过，既有核心安全测试全部通过。

- [ ] **步骤 5：提交**

```powershell
git add .gitignore MicroCleaningVision/firmware/nucleo_f401re
git commit -m "feat: 导入F401RE固件工程"
```

### 任务 2：实现 MCV1 同步命令与明确错误回复

**文件：**
- 创建：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/app/include/mcv1_protocol.h`。
- 创建：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/app/src/mcv1_protocol.c`。
- 创建：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/tests/test_mcv1_protocol.c`。
- 修改：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/Core/Src/main.c`。
- 修改：`MicroCleaningVision/说明文档/硬件组/STM32最小串口协议_v0.1.md`。

**接口：**
- 输入：`fw_core_t`、`fw_inputs_t`、一行 ASCII 文本。
- 输出：固定 128 字节内的 `MCV1|PONG`、`MCV1|STATUS|ESTOP=<0/1>|PUMP=<0/1>`、`MCV1|ACK|STOP`、`MCV1|DONE|STOP` 或 `MCV1|ERR|<action_id>|<code>`。

- [ ] **步骤 1：写失败的 MCV1 合同测试**

在 C 测试中先断言以下输出逐字节相同：

```c
EXPECT_LINE("MCV1|PING\n", "MCV1|PONG\r\n");
EXPECT_LINE("MCV1|STATUS\n", "MCV1|STATUS|ESTOP=0|PUMP=0\r\n");
EXPECT_LINE("MCV1|STOP\n", "MCV1|ACK|STOP\r\n");
EXPECT_NEXT("MCV1|DONE|STOP\r\n");
EXPECT_LINE("MCV2|PING\n", "MCV1|ERR|NONE|BAD_VERSION\r\n");
```

- [ ] **步骤 2：运行并确认失败**

运行：

```powershell
powershell -ExecutionPolicy Bypass -File MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/scripts/run-host-tests.ps1 -Only mcv1_protocol
```

预期：因 `mcv1_protocol` 尚不存在而失败。

- [ ] **步骤 3：写最小解析和格式化实现**

实现 `mcv1_session_init`、`mcv1_process_line` 和 `mcv1_poll`。未知版本、字段数错误、非 ASCII、超长行和非法动作编号必须返回固定 `ERR`，且不得改变 PB5 决策。`STATUS` 只返回 `ESTOP` 和 `PUMP` 两个稳定字段。

- [ ] **步骤 4：运行协议、核心与 Python 合同回归**

运行：

```powershell
powershell -ExecutionPolicy Bypass -File MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/scripts/run-host-tests.ps1
Set-Location MicroCleaningVision
.\.venv\Scripts\python.exe -m unittest test.control_system.test_stm32_protocol -v
```

预期：固件和电脑端对 `PING`、`STATUS`、`STOP`、版本错误的编码/解析一致。

- [ ] **步骤 5：提交**

```powershell
git add MicroCleaningVision/firmware MicroCleaningVision/说明文档/硬件组/STM32最小串口协议_v0.1.md
git commit -m "feat: 接入MCV1基础协议"
```

### 任务 3：实现 PUMP 的 ACK/DONE、急停与防重放

**文件：**
- 修改：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/app/include/mcv1_protocol.h`。
- 修改：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/app/src/mcv1_protocol.c`。
- 修改：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/Core/Src/main.c`。
- 修改：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/tests/test_mcv1_protocol.c`。

**接口：**
- 输入：`MCV1|PUMP|<action_id>|<duration_ms>`。
- 输出：先 `ACK`，在本地定时结束后仅一次 `DONE`；错误时只输出 `ERR`，不启动 PB5。

- [ ] **步骤 1：写失败的动作生命周期测试**

测试必须包含以下行为：

```c
EXPECT_LINE("MCV1|PUMP|A001|300\n", "MCV1|ACK|A001\r\n");
EXPECT_PUMP(true);
ADVANCE_MS(299);
EXPECT_NO_LINE();
ADVANCE_MS(1);
EXPECT_NEXT("MCV1|DONE|A001\r\n");
EXPECT_PUMP(false);
EXPECT_LINE("MCV1|PUMP|A001|300\n", "MCV1|ERR|A001|DUPLICATE\r\n");
SET_ESTOP(true);
EXPECT_LINE("MCV1|PUMP|A002|300\n", "MCV1|ERR|A002|ESTOP\r\n");
```

另写 99 ms、2001 ms、动作正在执行时的新编号、串口断流、STOP 与急停中断的测试；每种情况均断言 PB5 为低或按时关闭。

- [ ] **步骤 2：运行并确认失败**

运行：

```powershell
powershell -ExecutionPolicy Bypass -File MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/scripts/run-host-tests.ps1 -Only mcv1_protocol
```

预期：当前协议层无法产生异步 `DONE` 和去重结果而失败。

- [ ] **步骤 3：实现会话状态与轮询**

会话对象保存活动 `action_id`、最近结束 `action_id` 与终态。`PUMP` 仅在急停未锁存、无活动动作、时长范围有效且编号未使用时，内部发起一次安全状态机授权和脉冲；主循环每次 `fw_core_step` 后调用 `mcv1_poll`，仅在输出从泵开变为泵关时发送一次 `DONE`。急停或 STOP 终止活动动作时发送对应 `ERR` 或 `DONE`，不得自动恢复。

- [ ] **步骤 4：运行完整回归**

运行：

```powershell
powershell -ExecutionPolicy Bypass -File MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/scripts/run-host-tests.ps1
Set-Location MicroCleaningVision
.\.venv\Scripts\python.exe -m unittest discover -s test -v
```

预期：所有固件主机测试和既有 Python 测试通过。

- [ ] **步骤 5：提交**

```powershell
git add MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1
git commit -m "feat: 完成MCV1泵动作回执"
```

### 任务 4：目标编译、烧录和无 12 V 串口证据

**文件：**
- 创建：`MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/verification/target-build.txt`。
- 修改：`MicroCleaningVision/说明文档/硬件组/软硬件联调操作手册.md`。
- 修改：`MicroCleaningVision/project_state.yaml`。

**接口：**
- 输入：CubeIDE 可编译工程、ST-LINK、明确的 `COM5`。
- 输出：目标 `.elf` 仅作为本机烧录输入；Git 中保存构建日志、烧录命令和 `PING/STATUS` 文本证据，不提交二进制。

- [ ] **步骤 1：先写失败的联调证据检查**

新增一个 Node 或 Python 文档检查，要求 `target-build.txt` 包含：板型、工具版本、编译成功标识、烧录命令、实际 COM 口、PONG、两种 STATUS、`12V disconnected`。

- [ ] **步骤 2：运行并确认失败**

运行该检查；预期：验证文件尚不存在。

- [ ] **步骤 3：生成、编译、烧录并低压探测**

在英文临时路径由 CubeMX 生成工程，再由 CubeIDE headless build 构建。编译成功后，先用 CubeProgrammer 读取芯片身份确认 `STM32F401RE`，再写入本次 `.elf` 或 `.hex`。烧录期间仅连接 ST-LINK USB，保持泵、MOSFET 输入和 12 V 物理断开。使用：

```powershell
Set-Location MicroCleaningVision
.\.venv\Scripts\python.exe scripts\probe_stm32_link.py --port COM5 --command ping
.\.venv\Scripts\python.exe scripts\probe_stm32_link.py --port COM5 --command status
```

急停两种 STATUS 只有在 PB12 接线和硬件人员明确确认后才执行；未验证则写为未完成，不能伪造结果。

- [ ] **步骤 4：运行全部软件回归和证据检查**

运行：

```powershell
powershell -ExecutionPolicy Bypass -File MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/scripts/run-host-tests.ps1
Set-Location MicroCleaningVision
.\.venv\Scripts\python.exe -m unittest discover -s test -v
git diff --check
```

预期：软件测试通过；验证报告准确区分已完成与未完成的硬件证据。

- [ ] **步骤 5：提交并推送分支**

```powershell
git add MicroCleaningVision/firmware MicroCleaningVision/说明文档/硬件组/软硬件联调操作手册.md MicroCleaningVision/project_state.yaml
git commit -m "test: 记录F401RE低压联调"
git push -u origin feat/firmware-f401re-mcv1
```

## 自检

- 设计中的 MCV1 消息、115200 8N1、ACK/DONE、动作编号、防重放、PB5/PB12、100-2000 ms、无 12 V 条件均有对应任务。
- 本计划没有移动、坐标、托盘尺寸、步进电机或视觉决策的实现任务。
- 所有新增生产行为均在对应任务中先定义失败测试和预期失败原因。
