#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 异常模块
=========================================================

功能描述:
    定义系统中使用的自定义异常类。
    
设计原则:
    1. 异常分类清晰，便于捕获和处理
    2. 提供详细的错误信息
    3. 支持错误代码和上下文信息
    
使用方式:
    from utils.exceptions import CameraError
    
    try:
        # 代码
    except CameraError as e:
        logger.error(str(e), module="Camera", function="capture")
    
TODO:
    - 添加更多异常类型
    - 实现异常处理装饰器
"""


class MicroCleaningVisionError(Exception):
    """
    基础异常类
    
    所有自定义异常的基类。
    
    Attributes:
        error_code: 错误代码
        message: 错误消息
        context: 错误上下文信息
    """
    
    def __init__(self, message="", error_code="MCV_ERROR", context=None):
        """
        初始化基础异常
        
        参数:
            message: 错误消息
            error_code: 错误代码
            context: 错误上下文信息
        """
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.context = context or {}
    
    def __str__(self):
        """
        返回错误描述字符串
        
        返回:
            str: 错误描述
        """
        result = f"[{self.error_code}] {self.message}"
        if self.context:
            result += f" | 上下文: {self.context}"
        return result


class CameraError(MicroCleaningVisionError):
    """
    相机模块异常
    
    用于相机连接、采集、控制等操作失败时抛出。
    
    Attributes:
        camera_id: 相机ID
        operation: 操作类型
    """
    
    def __init__(self, message="", camera_id=None, operation=None, context=None):
        """
        初始化相机异常
        
        参数:
            message: 错误消息
            camera_id: 相机ID
            operation: 操作类型（如 connect, capture, set_parameter）
            context: 错误上下文信息
        """
        error_code = "CAMERA_ERROR"
        super().__init__(message, error_code, context)
        self.camera_id = camera_id
        self.operation = operation
    
    def __str__(self):
        """
        返回错误描述字符串
        
        返回:
            str: 错误描述
        """
        result = f"[{self.error_code}] "
        if self.camera_id is not None:
            result += f"相机 {self.camera_id} "
        if self.operation is not None:
            result += f"{self.operation} 失败: "
        result += self.message
        if self.context:
            result += f" | 上下文: {self.context}"
        return result


class DetectionError(MicroCleaningVisionError):
    """
    检测模块异常
    
    用于AI检测、模型加载、推理等操作失败时抛出。
    
    Attributes:
        model_name: 模型名称
        operation: 操作类型
    """
    
    def __init__(self, message="", model_name=None, operation=None, context=None):
        """
        初始化检测异常
        
        参数:
            message: 错误消息
            model_name: 模型名称
            operation: 操作类型（如 load_model, detect, inference）
            context: 错误上下文信息
        """
        error_code = "DETECTION_ERROR"
        super().__init__(message, error_code, context)
        self.model_name = model_name
        self.operation = operation
    
    def __str__(self):
        """
        返回错误描述字符串
        
        返回:
            str: 错误描述
        """
        result = f"[{self.error_code}] "
        if self.model_name is not None:
            result += f"模型 {self.model_name} "
        if self.operation is not None:
            result += f"{self.operation} 失败: "
        result += self.message
        if self.context:
            result += f" | 上下文: {self.context}"
        return result


class ReconstructionError(MicroCleaningVisionError):
    """
    三维重建模块异常
    
    用于深度图生成、点云处理、三维重建等操作失败时抛出。
    
    Attributes:
        method: 重建方法
        operation: 操作类型
    """
    
    def __init__(self, message="", method=None, operation=None, context=None):
        """
        初始化三维重建异常
        
        参数:
            message: 错误消息
            method: 重建方法（如 stereo, depth, structured_light）
            operation: 操作类型（如 generate_depth, process_point_cloud, reconstruct）
            context: 错误上下文信息
        """
        error_code = "RECONSTRUCTION_ERROR"
        super().__init__(message, error_code, context)
        self.method = method
        self.operation = operation


class PlanningError(MicroCleaningVisionError):
    """
    规划模块异常
    
    用于路径规划、坐标计算、决策制定等操作失败时抛出。
    
    Attributes:
        operation: 操作类型
    """
    
    def __init__(self, message="", operation=None, context=None):
        """
        初始化规划异常
        
        参数:
            message: 错误消息
            operation: 操作类型（如 calculate_coordinates, plan_paths, decide）
            context: 错误上下文信息
        """
        error_code = "PLANNING_ERROR"
        super().__init__(message, error_code, context)
        self.operation = operation


class CommunicationError(MicroCleaningVisionError):
    """
    通信模块异常
    
    用于串口通信、指令发送、状态接收等操作失败时抛出。
    
    Attributes:
        port: 串口端口号
        operation: 操作类型
    """
    
    def __init__(self, message="", port=None, operation=None, context=None):
        """
        初始化通信异常
        
        参数:
            message: 错误消息
            port: 串口端口号
            operation: 操作类型（如 connect, send_command, receive_response）
            context: 错误上下文信息
        """
        error_code = "COMMUNICATION_ERROR"
        super().__init__(message, error_code, context)
        self.port = port
        self.operation = operation
    
    def __str__(self):
        """
        返回错误描述字符串
        
        返回:
            str: 错误描述
        """
        result = f"[{self.error_code}] "
        if self.port is not None:
            result += f"串口 {self.port} "
        if self.operation is not None:
            result += f"{self.operation} 失败: "
        result += self.message
        if self.context:
            result += f" | 上下文: {self.context}"
        return result


class CalibrationError(MicroCleaningVisionError):
    """
    标定模块异常
    
    用于相机标定、坐标转换等操作失败时抛出。
    
    Attributes:
        camera_id: 相机ID
        operation: 操作类型
    """
    
    def __init__(self, message="", camera_id=None, operation=None, context=None):
        """
        初始化标定异常
        
        参数:
            message: 错误消息
            camera_id: 相机ID
            operation: 操作类型（如 calibrate, transform, load_parameters）
            context: 错误上下文信息
        """
        error_code = "CALIBRATION_ERROR"
        super().__init__(message, error_code, context)
        self.camera_id = camera_id
        self.operation = operation


class ConfigError(MicroCleaningVisionError):
    """
    配置模块异常
    
    用于配置加载、解析、验证等操作失败时抛出。
    
    Attributes:
        config_key: 配置键名
        operation: 操作类型
    """
    
    def __init__(self, message="", config_key=None, operation=None, context=None):
        """
        初始化配置异常
        
        参数:
            message: 错误消息
            config_key: 配置键名
            operation: 操作类型（如 load, parse, validate）
            context: 错误上下文信息
        """
        error_code = "CONFIG_ERROR"
        super().__init__(message, error_code, context)
        self.config_key = config_key
        self.operation = operation


class LoggerError(MicroCleaningVisionError):
    """
    日志模块异常
    
    用于日志系统初始化、记录等操作失败时抛出。
    
    Attributes:
        operation: 操作类型
    """
    
    def __init__(self, message="", operation=None, context=None):
        """
        初始化日志异常
        
        参数:
            message: 错误消息
            operation: 操作类型（如 init, write, rotate）
            context: 错误上下文信息
        """
        error_code = "LOGGER_ERROR"
        super().__init__(message, error_code, context)
        self.operation = operation


class DatasetError(MicroCleaningVisionError):
    """
    数据集模块异常
    
    用于数据集加载、保存、处理等操作失败时抛出。
    
    Attributes:
        dataset_name: 数据集名称
        operation: 操作类型
    """
    
    def __init__(self, message="", dataset_name=None, operation=None, context=None):
        """
        初始化数据集异常
        
        参数:
            message: 错误消息
            dataset_name: 数据集名称
            operation: 操作类型（如 load, save, preprocess）
            context: 错误上下文信息
        """
        error_code = "DATASET_ERROR"
        super().__init__(message, error_code, context)
        self.dataset_name = dataset_name
        self.operation = operation


class ModelError(MicroCleaningVisionError):
    """
    模型模块异常
    
    用于模型加载、保存、推理等操作失败时抛出。
    
    Attributes:
        model_path: 模型路径
        operation: 操作类型
    """
    
    def __init__(self, message="", model_path=None, operation=None, context=None):
        """
        初始化模型异常
        
        参数:
            message: 错误消息
            model_path: 模型路径
            operation: 操作类型（如 load, save, inference）
            context: 错误上下文信息
        """
        error_code = "MODEL_ERROR"
        super().__init__(message, error_code, context)
        self.model_path = model_path
        self.operation = operation
