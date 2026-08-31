# Git 与 GitHub 协作教程：三名新手也不会把项目改乱

## 1. 先用大白话理解四个东西

| 名词 | 大白话解释 |
|---|---|
| 工作区 | 你电脑上正在修改的文件 |
| Commit（提交） | 给一组相关修改拍一张带说明的快照 |
| Branch（分支） | 从主线分出的独立施工通道 |
| GitHub | 团队共享提交、分支和PR的远程平台 |

Git不自动理解哪个文件重要。它只记录变化，所以每次提交前必须由人检查。

## 2. 本项目的基本规则

仓库地址：`https://github.com/fangfangzhouhang/3d.git`

```text
main                     稳定主线
feat/a-...               A的数据与模型任务
feat/b-...               B的视觉任务
feat/c-...               C的控制仿真任务
docs/...                 纯文档任务
fix/...                  明确的小修复
```

每张任务卡一个分支、一个主要业务目录、一个PR。不要在同一PR里同时重写A/B/C三个目录。

## 3. 第一次在新电脑克隆

在准备存放项目的父目录打开PowerShell：

```powershell
git clone https://github.com/fangfangzhouhang/3d.git
cd 3d\MicroCleaningVision
git remote -v
git status
```

`git remote -v` 应显示 `origin` 指向上面的地址。

### 重新创建虚拟环境

虚拟环境 `.venv` 不进入Git。它包含本机解释器路径和已安装二进制包，跨电脑复制容易损坏，而且体积大。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\check_environment.py --profile mock
```

需要OpenCV任务时才安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements\perception-opencv.txt
```

结论：换电脑时同步代码和依赖清单，不同步 `.venv`。

## 4. 开始任务前的标准动作

### 第一步：确认位置和当前变化

```powershell
git status
git branch --show-current
```

如果看到不认识的修改，先询问文件所有者，不能直接删除、覆盖或重置。

### 第二步：更新主线

只有当前工作区干净、并且你准备从主线开新任务时：

```powershell
git switch main
git pull --ff-only origin main
```

`--ff-only` 表示只接受清晰的快进更新，避免Git在不知情时自动制造合并提交。

### 第三步：创建自己的分支

```powershell
git switch -c feat/a-usb-dataset-v0
```

B和C替换为自己的前缀。分支名应包含负责人和能力，不写 `new`、`test2`、`最终版`。

## 5. 开发过程中怎样提交

### 先看差异

```powershell
git status --short
git diff
```

只暂存你确认过的文件：

```powershell
git add microcleaning/data_learning/image_quality.py
git add test/data_learning/test_data_learning.py
git diff --cached
```

不要习惯性使用 `git add .`。它可能把个人配置、临时输出或别人的修改一起带进去。

### 提交说明

推荐格式：

```text
feat(data): add USB image manifest validation
fix(vision): reject empty contamination mask
test(control): cover no-target planning
docs(team): explain parallel task packages
```

提交命令：

```powershell
git commit -m "feat(data): add USB image manifest validation"
```

一次提交解决一个小问题。提交说明应回答“增加了什么能力”，而不是“更新代码”。

## 6. 推送和创建PR

第一次推送当前分支：

```powershell
git push -u origin feat/a-usb-dataset-v0
```

然后在GitHub创建Pull Request（合并请求，邀请别人审查分支变化）。目标分支选择 `main`。

### PR必须回答

```text
1. 任务编号和要解决的问题是什么？
2. 修改了哪个负责人目录？
3. 输入和输出是什么？
4. 怎样运行？
5. 哪些测试通过？
6. 产生了什么真实/模拟证据？
7. 什么仍未验证？
8. 是否改变共享接口？
9. 风险和回滚方式是什么？
```

### 审查关系

- A的输出由B检查是否可用于视觉；
- B的输出由C检查是否可用于目标规划；
- C的输出由A或B检查是否忠实使用上游数据；
- 共享契约至少两人确认。

审查者指出问题，文件所有者自己修改。不要直接冲进对方分支重写。

## 7. 两台电脑之间切换

### 离开电脑一之前

```powershell
git status
git add <确认过的文件>
git commit -m "wip(data): record validated capture manifest progress"
git push
```

如果修改还不能形成完整功能，可以使用清楚标记的WIP提交；不要依赖某台电脑的未提交文件。

### 到电脑二之后

首次克隆后：

```powershell
git fetch origin
git switch feat/a-usb-dataset-v0
git pull --ff-only
```

如果本地还没有该分支：

```powershell
git switch --track origin/feat/a-usb-dataset-v0
```

每台电脑单独建立 `.venv`，不要从GitHub下载或提交虚拟环境。

## 8. 三个人同时工作怎样避免冲突

目录所有权已经提供第一层保护：

```text
A只改 data_learning/
B只改 vision/
C只改 control_system/
```

共享文件确实需要修改时：

1. 先写接口变更提案；
2. 暂停其他人对该共享文件的修改；
3. 单独分支和单独PR；
4. 更新全部消费者测试；
5. 合并后其他分支再同步。

不要通过复制同一函数到三个目录“避免冲突”，那会制造更严重的重复逻辑。

## 9. 同步主线到自己的分支

先保存并推送自己的工作，然后：

```powershell
git fetch origin
git switch feat/a-usb-dataset-v0
git merge origin/main
```

新手阶段推荐使用明确的merge，不强制rebase。出现冲突时不要盲选“接受全部当前”或“接受全部传入”。

### 冲突处理步骤

1. `git status` 查看冲突文件；
2. 打开冲突位置，理解两边修改；
3. 如果是别人拥有的业务文件，联系所有者共同决定；
4. 删除冲突标记，保留正确内容；
5. 运行相关测试；
6. `git add <已解决文件>`；
7. `git commit` 完成合并。

如果无法判断，停止并求助。不要使用 `git reset --hard` 或强推来“快速解决”。

## 10. 哪些东西不能进Git

- `.venv/`、`venv/`：每台电脑重建；
- `__pycache__/`：Python自动生成；
- 原始大图和实验视频：通过数据存储和清单管理；
- 模型权重 `.pt/.pth/.onnx`：单独版本管理；
- API密钥、密码、`.env`；
- IDE个人配置；
- 运行输出和临时文件。

可以进入Git：小型脱敏示例、标注JSON、数据清单、实验协议、依赖清单、测试和代码。

## 11. 常见错误

### `git pull`提示本地修改会被覆盖

先运行 `git status`。把自己的修改提交到当前功能分支；如果混入别人修改，先确认所有权。不要直接丢弃。

### 推送被拒绝

可能远程分支有新提交：

```powershell
git pull --ff-only
```

若不能快进，说明双方都提交过，需要合并并处理冲突。

### 把 `.venv` 加进暂存区

如果还没提交：

```powershell
git restore --staged .venv
```

这只取消暂存，不删除本地虚拟环境。确认 `.gitignore` 中仍有 `.venv/`。

### 提交了大模型文件

不要继续反复推送。停止操作，告诉团队文件名和提交位置，再决定安全地从历史或提交中移除。未经确认不要重写公共历史。

### 中文路径或终端乱码

Python文件读写显式使用UTF-8；Git仍能追踪中文文件名。终端乱码不一定表示文件内容损坏，先用编辑器打开确认。

## 12. 合并前检查清单

```text
[ ] 只修改了任务卡允许的目录
[ ] 没有提交原始大数据、权重、venv或密钥
[ ] git diff --cached 已人工阅读
[ ] 自己模块测试通过
[ ] 全部软件回归通过
[ ] PR写清真实证据和未验证内容
[ ] 下游成员能够理解并运行输出
[ ] project_state只写已发生事实
```

## 13. 一次标准工作示例

```powershell
git switch main
git pull --ff-only origin main
git switch -c feat/b-hsv-baseline-v0

# 修改 vision/ 和 test/vision/
.\.venv\Scripts\python.exe -m unittest discover -s test\vision -p "test*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s test -p "test*.py" -v

git status --short
git diff
git add microcleaning/vision
git add test/vision
git diff --cached
git commit -m "feat(vision): add HSV contamination baseline"
git push -u origin feat/b-hsv-baseline-v0
```

最后去GitHub创建PR，不直接在 `main` 上继续开发。
