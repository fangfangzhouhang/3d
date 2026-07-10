# MicroCleaningVision 架构文档

## 1. 软件架构图（数据流）

```mermaid
flowchart TD
    subgraph 硬件层
        STM32["STM32\n微控制器"]
        XY平台["XY平台"]
        喷头["清洗喷头"]
    end
    
    subgraph 软件层
        direction LR
        
        subgraph Camera["Camera模块"]
            top_cam["顶部显微镜"]
            angle_cam["45°显微镜"]
        end
        
        subgraph Detection["Detection模块"]
            preprocess["图像预处理"]
            ai_detect["AI污染识别"]
            postprocess["后处理"]
        end
        
        subgraph Reconstruction["Reconstruction模块"]
            depth["深度图生成"]
            pointcloud["点云处理"]
            reconstruct["三维重建"]
        end
        
        subgraph Planning["Planning模块"]
            coord["坐标计算"]
            path["路径规划"]
            spray["喷射控制"]
            decision["决策制定"]
        end
        
        subgraph Communication["Communication模块"]
            protocol["通信协议"]
            send["指令发送"]
            receive["状态接收"]
        end
        
        Main["main.py\n主控制器"]
    end
    
    top_cam -->|Frame| preprocess
    angle_cam -->|Frame| preprocess
    
    preprocess -->|预处理图像| ai_detect
    ai_detect -->|DetectionResult| postprocess
    postprocess -->|DetectionResult| coord
    
    depth -->|DepthMap| pointcloud
    pointcloud -->|PointCloud| reconstruct
    reconstruct -->|3DModel| coord
    
    coord -->|WorldPoint| path
    path -->|CleaningPath| spray
    spray -->|Target| decision
    
    decision -->|Target| protocol
    protocol -->|指令| send
    send -->|串口数据| STM32
    
    STM32 -->|状态| receive
    receive -->|CommandResult| decision
    
    STM32 -->|控制信号| XY平台
    STM32 -->|控制信号| 喷头
    
    Main -->|调度| Camera
    Main -->|调度| Detection
    Main -->|调度| Reconstruction
    Main -->|调度| Planning
    Main -->|调度| Communication
    
    style Camera fill:#4CAF50,color:#fff
    style Detection fill:#2196F3,color:#fff
    style Reconstruction fill:#FF9800,color:#fff
    style Planning fill:#9C27B0,color:#fff
    style Communication fill:#F44336,color:#fff
    style Main fill:#00BCD4,color:#fff
```

## 2. 模块依赖图

```mermaid
graph TD
    subgraph 核心模块
        Main["main.py\n主控制器"]
    end
    
    subgraph 功能模块
        Camera["camera模块"]
        Detection["detection模块"]
        Reconstruction["reconstruction模块"]
        Planning["planning模块"]
        Communication["communication模块"]
        Dataset["dataset模块"]
        Models["models模块"]
    end
    
    subgraph 工具模块
        Utils["utils模块"]
        Config["config.py\n配置管理"]
        Logger["logger.py\n日志管理"]
        Types["types.py\n数据类型"]
    end
    
    Main -->|导入| Camera
    Main -->|导入| Detection
    Main -->|导入| Reconstruction
    Main -->|导入| Planning
    Main -->|导入| Communication
    Main -->|导入| Dataset
    Main -->|导入| Models
    Main -->|导入| Utils
    Main -->|导入| Config
    
    Camera -->|导入| Config
    Camera -->|导入| Logger
    Camera -->|导入| Types
    
    Detection -->|导入| Config
    Detection -->|导入| Logger
    Detection -->|导入| Types
    
    Reconstruction -->|导入| Config
    Reconstruction -->|导入| Logger
    Reconstruction -->|导入| Types
    
    Planning -->|导入| Config
    Planning -->|导入| Logger
    Planning -->|导入| Types
    
    Communication -->|导入| Config
    Communication -->|导入| Logger
    Communication -->|导入| Types
    
    Dataset -->|导入| Config
    Dataset -->|导入| Logger
    Dataset -->|导入| Types
    
    Models -->|导入| Config
    Models -->|导入| Logger
    Models -->|导入| Types
    
    Utils -->|导入| Config
    
    style Main fill:#00BCD4,color:#fff,stroke:#0097A7,stroke-width:2px
    style Utils fill:#795548,color:#fff
    style Config fill:#607D8B,color:#fff
    style Logger fill:#795548,color:#fff
    style Types fill:#795548,color:#fff
    
    style Camera fill:#4CAF50,color:#fff
    style Detection fill:#2196F3,color:#fff
    style Reconstruction fill:#FF9800,color:#fff
    style Planning fill:#9C27B0,color:#fff
    style Communication fill:#F44336,color:#fff
    style Dataset fill:#E91E63,color:#fff
    style Models fill:#8BC34A,color:#fff
```

## 3. 数据类依赖关系

```mermaid
classDiagram
    class Frame {
        +image: numpy.ndarray
        +timestamp: datetime
        +camera_info: CameraInfo
        +frame_id: int
    }
    
    class CameraInfo {
        +camera_id: int
        +camera_type: str
        +resolution: tuple
        +fps: float
        +exposure: float
        +gain: float
    }
    
    class Detection {
        +bbox: tuple
        +confidence: float
        +class_id: int
        +class_name: str
        +center: tuple
    }
    
    class DetectionResult {
        +detections: list[Detection]
        +image_shape: tuple
        +timestamp: datetime
        +source_camera: str
    }
    
    class WorldPoint {
        +x: float
        +y: float
        +z: float
        +unit: str
    }
    
    class Target {
        +id: int
        +world_point: WorldPoint
        +priority: int
        +status: str
        +spray_duration: int
        +spray_pressure: float
    }
    
    class PathPoint {
        +x: float
        +y: float
        +z: float
        +speed: float
        +acceleration: float
    }
    
    class CleaningPath {
        +path_id: int
        +points: list[PathPoint]
        +total_distance: float
        +estimated_time: float
    }
    
    class CalibrationResult {
        +camera_matrix: numpy.ndarray
        +distortion_coefficients: numpy.ndarray
        +rotation_matrix: numpy.ndarray
        +translation_vector: numpy.ndarray
        +reprojection_error: float
    }
    
    class SystemStatus {
        +camera_status: str
        +detection_status: str
        +planning_status: str
        +communication_status: str
        +is_running: bool
        +current_cycle: int
    }
    
    class SystemState {
        +status: SystemStatus
        +last_update: datetime
        +errors: list[str]
    }
    
    class CommandResult {
        +command_id: int
        +command_type: str
        +success: bool
        +message: str
        +timestamp: datetime
    }
    
    class CleaningResult {
        +target_id: int
        +success: bool
        +effectiveness: float
        +before_image: Frame
        +after_image: Frame
        +timestamp: datetime
    }
    
    Frame --> CameraInfo
    DetectionResult --> Detection
    Target --> WorldPoint
    CleaningPath --> PathPoint
    PathPoint --> WorldPoint
    SystemState --> SystemStatus
    CleaningResult --> Frame
```

## 4. 系统状态机

```mermaid
stateDiagram-v2
    [*] --> Idle: 系统启动
    
    Idle --> Capturing: start_cleaning()
    Capturing --> Detecting: 图像采集完成
    
    Detecting --> Planning: 检测完成
    Detecting --> Idle: 未检测到污染物
    
    Planning --> Communicating: 路径规划完成
    
    Communicating --> Executing: 指令发送成功
    
    Executing --> Evaluating: 清洗完成
    
    Evaluating --> Capturing: 需要继续清洗
    Evaluating --> Idle: 清洗完成
    
    state Error {
        [*] --> CameraError: 相机错误
        [*] --> CommunicationError: 通信错误
        [*] --> DetectionError: 检测错误
        
        CameraError --> Idle: 修复相机
        CommunicationError --> Idle: 修复通信
        DetectionError --> Idle: 修复检测
    }
    
    Capturing --> CameraError: 相机故障
    Communicating --> CommunicationError: 通信故障
    Detecting --> DetectionError: 检测故障
    
    Idle --> [*]: stop_system()
```

## 5. 模块职责矩阵

| 模块 | 核心职责 | 输入数据 | 输出数据 | 关键类 |
|------|----------|----------|----------|--------|
| camera | 图像采集与控制 | - | Frame | CameraManager |
| detection | AI污染检测 | Frame | DetectionResult | Detector |
| reconstruction | 三维重建 | Frame | PointCloud/DepthMap | ReconstructionManager |
| planning | 路径规划与决策 | DetectionResult/PointCloud | Target/CleaningPath | Planner |
| communication | STM32通信 | Target/CleaningPath | CommandResult | STM32Communicator |
| dataset | 数据集管理 | - | Dataset | DatasetManager |
| models | 模型管理 | - | Model | ModelManager |
| utils | 工具函数 | - | - | Logger/ConfigParser |

## 6. 设计原则总结

### 高内聚低耦合
- 每个模块只负责一个核心功能
- 模块之间通过统一数据类型交互
- 避免模块间的直接依赖

### 单一职责
- Camera: 只负责图像采集
- Detection: 只负责污染检测
- Planning: 只负责路径规划和决策
- Communication: 只负责与STM32通信

### 统一接口
- 所有模块通过config.py获取配置
- 所有模块使用logger.py记录日志
- 所有模块之间传递统一的数据类

### 可扩展性
- 预留三维重建接口
- 支持多种AI模型切换
- 支持多种通信协议扩展

### 便于协作
- 模块独立，多人可并行开发
- 统一编码规范和文档标准
- 清晰的模块边界和数据流
