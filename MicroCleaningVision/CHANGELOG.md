# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- 项目初始化，创建基础工程架构
- 配置模块（config.py），集中管理所有配置参数
- 相机模块（camera/），包含相机管理、标定、立体视觉、光源控制
- 检测模块（detection/），包含检测器、预处理、追踪、后处理
- 规划模块（planning/），包含规划器、坐标计算、喷射控制、决策制定
- 通信模块（communication/），包含STM32通信、协议、错误处理、状态监控
- 数据集模块（dataset/），包含数据集管理、数据加载、数据保存
- 模型模块（models/），包含模型管理、YOLO模型、自定义模型
- 工具模块（utils/），包含日志、配置解析、图像工具、通用工具
- 统一数据类型定义（utils/types.py），使用Python dataclass
- 日志系统（utils/logger.py），基于loguru实现
- 主程序入口（main.py），统一调度所有模块
- .gitignore文件，排除不必要的文件
- CONTRIBUTING.md，贡献指南
- README.md，项目说明文档

### Changed

### Fixed

### Removed

## [0.1.0] - 2026-07-10

### Added

- 项目框架搭建完成
- 所有模块文件创建完毕
- 配置系统初始化完成
- 日志系统初始化完成
- 数据类型定义完成
- 开发规范文档完成
