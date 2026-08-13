# MicroCleaningVision 项目架构说明文档

## 一、项目概述

MicroCleaningVision 是一个基于双显微镜（顶部+45°）的智能微污染检测与清洗系统。系统通过工业相机采集图像，利用AI模型识别污染物，计算目标坐标，规划清洗路径，并通过串口控制STM32执行清洗操作。

**核心流程：**
1. 顶部显微镜采集图像
2. 45°显微镜采集图像
3. 图像预处理
4. AI识别污染
5. 三维重建（后期）
6. 坐标计算
7. 发送STM32指令
8. 控制XY平台移动
9. 喷头喷射清洗
10. 返回检测
11. 判断是否继续清洗

## 二、目录结构总览

```
MicroCleaningVision/
├── camera/              # 相机管理模块
├── communication/       # 串口通信模块
├── dataset/             # 数据集管理模块
├── detection/           # AI检测模块
├── docs/                # 文档目录
├── models/              # 模型封装模块
├── planning/            # 路径规划模块
├── reconstruction/      # 三维重建模块（后期开发）
├── test/                # 测试脚本
├── training/            # 模型训练模块
├── utils/               # 工具模块
├── config.py            # 集中配置文件
├── main.py              # 主程序入口
├── requirements.txt     # 依赖清单
└── test_env.py          # 环境测试脚本
```

## 三、核心目录功能说明

### 3.1 根目录文件

| 文件 | 功能定位 | 作用说明 |
|------|----------|----------|
| main.py | 系统调度中心 | 创建主控制器，初始化所有模块，协调清洗循环流程 |
| config.py | 集中配置管理 | 定义所有配置类，统一管理系统参数 |
| requirements.txt | 依赖管理 | 列出项目所需的第三方库及其版本 |
| test_env.py | 环境测试 | 验证项目环境配置是否正确 |

### 3.2 camera/ - 相机管理模块

**设计目的**：负责管理顶部和45°两个工业相机的连接、采集和控制。

| 文件 | 类名 | 功能说明 |
|------|------|----------|
| camera.py | CameraManager | 相机管理器，统一管理多相机连接、图像采集、参数设置 |
| calibration.py | CameraCalibration | 摄像头标定，计算内外参矩阵和畸变系数 |
| light.py | LightController | 光源控制器，实现亮度调节和自适应光照补偿 |
| stereo.py | StereoVision | 双目立体视觉，计算视差图和深度信息 |

**模块职责**：
- CameraManager 作为对外接口，提供统一的相机操作入口
- CameraCalibration 为坐标计算提供标定参数
- LightController 优化图像采集质量
- StereoVision 为三维重建提供深度数据

### 3.3 communication/ - 串口通信模块

**设计目的**：负责与STM32微控制器的串口通信，发送控制指令并接收状态反馈。

| 文件 | 类名 | 功能说明 |
|------|------|----------|
| stm32_comm.py | STM32Communicator | 通信器，提供串口连接、指令发送、状态查询接口 |
| protocol.py | CommunicationProtocol | 通信协议，定义指令格式、编解码和校验和计算 |
| error_handler.py | CommunicationErrorHandler | 错误处理器，处理通信异常和重试逻辑 |
| status_monitor.py | CommunicationStatusMonitor | 状态监控，实时监控通信状态和心跳检测 |

**模块职责**：
- STM32Communicator 作为对外接口，提供 move/spray/stop 等高层指令
- CommunicationProtocol 处理底层协议细节（字节编码、校验和）
- CommunicationErrorHandler 提供错误恢复策略
- CommunicationStatusMonitor 确保通信链路稳定性

### 3.4 detection/ - AI检测模块

**设计目的**：负责图像预处理、AI模型推理和检测结果后处理。

| 文件 | 类名 | 功能说明 |
|------|------|----------|
| detector.py | Detector | 检测器，协调整个检测流程 |
| preprocessing.py | ImagePreprocessor | 图像预处理，去噪、增强、光照补偿 |
| postprocessing.py | DetectionPostprocessor | 检测后处理，过滤、筛选、合并检测结果 |
| tracker.py | TargetTracker | 目标跟踪，处理目标移动和丢失情况 |

**模块职责**：
- Detector 作为对外接口，提供 load_model/detect/detect_single 等方法
- ImagePreprocessor 优化输入图像质量，提高检测精度
- DetectionPostprocessor 过滤低质量检测结果，生成标准化输出
- TargetTracker 在连续帧之间跟踪目标，保持ID连续性

### 3.5 models/ - 模型封装模块

**设计目的**：封装AI模型的加载和推理接口。

| 文件 | 类名 | 功能说明 |
|------|------|----------|
| yolo_model.py | YOLOModel | YOLO模型封装，实现模型加载、推理、预热（已实现） |
| model_manager.py | ModelManager | 模型管理器（空实现，预留多模型管理能力） |
| custom_model.py | CustomModel | 自定义模型封装（空实现，预留扩展能力） |

**模块职责**：
- YOLOModel 是当前核心实现，封装Ultralytics YOLO库的调用
- ModelManager 和 CustomModel 为空实现框架，预留多模型切换和自定义模型扩展能力

### 3.6 planning/ - 路径规划模块

**设计目的**：负责坐标计算、清洗路径规划和清洗策略决策。

| 文件 | 类名 | 功能说明 |
|------|------|----------|
| planner.py | Planner | 规划器，协调整个规划流程 |
| coordinate_calculator.py | CoordinateCalculator | 坐标计算器，像素坐标转物理坐标 |
| decision_maker.py | DecisionMaker | 决策制定器，制定清洗策略和优先级 |
| spray_controller.py | SprayController | 喷水控制器，控制喷水时机、持续时间和强度 |

**模块职责**：
- Planner 作为对外接口，提供 calculate_coordinates/plan_paths/decide_continue_cleaning 等方法
- CoordinateCalculator 将像素坐标转换为物理世界坐标
- DecisionMaker 根据检测结果决定清洗顺序和优先级
- SprayController 根据污渍特征计算喷水参数

### 3.7 reconstruction/ - 三维重建模块

**设计目的**：负责从双摄像头图像中恢复三维信息（后期开发）。

| 文件 | 类名 | 功能说明 |
|------|------|----------|
| three_d_reconstructor.py | ReconstructionManager | 重建管理器，协调整个重建流程 |
| depth_map.py | DepthMapGenerator | 深度图生成器，从双目图像生成深度图 |
| point_cloud.py | PointCloudProcessor | 点云处理器，点云去噪、下采样、配准 |

**模块职责**：
- ReconstructionManager 作为对外接口，提供 reconstruct/generate_depth_map 等方法
- DepthMapGenerator 生成深度图和视差图
- PointCloudProcessor 处理和优化点云数据

### 3.8 utils/ - 工具模块

**设计目的**：提供系统级的通用工具和基础组件。

| 文件 | 类/对象 | 功能说明 |
|------|---------|----------|
| logger.py | Logger / logger / ExperimentManager | 日志系统，统一记录日志和管理实验编号（已实现） |
| types.py | dataclass集合 | 数据类型定义，所有模块间传递的数据结构（已实现） |
| exceptions.py | 异常类集合 | 自定义异常，统一错误处理（已实现） |
| common_utils.py | CommonUtils | 通用工具（空实现，预留基础工具能力） |
| image_utils.py | ImageUtils | 图像处理工具（空实现，预留图像操作能力） |

**模块职责**：
- Logger 提供统一的日志记录接口，所有模块共享同一个实例
- types.py 定义了所有数据交换格式（Frame、Detection、Target等）
- exceptions.py 定义了各模块的自定义异常类型
- common_utils.py 和 image_utils.py 为空实现框架，预留通用工具能力

### 3.9 dataset/ - 数据集管理模块

**设计目的**：负责数据集的组织、加载和保存。

| 文件 | 类名 | 功能说明 |
|------|------|----------|
| dataset.py | DatasetManager | 数据集管理器，实现数据集加载、划分、统计和验证 |
| data_loader.py | DataLoader | 数据加载器，实现图像和标签的批量加载，支持数据增强 |
| data_saver.py | DataSaver | 数据保存器，实现图像、标签和检测结果的保存 |

**模块职责**：
- DatasetManager 作为对外接口，提供数据集管理的完整功能
- DataLoader 为模型训练提供批量数据加载能力
- DataSaver 保存采集数据和检测结果，支持数据回溯和分析

### 3.10 training/ - 模型训练模块

| 文件 | 功能说明 |
|------|----------|
| trainer.py | YOLO模型训练脚本，实现数据集训练、验证和评估 |

## 四、模块间依赖关系

### 4.1 核心依赖图

```
main.py (主控制器)
├── config.py (配置) [只读依赖]
├── camera.CameraManager (相机)
│   ├── camera.CameraCalibration (标定)
│   ├── camera.LightController (光源)
│   └── camera.StereoVision (立体视觉)
├── detection.Detector (检测)
│   ├── detection.ImagePreprocessor (预处理)
│   ├── detection.DetectionPostprocessor (后处理)
│   ├── detection.TargetTracker (跟踪)
│   └── models.YOLOModel (YOLO模型)
├── dataset.DatasetManager (数据集)
│   ├── dataset.DataLoader (数据加载)
│   └── dataset.DataSaver (数据保存)
├── planning.Planner (规划)
│   ├── planning.CoordinateCalculator (坐标计算)
│   ├── planning.DecisionMaker (决策)
│   └── planning.SprayController (喷水控制)
├── communication.STM32Communicator (通信)
│   ├── communication.CommunicationProtocol (协议)
│   ├── communication.CommunicationErrorHandler (错误处理)
│   └── communication.CommunicationStatusMonitor (状态监控)
└── utils.logger (日志) [全局共享]

reconstruction.ReconstructionManager (三维重建，可选)
├── reconstruction.DepthMapGenerator (深度图)
└── reconstruction.PointCloudProcessor (点云)
```

### 4.2 数据流向

```
相机采集 → Frame (types.py) → 图像预处理 → YOLO推理 → DetectionResult (types.py)
                                                           ↓
                                                      坐标计算 → WorldPoint (types.py)
                                                           ↓
                                                      路径规划 → CleaningPath (types.py)
                                                           ↓
                                                      串口通信 → CommandResult (types.py)
```

## 五、冗余结构分析

### 5.1 已移除的冗余结构

| 结构 | 移除原因 | 替代方案 |
|------|----------|----------|
| stain_detection.py (根目录) | 独立脚本，与MicroCleaningVision包功能重复 | 使用main.py + detection模块 |
| __pycache__/ (根目录) | Python缓存目录，不应提交到版本控制 | 通过.gitignore忽略 |
| config_parser.py | 与config.py功能重叠，config.py已完整实现配置管理 | 使用config.py |

### 5.2 保留的空实现框架

以下空实现框架被保留，用于后续功能扩展：

| 结构 | 保留原因 | 预期用途 |
|------|----------|----------|
| model_manager.py | 预留多模型管理能力 | 后期支持多种AI模型切换 |
| custom_model.py | 预留自定义模型扩展能力 | 支持用户自定义模型接入 |
| common_utils.py | 预留通用工具能力 | 提供时间、文件、数学等基础工具 |
| image_utils.py | 预留图像处理工具能力 | 提供图像读写、转换、可视化等工具 |

### 5.3 功能重复分析

| 重复功能 | 位置1 | 位置2 | 职责划分 |
|----------|-------|-------|----------|
| 距离计算 | common_utils.calculate_distance | coordinate_calculator.calculate_distance | common_utils用于通用计算，coordinate_calculator用于坐标系统内计算 |
| 角度计算 | common_utils.calculate_angle | coordinate_calculator.calculate_angle | 同上 |
| 模型加载 | detector.load_model | model_manager.load_model | detector负责检测流程中的模型管理，model_manager预留多模型管理 |

## 六、架构设计原则

### 6.1 高度解耦

所有模块通过config.py和utils/types.py进行数据交换，模块之间不直接调用，由main.py统一调度。

### 6.2 集中配置

所有参数集中在config.py中管理，避免魔法数字散布在代码中。

### 6.3 统一日志

日志系统通过utils/logger.py实现全局共享，确保所有模块使用统一的日志格式。

### 6.4 数据类型标准化

模块间传递的数据必须使用utils/types.py中定义的dataclass，禁止直接使用tuple或dict。

### 6.5 预留扩展能力

保留空实现框架（如model_manager、custom_model），便于后续添加新功能。

## 七、使用方法

### 7.1 启动系统

```python
from main import MainController

controller = MainController()
controller.start()
```

### 7.2 访问配置

```python
from config import Config

config = Config()
camera_id = config.camera.top_camera_id
threshold = config.detection.confidence_threshold
port = config.communication.port
```

### 7.3 记录日志

```python
from utils.logger import logger

logger.info("操作成功", module="Camera", function="capture")
logger.error("操作失败", module="Detection", function="detect")
```

### 7.4 使用数据类型

```python
from utils.types import Detection, Frame, Target

detection = Detection(
    detection_id="det_001",
    label="dust",
    confidence=0.95,
    bbox=(100, 100, 200, 200),
    center=(150, 150),
    area=10000
)
```

## 八、扩展指南

### 8.1 添加新模型

1. 在models/目录下创建新的模型封装类（如CustomModel）
2. 在models/__init__.py中导出新类
3. 在detection/detector.py中集成新模型

### 8.2 添加新功能模块

1. 在根目录下创建新的模块目录
2. 在模块目录中创建核心类和辅助文件
3. 在main.py中添加模块初始化和调度逻辑
4. 在config.py中添加对应的配置类

### 8.3 添加新数据类型

1. 在utils/types.py中定义新的dataclass
2. 在utils/__init__.py中导出新类型
3. 在相关模块中使用新类型进行数据传递

## 九、已修复的问题

1. **detector.py 属性名错误**：修复了 `config.models.yolo_model_path` → `config.model.yolo_model_path`（config.py中使用的是 `model` 属性而非 `models`）

2. **yolo_model.py 属性名错误**：修复了 `config.models.yolo_model_path` → `config.model.yolo_model_path`

3. **根目录遗留文件**：删除了 `stain_detection.py` 和 `__pycache__/`，这些是包外的独立脚本和缓存文件

4. **配置解析器冗余**：删除了 `utils/config_parser.py`，其功能已由 `config.py` 完整实现

5. **模块导出清理**：更新了 `models/__init__.py` 和 `utils/__init__.py`，移除了对已删除模块的引用

6. **方法名不匹配**：在 `preprocessing.py` 中添加了 `process()` 方法，在 `postprocessing.py` 中添加了 `process()` 和 `merge_results()` 方法，确保与 `detector.py` 中的调用一致

## 十、关键设计决策

### 10.1 保留空实现框架

尽管部分模块（如model_manager.py、custom_model.py、common_utils.py、image_utils.py）目前为空实现，但这些框架为后续功能扩展预留了空间：
- ModelManager：未来支持多模型切换和版本管理
- CustomModel：未来支持用户自定义模型接入
- CommonUtils：提供通用工具函数
- ImageUtils：提供图像处理工具函数

### 10.2 统一接口设计

所有核心模块（Detection、Planning、Communication等）都采用统一的接口设计模式：
- 对外提供高层接口方法
- 内部通过子模块实现具体功能
- 使用config.py获取配置参数
- 使用logger记录日志

### 10.3 数据类型标准化

通过utils/types.py定义了所有数据交换格式，确保：
- 模块间传递的数据结构清晰
- 便于类型检查和代码提示
- 避免使用tuple/dict等松散数据结构
- 支持序列化和反序列化扩展