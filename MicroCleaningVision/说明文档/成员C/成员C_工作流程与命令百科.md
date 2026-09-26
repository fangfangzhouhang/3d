# 成员 C 工作流程与命令百科

> 成员 C 的目标：把 B 的算法 Mask 变成可解释的像素目标点和分段路径，并保留未来像素—毫米标定与 STM32 协议入口。

## 0. 当前该做什么（2026-09-19）

当前链路见 [当前总流程](../总流程说明/团队总流程与输入输出.md)。下文是命令字典。

路径只能预览，不能驱动 XY。`path_px` 禁止直连串口。假设毫米和步进对照表**不是**已验收标定，也**不是** MOVE 指令。

**一条总命令（看 Mask、路线编号和终端解说，不发泵）：**

```powershell
cd "D:\大创\3d\MicroCleaningVision"
.\.venv\Scripts\python.exe -m demo.demo_pipeline --input "data\raw_images\public\public_001.jpg" --mode analyze
```

看终端里的「路径预览」文字、`output/demo/demo_*/path_overlay.png` 和 `path_narrative.txt`。人认 COM 后才 ping，禁止扫口。

可选：用 JSON 改占位参数（尺度、喷头偏移、丝杆导程），仍不能发 MOVE：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze `
  --path-placeholders path_placeholders.json
```

## 目录

1. [C 在整条链中的位置](#1-c-在整条链中的位置)
2. [C 的总体输入和输出](#2-c-的总体输入和输出)
3. [C 负责的代码文件](#3-c-负责的代码文件)
4. [当前真实能力边界](#4-当前真实能力边界)
5. [运行真实图片路径预览](#5-运行真实图片路径预览)
6. [读取路径结果](#6-读取路径结果)
7. [运行纯软件仿真](#7-运行纯软件仿真)
8. [规划规则怎样工作](#8-规划规则怎样工作)
9. [从像素到STM32还差什么](#9-从像素到stm32还差什么)
10. [测试与Git](#10-测试与git)
11. [常见失败](#11-常见失败)
12. [完整复制命令](#12-完整复制命令)
13. [C 的完成标准](#13-c-的完成标准)

## 1. C 在整条链中的位置

```text
B算法Mask
↓
C判断没有目标/小区域/大区域
↓
中心点或往复扫描；多块用最近邻排序
↓
像素路线 + 假设毫米 + 步进对照（预览）
↓
终端文字 / path_overlay / path_narrative.txt
↓
未来：已验收标定才允许 work_mm
↓
结构化ActionRequest
↓
最低边界检查
↓
FakeSerial / 未来STM32（当前 MCV1 无 MOVE）
↓
ExecutionReceipt
↓
B再次拍照复检
```

C 不是把路线画漂亮就结束。路线必须能解释来源、坐标系、分段和当前不可执行原因。

## 2. C 的总体输入和输出

### 从 B 得到

| 输入 | 当前来源 | 用途 |
|---|---|---|
| 算法Mask | `output/demo/<run_id>/mask.png` | 规划区域 |
| 面积 | `summary.json` 的 `contamination.area_px` | 选择点喷或扫描策略 |
| 中心 | `contamination.centroid_px` | 位置参考 |
| 算法版本 | `contamination.algorithm_version` | 追溯路线由哪个识别结果产生 |
| 坐标单位 | 当前固定 `image_px` | 防止把像素冒充毫米 |
| 离线 mm/px JSON（可选） | `scripts/measure_scale_mm_per_px.py` 的输出 | 只填占位 `mm_per_px`，仍禁止写入动作 |

### 当前输出

| 输出 | 当前位置 | 含义 |
|---|---|---|
| 处理策略 | `summary.json` 的 `cleaning_plan.strategy` | NO_TARGET/CENTER_POINT/RASTER_SCAN |
| 路径点 | `cleaning_plan.path_px` | 图像中的像素位置 |
| 分段起点 | `segment_start_indices` | 哪些位置是新污染块，段间默认不喷 |
| 路径图 | `path_overlay.png` | 编号、段内实线、段间虚线；图上英文 `NO MOVE` |
| 文字解说 | 终端 与 `path_narrative.txt` | 用大白话走完规则→毫米→步数 |
| 假设毫米/步进 | `summary.json` 的 `path_preview` | 对照表；`feeds_action_request=false` |

### 未来输出

```text
已验收标定版本的真实 work_mm
结构化ActionRequest（SPRAY_AT_POINT）
MCV2 HOME/MOVE
控制器ExecutionReceipt
动作后的VerificationResult
```

## 3. C 负责的代码文件

```text
microcleaning/control_system/
├── planning/            # 路径：走法、假设毫米、步进对照
│   ├── cleaning_plan.py
│   ├── work_frame.py
│   ├── stepper_preview.py
│   └── path_preview.py
├── safety/              # 动作申请与安全闸
│   ├── fixed_rule.py
│   └── governor.py
├── serial/              # 假串口与 STM32 协议
│   ├── fake_serial.py
│   ├── stm32_protocol.py
│   └── stm32_serial.py
└── replay/              # 软件回放、Episode、Mock 基线
    ├── replay_mcl.py
    ├── episode_store.py
    └── mock_mcl.py

test/control_system/
├── planning/
├── safety/
├── serial/
└── replay/
```

改尺度、喷头偏移、丝杆导程：**只改 JSON 或这两个配置对象**，不要改走法代码。

## 4. 当前真实能力边界

当前已经能做：

```text
B算法Mask → C像素路径 → 假设毫米对照 → 步进对照 → 路径图和终端解说
```

当前不能声称：

```text
假设毫米已经是真实工作台坐标
步进对照已经可以发给电机
像素路径已经是毫米路径
FakeSerial已经连接STM32
收到ACK就表示清洗成功
模拟擦除Mask等于真实污染被清除
```

## 5. 运行真实图片路径预览

所有命令从项目根目录运行：

```powershell
cd "D:\大创\3d\MicroCleaningVision"
```

运行分析模式：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze
```

这个入口先调用 B 生成 Mask，再把同一 Mask 传给 C 的 `plan_cleaning()` 和 `build_path_preview()`。因此它验证的是当前 A图片→B识别→C路径的软件交接。

`analyze` 不生成真实 ActionRequest，也不打开串口。终端会打印「路径预览」解说；同内容写入 `path_narrative.txt`。

## 6. 读取路径结果

找到最新目录：

```powershell
$demoDir = Get-ChildItem "output\demo" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$demoDir.FullName
```

查看路径字段和解说：

```powershell
$summary = Get-Content -Raw -Encoding UTF8 `
  (Join-Path $demoDir.FullName "summary.json") | ConvertFrom-Json

$summary.cleaning_plan | Format-List
$summary.path_preview.evidence_boundary
Get-Content -Encoding UTF8 (Join-Path $demoDir.FullName "path_narrative.txt")
```

检查路径图：

```powershell
Invoke-Item (Join-Path $demoDir.FullName "path_overlay.png")
```

人工检查：

1. 路径点是否落在 B 的白色 Mask 内；
2. 两个断开的污染块之间是否只用虚线连接（段间不喷）；
3. 大区域是否有基本覆盖，小区域是否避免产生过多点；
4. `cleaning_plan` 的坐标单位是否明确写着 `image_px`；
5. `path_preview.feeds_action_request` 和 `send_to_controller` 是否都是 false。

## 7. 运行纯软件仿真

使用固定合成图、归一化虚拟标定和 FakeSerial：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --generate-sample `
  --mode simulate

.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --live --camera-index 1
.\.venv\Scripts\python.exe -m demo.demo_pipeline --generate-sample --mode ping-only
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --generate-sample --mode arm-pump --confirm-pump --controller fake
```

额外输出包括：

```text
post_mask.png              程序模拟擦除后的Mask
action_request             仅模拟动作申请
safety_decision            软件判断结果
execution_receipt          FakeSerial模拟回执
verification              模拟前后面积比较
path_preview               即使 simulate 也仍是预览，不发 MOVE
```

这条命令的意义是验证对象和程序能接起来，不是验证真实机械或清洗效果。

## 8. 规划规则怎样工作

几何层复用现有 `plan_cleaning()`：OpenCV 连通域 + 往复扫描（boustrophedon，像耕地一样来回扫）。多块默认最近邻（nearest neighbor，从 `start_px` 出发每次去最近的一块），不是按面积从大到小。

### 8.1 走法（与标定无关）

| 规则 | 行为 |
|---|---|
| 空 Mask | `NO_TARGET`，不产生路径 |
| 面积占比 ≤ `small_target_ratio`（默认 2%） | 每块一个中心点 |
| 更大区域 | 块内往复扫描，默认间距 `raster_step_px=16` |
| 断开的块 | `segment_start_indices` 分段；段间关泵 |
| 块顺序 | 默认 `nearest_neighbor`；可改回 `area_desc` |

`CleaningPlanPolicy` 参数：

```text
small_target_ratio    小区域与大区域的分界比例
raster_step_px        大区域扫描点之间的像素间距
visit_order           nearest_neighbor 或 area_desc
start_px              最近邻的出发像素，默认 (0,0)
```

若 JSON 里填写 `assumed_spray_width_mm`，会按当前有效 mm/px 换算 `raster_step_px`。没有真实湿斑直径时不要把这个数当成覆盖率结论。

### 8.2 占位参数改哪里

未测的量全部放在 `--path-placeholders` JSON，默认也能跑（用假设值，并在终端标明 assumed）。

```json
{
  "assumed_spray_width_mm": null,
  "visit_start_px": [0, 0],
  "work": {
    "mm_per_px": null,
    "assumed_mm_per_px": 0.01,
    "origin_px": [0, 0],
    "rotation_deg": 0,
    "flip_y": true,
    "nozzle_offset_mm": [0.0, 0.0],
    "travel_min_mm": null,
    "travel_max_mm": null,
    "homography_3x3": null,
    "feeds_action_request": false
  },
  "stepper": {
    "steps_per_rev": 200,
    "microstep": 16,
    "assumed_lead_mm_per_rev": 8.0,
    "lead_mm_per_rev_x": null,
    "lead_mm_per_rev_y": null,
    "steps_per_mm_x": null,
    "steps_per_mm_y": null,
    "homed": false,
    "send_to_controller": false
  }
}
```

| 以后测到什么 | 改哪一格 |
|---|---|
| 测微尺 mm/px | `work.mm_per_px`（仍须 `feeds_action_request=false`） |
| 画面原点对应工作台哪一点 | `work.origin_px` |
| 相机相对台面转了多少度 | `work.rotation_deg` |
| 图像 y 向下、台面 y 向上 | `work.flip_y` |
| 喷头相对显微镜光心偏了多少毫米 | `work.nozzle_offset_mm` |
| 托盘行程 | `work.travel_min_mm` / `travel_max_mm` |
| 倾斜平面需要单应性 | `work.homography_3x3`（3×3） |
| 电机步距角/细分 | `stepper.steps_per_rev` / `microstep` |
| 丝杆导程（转一圈走多少毫米） | `lead_mm_per_rev_x/y` |
| 铭牌直接给了步/毫米 | `steps_per_mm_x/y`（优先于导程公式） |
| 已经回零 | `homed`（现在必须保持 false 才诚实） |

`feeds_action_request=true` 或 `send_to_controller=true` 会被直接拒绝。

### 8.3 毫米怎么变成转数

对照用的步进当量（steps/mm，走 1 毫米要发多少脉冲）：

```text
步/mm = (步/圈 × 细分) / 导程(mm/圈)
转数 = 步数 / (步/圈 × 细分)
```

默认假设：200 步/圈、16 细分、导程 8 mm → **400 步/mm**。例如 Δx=2 mm、Δy=0 → X 走 800 步，约 0.25 圈。这是桌面 CNC/3D 打印机的常用公式，不是你们模组的实测值。

电机对照表在 `path_preview.motion.legs`：

- `home_to_first`：从假设原点到第一个污渍，泵关
- `spray`：同一段内移动，泵开
- `segment_travel`：换到下一块，泵关

未 HOME、无 MCV2 `MOVE` 时，这些数字只出现在终端和 JSON 里。

## 9. 从像素到STM32还差什么

### 第一道桥：像素—毫米标定

软件已经能用占位公式预览。要变成真实 `work_mm` 必须知道：

```text
相机像素点对应平台哪个毫米位置
X/Y方向是否相反或旋转
喷头相对光心的偏移
标定误差是多少
标定版本何时失效
```

输出不能只是比例，还要包括：标定版本、适用工作平面、验证误差和有效状态。B 的离线 `measure_scale_mm_per_px.py` 可以提供 `mm_per_px`，但 JSON 里必须保持 `feeds_action_request=false`。

### 第二道桥：ActionRequest

标定验收后才能把目标放入结构化动作申请。动作申请只是“请求做什么”，不是直接写串口。`analyze` 模式现在仍然不生成申请。

### 第三道桥：STM32协议

必须由STM32团队提供：

- 帧头、字段、单位和字节顺序；
- 命令ID；
- ACK和错误码；
- 超时、重复命令和停止规则；
- 限位和急停事实。

这些信息缺失时，C只使用 FakeSerial，不能猜测字节并发送到真实 COM 口。

没有步进电机、没有 MCV2 时，不要申请 XY。可改用定点短喷：`propose_pump_in_place()` 生成
`primitive=PUMP_IN_PLACE`、`coordinate_frame=nozzle_fixed`、目标 `(0,0)`，表示喷头
原地 100～300 ms 脉冲，不是伪造的 `work_mm` 标定。治理器对此返回 HUMAN；Demo
需要 `--mode arm-pump --confirm-pump`。`STM32SerialController` 还要 `--arm-pump`
才会把 ALLOW 翻译成 `MCV1|PUMP`。`stop()` 可在未武装时发送 `MCV1|STOP`，不发泵。
默认 `--from-camera` 只分析，不发泵。当前固件遇到 `HOME`/`MOVE` 必须拒绝。

### 第四道桥：动作后复检

控制器回执只证明“控制器报告执行了什么”。是否清洗成功必须由 B 对动作后图片再次测量。

## 10. 测试与Git

只运行 C 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s test\control_system `
  -p "test*.py" `
  -v
```

运行完整回归：

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s test `
  -p "test*.py" `
  -v
```

查看修改范围：

```powershell
git diff --name-only
git diff -- microcleaning/control_system test/control_system demo/demo_pipeline.py
```

分支示例：

```powershell
git switch -c feat/c-path-preview-v0
git add microcleaning/control_system test/control_system demo/demo_pipeline.py `
  "说明文档/成员C/成员C_工作流程与命令百科.md"
git commit -m "feat(control): 路径规则预览与步进对照"
```

## 11. 常见失败

| 现象 | 归属判断 | 正确处理 |
|---|---|---|
| Mask本身位置错误 | B | 保存路径图并退回B，不在C修改Mask |
| 大区域覆盖点太少 | C | 记录失败，再调整最小规则 |
| 两个污染块间出现喷射连线 | C | 检查分段起点和段间关闭规则 |
| 坐标方向反了 | 标定/C | 改 `flip_y` / `rotation_deg` 占位，不猜比例去发电机 |
| 把 400 步/mm 写成已测 | C | 终端必须写 assumed；等模组铭牌再填 `steps_per_mm_*` |
| FakeSerial超时 | C软件仿真 | 保存失败回执，不伪造成功 |
| ACK成功但污染未减少 | 视觉复检/物理工艺 | ACK不等于效果成功 |

## 12. 完整复制命令

```powershell
cd "D:\大创\3d\MicroCleaningVision"

.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --input "data\raw_images\public\public_001.jpg" `
  --mode analyze

$demoDir = Get-ChildItem "output\demo" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$summary = Get-Content -Raw -Encoding UTF8 `
  (Join-Path $demoDir.FullName "summary.json") | ConvertFrom-Json

$summary.cleaning_plan | Format-List
Get-Content -Encoding UTF8 (Join-Path $demoDir.FullName "path_narrative.txt")
Invoke-Item (Join-Path $demoDir.FullName "path_overlay.png")

.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze
.\.venv\Scripts\python.exe -m demo.demo_pipeline --generate-sample --mode ping-only
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --generate-sample --mode arm-pump --confirm-pump --controller fake

.\.venv\Scripts\python.exe -m unittest discover `
  -s test\control_system `
  -p "test*.py" `
  -v
```

## 13. C 的完成标准

```text
[ ] 输入来自B算法Mask，不用人工Mask冒充识别结果
[ ] 空Mask不产生路径
[ ] 小区域和大区域策略可解释
[ ] 多块默认最近邻，段间不喷
[ ] 路径点位于图像范围和Mask内
[ ] 断开区域分段明确
[ ] 保存path_overlay、path_narrative和cleaning_plan
[ ] 终端能讲清：规则 → 假设毫米 → 步数/转数
[ ] 坐标单位明确为image_px；毫米对照标记为 assumed_work_mm
[ ] 没有把虚拟标定写成真实标定
[ ] feeds_action_request 与 send_to_controller 均为 false
[ ] 默认不打开真实COM口；PING/PUMP 必须显式指定端口和武装标志
[ ] C测试和完整回归通过
```
