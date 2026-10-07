# MicroCleaningVision 实物联调进度记录（Gate 0–8）

> 用途：给硬件联调同学及其 Agent 使用的逐关进度账本。按《实物联调 Agent 操作手册》逐层推进，每关独立证据。
> 更新：2026-10-07（Gate 0–5 已完成；Gate 6 待测）。责任：H2 运动 + H1 喷洗 + C 软件协调。
> 进度标记：`[✓]` 通过 / `[▶]` 进行中 / `[ ]` 未开始 / `[!]` 有问题。

---

## Gate 0：代码与硬件身份确认 [✓]

- 目的：确认代码基线正确、软件闭环通过、实物板型号无误。
- 结果：
  - git：main `d8043c2`（2026-10-06 拉取 fast-forward，含 PR #22/#24）
  - 全量测试：**346 项通过**；Mock 闭环：**SUCCESS**
  - 实物板丝印：**STM32F103C8T6**（USART2: PA2=TX/PA3=RX；X: PA6/PA7；Y: PA0/PA1；PUMP=PB0；ESTOP=PB2；ARM=PB3）
- 证据：测试日志 + Mock 输出；板子实物照片/丝印确认。
- 通过：是。

---

## Gate 1：U500 / USB 相机 [✓]

- 目的：确认相机可开、不冻结、可存图、可识别。
- 命令/参数：
  ```powershell
  .\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --live --camera-index 0 --camera-backend 1400
  ```
- 结果：
  - 设备：**index 0**（枚举顺序已变更，原 index 1），设备名 "USB2.0 UVC PC Camera"，VID_A16F，640x480，MSMF backend=1400
  - 对焦分调到 **0.614**，FOCUS_LOW 消除
- 证据：实时画面 + 存图 PNG + 对焦/光照质量输出。
- 通过：是。

---

## Gate 2：PC ↔ STM32 串口 [✓]

- 目的：只验证通信，不动电机/泵。
- 参数：COM5、115200、8N1、ASCII；不扫描 COM（手动指定）。
- 接线：USB-TTL TX→PA3(RX)、RX→PA2(TX)、GND 共地；**DM542 PUL+/DIR+ 共阳接 3.3V**（接 5V 会方向失效/板子不应答；3.3V 接 GND 会短路）。
- 结果：
  - 板子烧的是 **Stage2 电机固件**，不认泵协议 `PING`（回 `ERR: BAD_CMD`）→ 用 HELLO 验证
  - `HELLO` → `STEP_OK v0.3`；`READXY` → `STEP2 X=0 BX=0 Y=0 BY=0`
- 通过：是。

---

## Gate 3：单轴电机最小运动 [✓]

- 目的：确认方向、指令稳定、停止可靠，记录方向表。
- 第一次低风险参数：SPEED 50 → PULSE 100~200；正式验证用 SPEED 100 + 800~1600 步。
- 结果：
  - **X 轴 = 下面那台电机，Y 轴 = 上面那台电机**
  - 正反运动正常、无异常响声；两轴独立定时器并行不干扰
  - 方向表（**物理电机→载物台/图像**，见下）：
  | 指令 | 转正摄像头后画面特征方向 | 备注 |
  |---|---|---|
  | X FWD | 特征向左（dx<0） | Gate4 重测确认 |
  | X REV | 特征向右 | |
  | Y FWD | 特征向下（dy>0） | |
  | Y REV | 特征向上（dy<0） | |
  - 【警告】转正摄像头前方向表为：X FWD→下、Y FWD→右；**摄像头朝向影响图像方向表**，物理轴不变。
- 故障记录：PUL-/3.3V 共阳线接触不良 → 电机时动时不动；重插端子螺丝后恢复。
- 通过：是。

---

## Gate 4：电机尺度 / 像素标定 [✓]

- 命令/参数：
  ```powershell
  .\.venv\Scripts\python.exe scripts\calibrate_motor_mm_per_px.py `
    --serial-port COM5 --camera-index 0 --camera-backend 1400 `
    --steps 800 --speed 100
  ```
- 结果（有效证据 `cal_20261006T121727Z`）：
  | 轴 | 动作 | 特征位移 | 分数 | mm/px |
  |---|---|---|---|---|
  | X | 800 FWD | 横移 147px | 0.994 | **0.0170** |
  | Y | 800 REV | 纵移 149px | 0.616 | **0.0168** |
  - `warnings=[]`；实测尺度≈0.017 mm/px（比占位 0.01 大 1.7 倍），**未写入 pipeline**（B/C 地盘）
- 证据：`output/calibration/cal_20261006T121727Z/{f0,f1_after_x,f2_after_y}.png` + `mm_per_px.json`
- `mm_per_px.json` SHA256：`3A00F0EBE00E9A4BC787F81F0102796A6831BECC6F812FC0606CA129CB870B10`
- 教训：首次标定摄像头装反 90° → 轴映射错位 + `AXIS_CROSSTALK_HIGH`，mm_per_px_x=0.096/0.1 不可用；转正后重测才有效。
- 通过：是。

---

## Gate 5：显微镜中心 → 喷头中心偏移 [✓]

- 定义：`scope_to_nozzle_delta_steps = [dx, dy]` —— 标记已在显微镜中心时，载物台再走多少有符号步能把同一位置送到喷头中心。
- 结构：显微镜固定、喷头固定、样品平台移动。
- **禁止**：尺子距离 ÷ 理论步距直接写偏移（受电机方向/丝杆/安装偏差/工作坐标/机械回差影响）；必须实测有符号步数。
- 粗定位现有命令：`TO_NEEDLE`（Y 7680 FWD）、`TO_SCOPE`（Y 7680 REV）；7680 只作粗定位，不能直接当偏移。
- 计划步骤：
  1. 载物台上放/找一个清晰标记点
  2. 显微镜下把标记对到视野中心（记录当前步数坐标）
  3. `TO_NEEDLE` 粗定位到针尖附近
  4. `MOVEXY` 小步微调，让标记对准针尖正下方
  5. 记录“显微镜中心 → 针尖”的净有符号步数 = `scope_to_nozzle_delta_steps`
  6. 生成偏移 JSON（绑定 Gate4 SHA256、装配编号、不确定度、`axes_confirmed:true`）
- 偏移 JSON 模板：
  ```json
  {
    "version": "scope-nozzle-v1",
    "setup_id": "rig-001",
    "coordinate_frame": "stage2_signed_steps_scope_to_nozzle",
    "scope_to_nozzle_delta_steps": [420, -310],
    "axes_confirmed": true,
    "calibration_source": "现场实测记录",
    "uncertainty_steps": [10, 15],
    "motor_calibration_sha256": "3A00F0EBE00E9A4BC787F81F0102796A6831BECC6F812FC0606CA129CB870B10",
    "mock_only": false
  }
  ```
- 结果（2026-10-07 实测）：标记对到显微镜中心 = (X=77, Y=121)；`TO_NEEDLE` 后标记在针尖正下方 = (X=77, Y=7680)。
  - `scope_to_nozzle_delta_steps = [0, +7559]`（X 未变=0；Y 净 +7559，含初始 Y=121）
  - 偏移 JSON：`output/offset/gate5_scope_nozzle_offset.json`（绑定 Gate4 SHA256 `3A00...B870B10`，`axes_confirmed:true`）
- 通过：是。

---

## Gate 6：去程 + 回程，不喷水 [✓]

- 验收：去程目标到达喷头区域；回程显微镜中原目标回到相同视野；记录回程像素误差并重复多次。
- 坐标关系：`q_exec = q_obs + d_target + d_offset`；`RETURN = -(d_target + d_offset)`（不能只退 offset）。
- 结果（2026-10-07 实测，2 次往返，不喷水）：
  - 去程：`TO_NEEDLE`（Y+7680）→ 针尖区域，十字与污渍重合。
  - 回程：`MOVEXY 0 FWD 7559 REV`（`RETURN=-(d_offset)`，Gate5 偏移 dy=+7559）→ 标记回到显微镜视野。
  - 回程像素误差（center_object 修正量，孔径 mask 后读）。
  - 第 1 次：X=20FWD / Y=136REV。
  - 第 2 次：X=2REV  / Y=118REV。
  - 平均：X≈11、Y≈127（回程偏差主集中在 Y 轴，机械回差为主）。
- 通过：是。

---

## Gate 7：泵独立测试 [ ]

- 顺序：GPIO 逻辑 → LED → 万用表/示波器 → MOSFET → 空载泵 → 少量水 → 清洗液。
- 第一次短喷：100 ms；`PUMP ON` 固定 300ms 自停。
- 验收：PC→PUMP→ACK→PB0→泵启停→DONE；DONE 只证明输出流程完成，不代表清洗成功。
- 结果：待测。

---

## Gate 8：第一次完整实物闭环 [ ]

- 命令模板（一次 1 污渍 1 cycle）：
  ```powershell
  python -m demo.closed_loop_station --real --camera-index 0 --serial-port COM5 `
    --stage2-calibration output\calibration\cal_20261006T121727Z\mm_per_px.json `
    --path-placeholders <工作坐标JSON> --nozzle-offset <Gate5偏移JSON> `
    --arm-stage2-xy --arm-pump --confirm-pump --stage2-set-zero `
    --pump-duration-ms 100 --max-cycles 1
  ```
- 验收：前图→识别→计算→去程→短喷→回程→后图→单目标复检→完整 Episode（`output/closed_loop/station_*/`）。
- 结果：待测。

---

## 汇总状态

| Gate | 名称 | 状态 |
|---|---|---|
| 0 | 代码与硬件身份 | ✓ |
| 1 | U500 相机 | ✓ |
| 2 | PC↔STM32 串口 | ✓ |
| 3 | 单轴电机 | ✓ |
| 4 | 尺度/像素标定 | ✓ |
| 5 | 显微镜→喷头偏移 | ✓ |
| 6 | 去程+回程（不喷水） | ✓ |
| 7 | 泵独立测试 |  |
| 8 | 完整实物闭环 |  |

> 每次实测后更新本表，并附命令、参数、结果、实物现象、证据路径。
