#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 数据类型定义模块
=========================================================

功能描述:
    定义系统中所有模块之间传递的数据结构。
    
设计原则:
    1. 所有模块只能传递这些数据类
    2. 不能直接传递tuple、dict等原生类型
    3. 使用Python dataclass确保数据结构清晰
    4. 便于后期维护和扩展
    
TODO:
    - 添加更多数据类型
    - 添加数据验证
    - 支持序列化和反序列化
"""


from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
import numpy as np


@dataclass
class CameraInfo:
    """
    相机信息类
    
    描述相机的基本信息和状态。
    
    Attributes:
        camera_id: 相机唯一标识
        camera_type: 相机类型（top/angle）
        manufacturer: 制造商
        model: 型号
        resolution: 分辨率 (width, height)
        fps: 帧率
        exposure: 曝光时间（毫秒）
        gain: 增益
        brightness: 亮度
        contrast: 对比度
        is_connected: 是否连接
        is_streaming: 是否正在流传输
        error_message: 错误信息
    """
    
    camera_id: int
    camera_type: str
    manufacturer: str = ""
    model: str = ""
    resolution: tuple = (0, 0)
    fps: int = 0
    exposure: int = 0
    gain: float = 1.0
    brightness: int = 128
    contrast: int = 128
    is_connected: bool = False
    is_streaming: bool = False
    error_message: str = ""


@dataclass
class Frame:
    """
    图像帧类
    
    表示从相机采集的一帧图像数据。
    
    Attributes:
        frame_id: 帧唯一标识
        camera_type: 相机类型（top/angle）
        image: 图像数据（numpy数组）
        timestamp: 采集时间戳
        width: 图像宽度
        height: 图像高度
        channels: 通道数
        format: 图像格式（RGB/BGR/GRAY）
        metadata: 元数据（可选）
        camera_info: 相机信息（可选）
    """
    
    frame_id: str
    camera_type: str
    image: np.ndarray
    timestamp: datetime
    width: int = 0
    height: int = 0
    channels: int = 3
    format: str = "BGR"
    metadata: Dict[str, Any] = field(default_factory=dict)
    camera_info: Optional[CameraInfo] = None


@dataclass
class Detection:
    """
    单个检测结果类
    
    表示一个检测到的目标对象。
    
    Attributes:
        detection_id: 检测唯一标识
        label: 目标类别标签
        confidence: 置信度（0-1）
        bbox: 边界框 (x, y, w, h)
        center: 中心点坐标 (x, y)
        area: 目标面积（像素）
        mask: 分割掩码（可选）
        extra: 额外信息（可选）
    """
    
    detection_id: str
    label: str
    confidence: float
    bbox: tuple
    center: tuple = (0, 0)
    area: float = 0.0
    mask: Optional[np.ndarray] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectionResult:
    """
    检测结果类
    
    表示一次检测的完整结果。
    
    Attributes:
        result_id: 结果唯一标识
        frame_id: 关联的帧ID
        timestamp: 检测时间戳
        detections: 检测列表
        image_shape: 输入图像形状
        inference_time: 推理时间（毫秒）
        model_name: 使用的模型名称
        model_version: 模型版本
        success: 是否成功
        error_message: 错误信息（可选）
    """
    
    result_id: str
    frame_id: str
    timestamp: datetime
    detections: List[Detection]
    image_shape: tuple = (0, 0, 3)
    inference_time: float = 0.0
    model_name: str = ""
    model_version: str = ""
    success: bool = True
    error_message: str = ""


@dataclass
class WorldPoint:
    """
    世界坐标点类
    
    表示物理世界中的三维坐标点。
    
    Attributes:
        x: X坐标（毫米）
        y: Y坐标（毫米）
        z: Z坐标（毫米，可选）
    """
    
    x: float
    y: float
    z: float = 0.0


@dataclass
class Target:
    """
    目标类
    
    表示一个需要清洗的目标位置。
    
    Attributes:
        target_id: 目标唯一标识
        world_point: 世界坐标点
        pixel_point: 像素坐标点（可选）
        label: 目标类别
        confidence: 置信度
        size: 目标尺寸（毫米）
        priority: 优先级（1-10，1最高）
        status: 状态（pending/processing/completed/failed）
        spray_duration: 建议喷射时间（毫秒）
        spray_pressure: 建议喷射压力
        extra: 额外信息（可选）
    """
    
    target_id: str
    world_point: WorldPoint
    pixel_point: Optional[tuple] = None
    label: str = ""
    confidence: float = 0.0
    size: float = 0.0
    priority: int = 5
    status: str = "pending"
    spray_duration: int = 1000
    spray_pressure: float = 0.5
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PathPoint:
    """
    路径点类
    
    表示路径规划中的一个点。
    
    Attributes:
        point_id: 点唯一标识
        world_point: 世界坐标点
        target_id: 关联的目标ID（可选）
        is_spray_point: 是否为喷射点
        spray_duration: 喷射时间（毫秒，可选）
        order: 顺序索引
    """
    
    point_id: str
    world_point: WorldPoint
    target_id: Optional[str] = None
    is_spray_point: bool = False
    spray_duration: int = 0
    order: int = 0


@dataclass
class CleaningPath:
    """
    清洗路径类
    
    表示一条完整的清洗路径。
    
    Attributes:
        path_id: 路径唯一标识
        points: 路径点列表
        total_length: 路径总长度（毫米）
        estimated_time: 预估时间（秒）
        priority: 优先级
        status: 状态（pending/processing/completed/failed）
    """
    
    path_id: str
    points: List[PathPoint]
    total_length: float = 0.0
    estimated_time: float = 0.0
    priority: int = 5
    status: str = "pending"


@dataclass
class CalibrationResult:
    """
    标定结果类
    
    表示相机标定的结果。
    
    Attributes:
        calibration_id: 标定唯一标识
        camera_type: 相机类型
        timestamp: 标定时间
        intrinsic_matrix: 内参矩阵
        distortion_coeffs: 畸变系数
        extrinsic_matrix: 外参矩阵（可选）
        reprojection_error: 重投影误差
        image_size: 图像尺寸
        success: 是否成功
        error_message: 错误信息（可选）
    """
    
    calibration_id: str
    camera_type: str
    timestamp: datetime
    intrinsic_matrix: np.ndarray
    distortion_coeffs: np.ndarray
    extrinsic_matrix: Optional[np.ndarray] = None
    reprojection_error: float = 0.0
    image_size: tuple = (0, 0)
    success: bool = True
    error_message: str = ""


@dataclass
class SystemStatus:
    """
    系统状态类
    
    表示系统各模块的状态。
    
    Attributes:
        module_name: 模块名称
        status: 状态（idle/running/error/stopped）
        error_message: 错误信息（可选）
        last_update_time: 最后更新时间
        uptime: 运行时间（秒）
    """
    
    module_name: str
    status: str
    error_message: str = ""
    last_update_time: datetime = field(default_factory=datetime.now)
    uptime: float = 0.0


@dataclass
class SystemState:
    """
    系统全局状态类
    
    表示整个系统的运行状态。
    
    Attributes:
        state_id: 状态唯一标识
        timestamp: 状态时间戳
        mode: 运行模式（auto/manual/test）
        current_cycle: 当前清洗循环次数
        total_cycles: 总清洗循环次数
        detected_targets: 已检测目标数量
        cleaned_targets: 已清洗目标数量
        failed_targets: 失败目标数量
        is_running: 是否运行中
        is_paused: 是否暂停
        module_status: 各模块状态
        error_message: 全局错误信息（可选）
    """
    
    state_id: str
    timestamp: datetime
    mode: str = "auto"
    current_cycle: int = 0
    total_cycles: int = 0
    detected_targets: int = 0
    cleaned_targets: int = 0
    failed_targets: int = 0
    is_running: bool = False
    is_paused: bool = False
    module_status: List[SystemStatus] = field(default_factory=list)
    error_message: str = ""


@dataclass
class CommandResult:
    """
    命令执行结果类
    
    表示发送给STM32的命令执行结果。
    
    Attributes:
        command_id: 命令唯一标识
        command_type: 命令类型（move/spray/stop/status）
        timestamp: 执行时间戳
        success: 是否成功
        error_message: 错误信息（可选）
        response_data: 响应数据（可选）
        execution_time: 执行时间（毫秒）
    """
    
    command_id: str
    command_type: str
    timestamp: datetime
    success: bool = True
    error_message: str = ""
    response_data: Dict[str, Any] = field(default_factory=dict)
    execution_time: float = 0.0


@dataclass
class CleaningResult:
    """
    清洗结果类
    
    表示一次清洗操作的结果。
    
    Attributes:
        result_id: 结果唯一标识
        target_id: 目标ID
        path_id: 路径ID
        timestamp: 清洗时间戳
        cleaned: 是否清洗成功
        before_detection: 清洗前检测结果（可选）
        after_detection: 清洗后检测结果（可选）
        cleaning_effect: 清洗效果（0-1）
        execution_time: 执行时间（毫秒）
        error_message: 错误信息（可选）
    """
    
    result_id: str
    target_id: str
    path_id: str
    timestamp: datetime
    cleaned: bool = False
    before_detection: Optional[DetectionResult] = None
    after_detection: Optional[DetectionResult] = None
    cleaning_effect: float = 0.0
    execution_time: float = 0.0
    error_message: str = ""
