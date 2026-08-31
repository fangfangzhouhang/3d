# 成员 C 工作流程与命令百科

> 成员 C 的目标：把 B 的算法 Mask 变成可解释的像素目标点和分段路径，并保留未来像素—毫米标定与 STM32 协议入口。

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
生成中心点或分段往复路线
↓
在原图上画出路线
↓
未来：像素—毫米标定
↓
结构化ActionRequest
↓
最低边界检查
↓
FakeSerial / 未来STM32
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

### 当前输出

| 输出 | 当前位置 | 含义 |
|---|---|---|
| 处理策略 | `summary.json` 的 `cleaning_plan.strategy` | NO_TARGET/CENTER_POINT/RASTER_SCAN |
| 路径点 | `cleaning_plan.path_px` | 图像中的像素位置 |
| 分段起点 | `segment_start_indices` | 哪些位置是新污染块，段间默认不喷 |
| 路径图 | `path_overlay.png` | 人工检查顺序和覆盖范围 |

### 未来输出

```text
带标定版本的毫米目标
结构化ActionRequest
控制器ExecutionReceipt
动作后的VerificationResult
```

## 3. C 负责的代码文件

```text
microcleaning/control_system/
├── cleaning_plan.py    # Mask到策略和像素路线
├── fixed_rule.py       # 最小动作申请
├── governor.py         # 最低动作边界
├── fake_serial.py      # 模拟ACK、超时和错误
├── replay_mcl.py       # 软件回放编排
├── episode_store.py    # 回合记录
└── mock_mcl.py         # 合成回归基线

test/control_system/
├── test_control_system.py
└── test_mock_mcl.py
```

## 4. 当前真实能力边界

当前已经能做：

```text
B算法Mask → C像素路径 → 路径图 → 软件记录
```

当前不能声称：

```text
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

这个入口先调用 B 生成 Mask，再把同一 Mask 传给 C 的 `plan_cleaning()`。因此它验证的是当前 A图片→B识别→C路径的软件交接。

`analyze` 不生成真实 ActionRequest，也不打开串口。

## 6. 读取路径结果

找到最新目录：

```powershell
$demoDir = Get-ChildItem "output\demo" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$demoDir.FullName
```

查看路径字段：

```powershell
$summary = Get-Content -Raw -Encoding UTF8 `
  (Join-Path $demoDir.FullName "summary.json") | ConvertFrom-Json

$summary.cleaning_plan | Format-List
```

检查路径图：

```powershell
Invoke-Item (Join-Path $demoDir.FullName "path_overlay.png")
```

人工检查四个问题：

1. 路径点是否落在 B 的白色 Mask 内；
2. 两个断开的污染块之间是否错误连成喷射线；
3. 大区域是否有基本覆盖，小区域是否避免产生过多点；
4. 路径是否明确写着 `image_px`。

## 7. 运行纯软件仿真

使用固定合成图、归一化虚拟标定和 FakeSerial：

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline `
  --generate-sample `
  --mode simulate
```

额外输出包括：

```text
post_mask.png              程序模拟擦除后的Mask
action_request             仅模拟动作申请
safety_decision            软件判断结果
execution_receipt          FakeSerial模拟回执
verification              模拟前后面积比较
```

这条命令的意义是验证对象和程序能接起来，不是验证真实机械或清洗效果。

## 8. 规划规则怎样工作

当前 `CleaningPlanPolicy` 只有两个主要参数：

```text
small_target_ratio    小区域与大区域的分界比例
raster_step_px        大区域扫描点之间的像素间距
```

当前规则：

- 空 Mask：`NO_TARGET`，不产生路径；
- 小区域：每个独立污染块产生一个中心点；
- 大区域：每个污染块分别生成往复式扫描段；
- 断开区域：通过 `segment_start_indices` 标记新段，段间默认关闭喷射。

修改参数前要写出具体失败。例如“喷头覆盖直径为3 mm，而当前像素间距造成明显漏区”；没有真实标定和喷头数据时，不要优化虚构的物理覆盖率。

## 9. 从像素到STM32还差什么

### 第一道桥：像素—毫米标定

必须知道：

```text
相机像素点对应平台哪个毫米位置
X/Y方向是否相反或旋转
标定误差是多少
标定版本何时失效
```

输出不能只是比例，还要包括：标定版本、适用工作平面、验证误差和有效状态。

### 第二道桥：ActionRequest

标定后才能把目标放入结构化动作申请。动作申请只是“请求做什么”，不是直接写串口。

### 第三道桥：STM32协议

必须由STM32团队提供：

- 帧头、字段、单位和字节顺序；
- 命令ID；
- ACK和错误码；
- 超时、重复命令和停止规则；
- 限位和急停事实。

这些信息缺失时，C只使用 FakeSerial，不能猜测字节并发送到真实 COM 口。

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
git diff -- microcleaning/control_system test/control_system
```

分支示例：

```powershell
git switch -c feat/c-path-v0-2
git add microcleaning/control_system test/control_system
git commit -m "feat(control): 改进像素路径规划"
```

## 11. 常见失败

| 现象 | 归属判断 | 正确处理 |
|---|---|---|
| Mask本身位置错误 | B | 保存路径图并退回B，不在C修改Mask |
| 大区域覆盖点太少 | C | 记录失败，再调整最小规则 |
| 两个污染块间出现喷射连线 | C | 检查分段起点和段间关闭规则 |
| 坐标方向反了 | 标定/C | 检查坐标系和标定点，不猜比例 |
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
Invoke-Item (Join-Path $demoDir.FullName "path_overlay.png")

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
[ ] 路径点位于图像范围和Mask内
[ ] 断开区域分段明确
[ ] 保存path_overlay和cleaning_plan
[ ] 坐标单位明确为image_px
[ ] 没有把虚拟标定写成真实标定
[ ] 没有打开真实COM口
[ ] C测试和完整回归通过
```

