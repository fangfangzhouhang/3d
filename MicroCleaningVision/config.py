#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 配置模块
=========================================================

功能描述:
    集中管理系统所有配置参数。
    
设计原则:
    1. 所有参数统一管理，不在其他文件中出现魔法数字
    2. 支持默认值和自定义值
    3. 配置类层级清晰，便于维护和扩展
    4. 所有模块都引用此配置文件
    
TODO:
    - 实现从YAML/JSON文件加载配置
    - 添加配置验证
    - 支持配置热更新
    - 添加配置变更监听
"""


class CameraConfig:
    """
    相机配置类
    
    管理工业相机和双目相机的所有参数。
    
    Attributes:
        top_camera_id: 顶部工业相机编号
        angle_camera_id: 45度工业相机编号
        stereo_left_camera_id: 双目左相机编号
        stereo_right_camera_id: 双目右相机编号
        image_width: 图像宽度（像素）
        image_height: 图像高度（像素）
        fps: 帧率（帧/秒）
        exposure: 曝光时间（毫秒）
        gain: 增益（1.0-10.0）
        brightness: 亮度（0-255）
        contrast: 对比度（0-255）
        saturation: 饱和度（0-255）
        gamma: 伽马值（0.1-3.0）
        white_balance_red: 白平衡红色通道（0-255）
        white_balance_green: 白平衡绿色通道（0-255）
        white_balance_blue: 白平衡蓝色通道（0-255）
        auto_exposure: 是否自动曝光
        auto_gain: 是否自动增益
        auto_white_balance: 是否自动白平衡
        trigger_mode: 触发模式（software/hardware）
    """
    
    def __init__(self):
        # 工业相机编号
        self.top_camera_id = 0
        self.angle_camera_id = 1
        
        # 双目相机编号
        self.stereo_left_camera_id = 2
        self.stereo_right_camera_id = 3
        
        # 图像尺寸
        self.image_width = 1920
        self.image_height = 1080
        
        # 帧率
        self.fps = 30
        
        # 曝光参数
        self.exposure = 100
        self.gain = 1.0
        self.brightness = 128
        self.contrast = 128
        self.saturation = 128
        self.gamma = 1.0
        
        # 白平衡参数
        self.white_balance_red = 128
        self.white_balance_green = 128
        self.white_balance_blue = 128
        
        # 自动控制
        self.auto_exposure = False
        self.auto_gain = False
        self.auto_white_balance = False
        
        # 触发模式
        self.trigger_mode = "software"


class DetectionConfig:
    """
    检测配置类
    
    管理AI检测相关的所有参数。
    
    Attributes:
        confidence_threshold: 置信度阈值（0.0-1.0）
        iou_threshold: IoU阈值（0.0-1.0）
        nms_threshold: NMS阈值（0.0-1.0）
        max_detections: 最大检测数量
        model_type: 模型类型（yolo/custom）
        input_size: 模型输入尺寸
        num_classes: 类别数量
        class_names: 类别名称列表
        min_stain_area: 最小污渍面积（像素）
        max_stain_area: 最大污渍面积（像素）
        enable_preprocessing: 是否启用图像预处理
    """
    
    def __init__(self):
        # 阈值参数
        self.confidence_threshold = 0.5
        self.iou_threshold = 0.45
        self.nms_threshold = 0.45
        
        # 检测参数
        self.max_detections = 100
        self.model_type = "yolo"
        self.input_size = 640
        
        # 类别配置
        self.num_classes = 1
        self.class_names = ["contamination"]
        
        # 污渍面积过滤参数
        self.min_stain_area = 10
        self.max_stain_area = 10000
        
        # 预处理配置
        self.enable_preprocessing = True


class ModelConfig:
    """
    模型配置类
    
    管理AI模型相关的所有参数。
    
    Attributes:
        yolo_model_path: YOLO模型文件路径
        custom_model_path: 自定义模型文件路径
        custom_model_type: 自定义模型类型（pytorch/tensorflow/onnx）
        weights_dir: 模型权重目录
        use_gpu: 是否使用GPU
        gpu_device_id: GPU设备ID
        use_half_precision: 是否使用半精度推理
    """
    
    def __init__(self):
        # 模型路径
        self.yolo_model_path = "output/models/yolov8n.pt"
        self.custom_model_path = "output/models/custom.pt"
        
        # 模型类型
        self.custom_model_type = "pytorch"
        
        # 权重目录
        self.weights_dir = "models/"
        
        # 设备配置
        self.use_gpu = True
        self.gpu_device_id = 0
        self.use_half_precision = False


class CommunicationConfig:
    """
    通信配置类
    
    管理与STM32微控制器通信的所有参数。
    
    Attributes:
        port: 串口端口号
        baud_rate: 波特率
        data_bits: 数据位
        parity: 校验位（none/odd/even）
        stop_bits: 停止位
        timeout: 串口超时时间（秒）
        command_timeout: 指令超时时间（秒）
        retry_count: 指令重试次数
        reconnect_interval: 重连间隔时间（秒）
    """
    
    def __init__(self):
        # 串口配置
        self.port = "COM3"
        self.baud_rate = 115200
        self.data_bits = 8
        self.parity = "none"
        self.stop_bits = 1
        
        # 超时配置
        self.timeout = 1.0
        self.command_timeout = 5.0
        
        # 重试配置
        self.retry_count = 3
        self.reconnect_interval = 5.0


class PlanningConfig:
    """
    规划配置类
    
    管理路径规划和清洗控制的所有参数。
    
    Attributes:
        move_speed: 移动速度（毫米/秒）
        acceleration: 加速度（毫米/秒²）
        deceleration: 减速度（毫米/秒²）
        spray_duration: 默认喷射时间（毫秒）
        spray_pressure: 默认喷射压力（0.0-1.0）
        spray_radius: 喷射半径（毫米）
        min_spray_duration: 最小喷射时间（毫秒）
        max_spray_duration: 最大喷射时间（毫秒）
        path_optimization: 是否启用路径优化
        optimization_algorithm: 优化算法（nearest_neighbor/genetic/aco）
        safety_margin: 安全边距（毫米）
        approach_distance: 接近距离（毫米）
        retract_distance: 撤回距离（毫米）
        cleaning_threshold: 清洗效果阈值（0.0-1.0）
        max_cleaning_iterations: 最大清洗迭代次数
        enable_auto_cleaning: 是否启用自动清洗
    """
    
    def __init__(self):
        # 移动参数
        self.move_speed = 10.0
        self.acceleration = 5.0
        self.deceleration = 5.0
        
        # 喷射参数
        self.spray_duration = 1000
        self.spray_pressure = 0.5
        self.spray_radius = 2.0
        self.min_spray_duration = 100
        self.max_spray_duration = 5000
        
        # 路径优化
        self.path_optimization = True
        self.optimization_algorithm = "nearest_neighbor"
        
        # 安全参数
        self.safety_margin = 1.0
        self.approach_distance = 5.0
        self.retract_distance = 5.0
        
        # 清洗决策参数
        self.cleaning_threshold = 0.95
        self.max_cleaning_iterations = 5
        self.enable_auto_cleaning = True


class ReconstructionConfig:
    """
    三维重建配置类
    
    管理三维重建相关的所有参数（后期开发）。
    
    Attributes:
        method: 重建方法（stereo/depth/structured_light）
        depth_scale: 深度缩放因子
        min_depth: 最小深度（毫米）
        max_depth: 最大深度（毫米）
        point_cloud_density: 点云密度（0.0-1.0）
        use_texture: 是否使用纹理映射
        texture_resolution: 纹理分辨率
        mesh_simplification: 是否启用网格简化
        simplification_ratio: 简化比例（0.0-1.0）
    """
    
    def __init__(self):
        # 重建方法
        self.method = "stereo"
        
        # 深度参数
        self.depth_scale = 1.0
        self.min_depth = 0.1
        self.max_depth = 10.0
        
        # 点云参数
        self.point_cloud_density = 1.0
        
        # 纹理映射
        self.use_texture = True
        self.texture_resolution = 2048
        
        # 网格简化
        self.mesh_simplification = False
        self.simplification_ratio = 0.5


class DatasetConfig:
    """
    数据集配置类
    
    管理数据集相关的所有参数。
    
    Attributes:
        dataset_path: 数据集根目录路径
        train_path: 训练集路径
        val_path: 验证集路径
        test_path: 测试集路径
        annotations_path: 标注文件路径
        batch_size: 批量大小
        shuffle: 是否打乱数据
        num_workers: 数据加载工作线程数
        pin_memory: 是否锁定内存
        prefetch_factor: 预取因子
    """
    
    def __init__(self):
        # 数据集路径
        self.dataset_path = "output/datasets/"
        self.train_path = "output/datasets/train/"
        self.val_path = "output/datasets/val/"
        self.test_path = "output/datasets/test/"
        self.annotations_path = "output/datasets/annotations/"
        
        # 数据加载参数
        self.batch_size = 8
        self.shuffle = True
        self.num_workers = 4
        self.pin_memory = True
        self.prefetch_factor = 2


class SaveConfig:
    """
    保存配置类
    
    管理数据保存相关的所有参数。
    
    Attributes:
        save_path: 保存根目录路径
        capture_path: 采集图像保存路径
        detection_path: 检测结果保存路径
        reconstruction_path: 重建结果保存路径
        log_path: 日志文件保存路径
        save_format: 图像保存格式（jpg/png/tiff）
        save_annotations: 是否保存标注
        save_visualizations: 是否保存可视化结果
        save_depth_maps: 是否保存深度图
        save_point_clouds: 是否保存点云
        overwrite_existing: 是否覆盖已存在文件
        compression_quality: 压缩质量（0-100）
    """
    
    def __init__(self):
        # 保存路径
        self.save_path = "output/data/"
        self.capture_path = "output/data/captures/"
        self.detection_path = "output/data/detections/"
        self.reconstruction_path = "output/data/reconstructions/"
        self.log_path = "output/logs/"
        
        # 保存格式
        self.save_format = "jpg"
        
        # 保存选项
        self.save_annotations = True
        self.save_visualizations = True
        self.save_depth_maps = False
        self.save_point_clouds = False
        
        # 文件管理
        self.overwrite_existing = False
        self.compression_quality = 95


class LoggingConfig:
    """
    日志配置类
    
    管理日志记录相关的所有参数。
    
    Attributes:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
        file_path: 日志文件路径
        log_file_name: 日志文件名
        max_size: 单个日志文件最大大小（MB）
        backup_count: 日志文件备份数量
        format: 日志格式字符串
        date_format: 日期格式字符串
        use_color: 是否使用彩色输出
        console_output: 是否输出到控制台
        file_output: 是否输出到文件
        rotation: 日志轮转方式（size/time）
        rotation_interval: 轮转间隔（小时）
    """
    
    def __init__(self):
        # 日志级别
        self.level = "WARNING"
        
        # 日志路径
        self.file_path = "output/logs/"
        self.log_file_name = "app.log"
        
        # 文件管理
        self.max_size = 5
        self.backup_count = 3
        
        # 格式配置
        self.format = "{time} | {level} | {message}"
        self.date_format = "%Y-%m-%d %H:%M:%S"
        
        # 输出配置
        self.use_color = True
        self.console_output = False
        self.file_output = True
        
        # 轮转配置
        self.rotation = "size"
        self.rotation_interval = 24


class SystemConfig:
    """
    系统配置类
    
    管理系统级别的所有参数。
    
    Attributes:
        mode: 运行模式（auto/manual/test）
        max_cleaning_cycles: 最大清洗循环次数
        min_cleaning_effect: 最小清洗效果阈值（0.0-1.0）
        target_cleaning_effect: 目标清洗效果（0.0-1.0）
        idle_timeout: 空闲超时时间（秒）
        startup_delay: 启动延迟时间（秒）
        shutdown_timeout: 关闭超时时间（秒）
        enable_reconstruction: 是否启用三维重建
        enable_tracking: 是否启用目标追踪
        enable_visualization: 是否启用可视化
    """
    
    def __init__(self):
        # 运行模式
        self.mode = "auto"
        
        # 清洗参数
        self.max_cleaning_cycles = 5
        self.min_cleaning_effect = 0.9
        self.target_cleaning_effect = 0.95
        
        # 超时配置
        self.idle_timeout = 600
        self.startup_delay = 5
        self.shutdown_timeout = 30
        
        # 功能开关
        self.enable_reconstruction = False
        self.enable_tracking = True
        self.enable_visualization = True


class Config:
    """
    主配置类
    
    统一管理所有子配置类，是系统配置的唯一入口。
    
    使用方式:
        from config import Config
        config = Config()
        
        # 访问相机配置
        camera_id = config.camera.top_camera_id
        
        # 访问检测配置
        threshold = config.detection.confidence_threshold
        
        # 访问通信配置
        port = config.communication.port
    
    Attributes:
        camera: 相机配置
        detection: 检测配置
        model: 模型配置
        communication: 通信配置
        planning: 规划配置
        reconstruction: 三维重建配置
        dataset: 数据集配置
        save: 保存配置
        logging: 日志配置
        system: 系统配置
    """
    
    def __init__(self):
        self.camera = CameraConfig()
        self.detection = DetectionConfig()
        self.model = ModelConfig()
        self.communication = CommunicationConfig()
        self.planning = PlanningConfig()
        self.reconstruction = ReconstructionConfig()
        self.dataset = DatasetConfig()
        self.save = SaveConfig()
        self.logging = LoggingConfig()
        self.system = SystemConfig()
    
    def get_log_file_path(self):
        """
        获取完整的日志文件路径
        
        返回:
            str: 完整的日志文件路径
        """
        return self.logging.file_path + self.logging.log_file_name
    
    def get_yolo_model_full_path(self):
        """
        获取完整的YOLO模型路径
        
        返回:
            str: 完整的模型文件路径
        """
        return self.model.yolo_model_path
    
    def get_image_size(self):
        """
        获取图像尺寸
        
        返回:
            tuple: (width, height)
        """
        return (self.camera.image_width, self.camera.image_height)


if __name__ == "__main__":
    config = Config()
    
    print("=" * 50)
    print("MicroCleaningVision - 配置参数清单")
    print("=" * 50)
    
    # 相机配置
    print("\n【相机配置】")
    print(f"  顶部相机编号: {config.camera.top_camera_id}")
    print(f"  45度相机编号: {config.camera.angle_camera_id}")
    print(f"  双目左相机编号: {config.camera.stereo_left_camera_id}")
    print(f"  双目右相机编号: {config.camera.stereo_right_camera_id}")
    print(f"  图像尺寸: {config.camera.image_width} x {config.camera.image_height}")
    
    # 通信配置
    print("\n【通信配置】")
    print(f"  串口号: {config.communication.port}")
    print(f"  波特率: {config.communication.baud_rate}")
    
    # 模型配置
    print("\n【模型配置】")
    print(f"  YOLO模型路径: {config.model.yolo_model_path}")
    
    # 日志配置
    print("\n【日志配置】")
    print(f"  日志路径: {config.logging.file_path}")
    
    # 数据集配置
    print("\n【数据集配置】")
    print(f"  数据集路径: {config.dataset.dataset_path}")
    
    # 保存配置
    print("\n【保存配置】")
    print(f"  保存路径: {config.save.save_path}")
    
    # 检测配置
    print("\n【检测配置】")
    print(f"  置信度阈值: {config.detection.confidence_threshold}")
    print(f"  IoU阈值: {config.detection.iou_threshold}")
    
    # 规划配置
    print("\n【规划配置】")
    print(f"  默认喷射时间: {config.planning.spray_duration} ms")
    print(f"  移动速度: {config.planning.move_speed} mm/s")
    
    print("\n" + "=" * 50)
    print("配置参数清单打印完成")
    print("=" * 50)
