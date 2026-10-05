# 公共入口、兼容工程与离线验收

## 1. 从哪里开始

这次不是重写驱动，而是把既有文件整理为 pump、motion、common 三个顶层目录，并接通主入口与已经存在的安全状态机。

| 文件 / 目录 | 做什么 | 主责 |
|---|---|---|
| ../pump/pump.c、pump.h、tests/ | PB0 泵输出、低层单元测试 | H1 |
| ../motion/stepmotor.c、stepmotor.h、tests/ | 双轴脉冲、偏移、低层单元测试 | H2 |
| main.c | 唯一当前串口入口、命令分发、计时和按钮轮询 | 共享，一次一位主改 |
| board/ | 芯片初始化、串口、毫秒时钟、实际中断转发 | 共享 |
| safety/ | 旧工程已有的纯 C 安全状态机，内容未重写 | 共享 |
| serial/mcv1_protocol.c、mcv1_protocol.h | 旧、新工程复用同一份喷水协议 | 共享 |
| keil/stage2-stepmotor.uvprojx | 当前 XY + 泵联动构建 | 共享 |
| keil/f103-stage1.uvprojx | 保留的旧喷水构建 | 共享 |
| compat/stage1/ | 旧板级入口、旧解析器和回归测试 | H1 维护兼容，H2 复核相关接口 |
| scripts/、tests/ | 本机编译与真实主入口的故障测试 | 共享 |

供应商库继续使用仓库原有 deps，不复制到两人的目录。当前入口与旧兼容入口各自只编译一次，不能把两个 main.c 塞进同一个工程。

## 2. 两条已经接通的调用链

~~~text
串口 MOVEXY → main.c → motion/sm_start_xy → TIM2/TIM3 → 已发脉冲和忙闲
串口 TO_NEEDLE / TO_SCOPE → main.c → sm_start_scope_offset → 同一个 sm_start_xy
串口 PUMP 或 MCV1 → main.c → 已有 protocol / control_core
                           ↑ 时间、PB2 急停、PB3 ARM
                           ↓ apply_safe_outputs → pump_on/off → PB0
STOP / MCV1|STOP / 急停 / FAULT → 泵关闭 + 两轴停止
~~~

针头偏移不是另一套运动驱动。继承的参数是 24 mm × 名义 320 步/mm = 7680 个 Y 脉冲；TO_NEEDLE 用 FWD，TO_SCOPE 用 REV。它是相对运动，不是回零、绝对定位或标定。

电脑的正式 Stage2SerialLink 目前不发送这两个偏移命令，而且默认每段最多 1600 步；不可为了试 7680 步绕开现有人工关卡或悄悄抬高上限。整条 Python 对中 → 偏移 → 喷洗 → 返回流程仍待下一轮明确接入。

## 3. 刻意保留与刻意改变

保留：USART2 / 115200、STEP_OK v0.3、合法运动命令和回复、X/Y 引脚、TIM2/TIM3、频率和计数方式、低电平开泵。旧兼容工程保留并可编译。

改变并需要成员知晓：

1. 旧 PUMP ON 仍回 PUMP_ON，但现在走已有状态机，只申请一次 300 ms 脉冲。到时自行关闭；重复 ON 不延长这一轮。
2. MCV1 的 PING / STATUS / 限时 PUMP / STOP 接入当前主入口；ACK 是接受申请，DONE 是输出定时结束，不是水量或清洗成功证据。固件继承范围 100–2000 ms；电脑既有编码器仍限制 100–500 ms，不能混为一个范围。
3. PB2 从浮空改为上拉。当前假设常闭触点接 PB2–GND：闭合低电平正常，按下或断线高电平锁存急停。没有连接触点时会阻止动作。这是需要现场核对的新接线要求，不是已经确认的实物事实。
4. PB3 上拉并关闭 JTAG 保留 SWD，释放 PB3 给 ARM 输入。默认 FW_REQUIRE_ARM_BUTTON=0 与旧配置一致，不宣称默认强制按 ARM。设为 1 的旧按钮防抖和原始 ARM/PUMP ON 流程已离线测试；MCV1 的额外按钮握手仍有旧限制。
5. CLEAR 只清除已解除的急停，不自动继续动作。STOP 中止喷水时保留中止回执，不伪造 DONE。
6. 运动忙时拒绝覆盖命令；非法数字/方向/尾部垃圾被拒绝；过长串口行整行丢弃。停止后挂起的中断不再多发脉冲。

主循环轮询联锁，仍可能受串口发送等影响产生延迟；它不等于独立硬件断电急停，不提供位置或流量传感器反馈。现场运行前必须核对接线和人工授权。

## 4. 离线测试：不插相机、不插板、不打开 COM

以下命令在项目根目录执行。ZigPath 换成自己的本地编译器，不自动下载。

~~~powershell
cd "D:\大创\3d\MicroCleaningVision"
& .\firmware\common\scripts\run-host-tests.ps1 `
  -ZigPath "D:\大创\tmp\hw-split-zig-0.16.0\zig-x86_64-windows-0.16.0\zig.exe"
# 如 node 不在 PATH，使用本机 node.exe 完整路径
node .\firmware\common\compat\stage1\tests\test_repository_layout.mjs
.\.venv\Scripts\python.exe -m unittest discover -s test -p "test*.py" -v
~~~

C 脚本编译真实泵、运动、主入口和状态机，用假 GPIO / 定时器代替电路：当前 5 个 C 测试程序，再运行旧兼容 7 个程序。另检查两份 Keil 工程的源文件、头文件目录及无测试替身混入。

想比较本机迁移前保存的三项测试，可额外指定：

~~~powershell
& .\firmware\common\scripts\run-host-tests.ps1 `
  -ZigPath "D:\大创\tmp\hw-split-zig-0.16.0\zig-x86_64-windows-0.16.0\zig.exe" `
  -BaselineRoot "output\firmware_audit\2026-10-05\before"
~~~

before 是本机未提交的现场备份，不在 Git 中。换电脑缺少备份时不要传 BaselineRoot；默认回归不依赖它。

### 再检查现有 Python 与实际 C 入口能否对接

~~~powershell
& .\firmware\common\scripts\run-host-tests.ps1 `
  -ZigPath "D:\大创\tmp\hw-split-zig-0.16.0\zig-x86_64-windows-0.16.0\zig.exe" `
  -PythonPath ".\.venv\Scripts\python.exe"
~~~

额外编译协议对端：用已有 Python 编码器发送，实际 C main/core/驱动处理，再用已有 Python 解析器读回复。19项交互包含16个协议请求和3个模拟时间/中断/急停推进；逐项收发写入.tools中的host_contract_transcript.json。只使用进程标准输入输出，无真实COM；不改变主机授权或业务程序。

## 5. 两份 Keil 工程的完整编译

Keil 已安装且有对应 Device Pack / CMSIS 时，在项目根目录执行：

~~~powershell
& .\firmware\common\scripts\build-local.ps1 `
  -KeilRoot "D:\大创\Keil_v5" `
  -DevicePackRoot "D:\大创\Keil_v5\ARM\PACK\Keil\STM32F1xx_DFP\2.4.1" `
  -CmsisInclude "D:\大创\Keil_v5\ARM\PACK\ARM\CMSIS\5.8.0\CMSIS\Core\Include"

& .\firmware\common\scripts\build-local.ps1 `
  -KeilRoot "D:\大创\Keil_v5" `
  -DevicePackRoot "D:\大创\Keil_v5\ARM\PACK\Keil\STM32F1xx_DFP\2.4.1" `
  -CmsisInclude "D:\大创\Keil_v5\ARM\PACK\ARM\CMSIS\5.8.0\CMSIS\Core\Include" `
  -Project f103-stage1
~~~

脚本复制一份临时工程，按显式参数适配本机库目录，只编译、不下载、不烧录。原工程供应商安装路径保持不变。旧、新 HAL 有同名头文件，脚本分别设置各自 include 路径，不能混用。

输出在 common/.tools/keil/<随机编号>/build.log 与 Objects/。这些是 Git 忽略的本机产物；keil/compile*.log 是迁移保留的历史日志，不是本次结果。

## 6. 下一次真实联调前

H1 核对 PB0 模块极性、PB2 常闭急停接线、定时关泵与实际出水；H2 核对方向、步距、24 mm 偏移及真实行程；双方确认固件版本。C 另外修主机各入口的统一授权、失败不喷、异常补关泵和单一有符号偏移。未经这些确认不要直接运行专用对中喷水脚本。

- [硬件组入口及两人任务](../../说明文档/硬件组/README.md)
- [唯一串口与参数约定](../../说明文档/硬件组/串口协议与参数.md)
- [本次迁移、对抗性审查和验收](../../说明文档/硬件组/结构整理与验收记录_2026-10-05.md)
