# 贡献指南

欢迎贡献代码！请遵循以下流程和规范。

## 代码贡献流程

### 1. Fork项目

在GitHub上Fork本项目到你的个人仓库。

### 2. 克隆代码

```bash
git clone https://github.com/your-username/MicroCleaningVision.git
cd MicroCleaningVision
```

### 3. 添加上游仓库

```bash
git remote add upstream https://github.com/original-owner/MicroCleaningVision.git
```

### 4. 创建开发分支

```bash
git checkout -b feature/your-feature-name
```

分支命名规范：
- 新功能: `feature/xxx-description`
- Bug修复: `bugfix/xxx-description`
- 文档更新: `docs/xxx-description`

### 5. 开发代码

遵循项目的编码规范：
- 遵循PEP 8
- 添加详细的文档字符串
- 使用类型提示
- 添加必要的测试

### 6. 提交代码

```bash
git add .
git commit -m "feat(module): description"
```

Commit规范请参考README.md中的Commit规范章节。

### 7. 同步上游代码

```bash
git fetch upstream
git rebase upstream/develop
```

### 8. 推送分支

```bash
git push origin feature/your-feature-name
```

### 9. 创建Pull Request

在GitHub上创建Pull Request，描述清楚你的修改：
- 实现了什么功能
- 修改了哪些文件
- 如何测试的

## 代码审查

### 审查要求

1. **代码质量**:
   - 代码易于理解
   - 变量命名清晰
   - 避免重复代码

2. **测试**:
   - 关键逻辑有测试用例
   - 测试覆盖率达标

3. **文档**:
   - 添加必要的文档字符串
   - 更新相关文档

4. **合规性**:
   - 符合编码规范
   - 符合项目架构设计

### 审查流程

1. 创建PR后自动触发CI/CD
2. 至少一位开发者审查
3. 审查通过后合并到develop分支

## 报告问题

### Bug报告

请在GitHub Issues中报告bug，包含以下信息：
- 问题描述
- 复现步骤
- 预期结果
- 实际结果
- 环境信息（Python版本、操作系统等）

### 功能请求

请在GitHub Issues中请求新功能，包含以下信息：
- 功能描述
- 需求背景
- 预期效果
- 优先级

## 开发环境

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行测试

```bash
pytest test/ -v
```

### 代码格式化

```bash
black .
```

### 代码检查

```bash
flake8 .
```

## 贡献类型

### 代码贡献
- 实现新功能
- 修复bug
- 优化性能

### 文档贡献
- 更新README.md
- 添加API文档
- 编写使用指南

### 测试贡献
- 添加测试用例
- 完善测试覆盖

### 其他贡献
- 改进项目架构
- 优化代码结构
- 提供使用建议

## 注意事项

1. **不要修改无关文件**
2. **保持代码风格一致**
3. **提交前确保测试通过**
4. **遵守项目的许可证**

## 许可证

本项目采用MIT License，所有贡献代码将遵循相同的许可证。

## 联系我们

如有问题，请通过以下方式联系：
- GitHub Issues
- 项目讨论区

感谢你的贡献！
