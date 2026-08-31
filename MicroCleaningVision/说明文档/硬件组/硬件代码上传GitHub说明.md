# 硬件代码上传 GitHub 说明

## 1. 结论

软件、STM32 固件、接线图和机械参数使用同一个 GitHub 仓库。当前团队规模小、接口变化快，同仓库能保证“修改固件协议时，同一次 PR 就能更新电脑端说明”。

但同仓库不等于同目录。

## 2. 谁创建固件框架

**由第一个拥有可编译 STM32CubeIDE 工程的硬件组成员创建。**

软件组不会提前建立空的 `Core/Drivers`，也不会猜工程名。这样可以避免：

- CubeMX 生成目录与空壳冲突；
- `.ioc` 和芯片配置不一致；
- 文件看起来齐全但实际无法编译；
- 上传者不知道哪些文件是假的。

## 3. 第一次上传后的建议结构

```text
MicroCleaningVision/
├── microcleaning/                  # 电脑端，软件组负责
├── firmware/
│   └── nucleo_f401re/
│       └── <真实工程名>/           # 硬件组创建并负责
│           ├── Core/
│           ├── Drivers/
│           ├── <工程名>.ioc
│           ├── .project
│           ├── .cproject
│           └── README.md
├── hardware/
│   ├── wiring/                     # 接线图和器件表
│   └── mechanical/                 # 有真实资料时再创建
└── docs/
    └── 硬件组/                     # 双方共同阅读的接口说明
```

没有真实文件时，不为了展示完整而创建空目录。

## 4. 应该上传什么

### STM32 工程

- `.ioc`；
- `.c`、`.h`；
- `Core/`、`Drivers/`；
- `.project`、`.cproject`；
- 必要的链接脚本和启动文件；
- 一份说明工程怎样编译、烧录和测试的 `README.md`。

### 硬件资料

- 当前有效接线图；
- 器件型号和购买链接；
- 引脚表；
- 接线图版本和日期；
- 必要的实物接线照片。

## 5. 不要上传什么

- `Debug/`、`Release/`；
- `.o`、`.d`、`.elf`、`.map` 等可重新生成文件；
- STM32CubeIDE 安装包；
- 驱动安装包；
- 整个 IDE 工作区缓存；
- 与项目无关的厂商示例；
- 大型测试视频；
- 密码、令牌或私人路径配置。

仓库 `.gitignore` 已预留常见 CubeIDE 编译产物规则，但上传前仍要运行 `git status` 检查。

## 6. 第一次上传步骤

```powershell
git switch main
git pull
git switch -c feat/firmware-f401re-v0
```

然后把真实工程复制到：

```text
firmware/nucleo_f401re/<真实工程名>/
```

接线资料放入：

```text
hardware/wiring/
```

检查：

```powershell
git status --short
git diff --check
```

确认没有 `Debug/Release` 后再提交：

```powershell
git add firmware hardware docs/硬件组
git commit -m "feat(firmware): add nucleo f401re ping and status"
git push -u origin feat/firmware-f401re-v0
```

不要直接向 `main` 推送。

## 7. 第一个硬件 PR 必须写清

```text
1. 使用哪块开发板和哪个 STM32CubeIDE/CubeMX 版本？
2. 哪些命令已经实现？
3. USART2 参数是什么？
4. PB5、PB12 实际接到哪里？
5. 编译是否成功？
6. PING/PONG 和 STATUS 的真实输出是什么？
7. 是否连接真实水泵？
8. 哪些功能仍未验证？
```

## 8. 怎样避免双方同时改乱

- 硬件组主要修改 `firmware/` 和 `hardware/`；
- 软件组主要修改 `microcleaning/` 和 `test/`；
- `docs/硬件组/STM32最小串口协议_v0.1.md` 是共同接口，修改时双方都要看；
- 协议变化必须同时更新 Python 编码测试和固件解析；
- 接线变化必须更新接线图版本，不能只在群里说一句；
- 合并前至少由另一组一人运行或审阅。

如果未来固件形成独立产品、发布周期完全不同，再讨论拆分仓库；当前阶段拆开只会让接口更难同步。
