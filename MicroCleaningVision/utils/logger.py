#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 日志模块
=========================================================

功能描述:
    基于loguru实现的统一日志系统，支持多人协作开发。
    
设计原则:
    1. 统一接口：所有模块共享同一个logger实例
    2. 分级存储：按日期+级别分类存储日志文件
    3. 低冗余：只记录关键节点，避免循环内大量重复
    4. 便于调试：包含模块、函数、实验编号等信息
    5. 多人协作：保证三人开发时日志格式完全一致
    
日志等级说明:
    DEBUG:   开发调试信息，仅在开发阶段使用
    INFO:    系统正常关键流程记录
    WARNING: 异常但不影响运行的问题
    ERROR:   模块运行错误，需要关注
    CRITICAL:导致系统无法工作的严重错误
    
日志文件结构:
    logs/
    ├── 2026-07-10/
    │   ├── info.log
    │   ├── warning.log
    │   └── error.log
    ├── 2026-07-11/
    │   ├── info.log
    │   ├── warning.log
    │   └── error.log
    └── ...

日志格式:
    [时间] [级别] [模块] [函数] [实验编号] [信息]
    示例: 2026-07-10 10:20:30.123 | INFO | Camera | capture() | EXP_001 | Image captured

使用方式:
    # 在main.py中初始化
    from utils.logger import Logger, logger, ExperimentManager
    
    # 初始化实验管理器
    exp_manager = ExperimentManager()
    exp_manager.start_new_experiment()
    
    # 初始化日志系统
    logger = Logger(config)
    logger.init()
    
    # 在其他模块中使用
    from utils.logger import logger
    
    logger.info("消息内容", module="Camera", function="capture")
    logger.error("错误消息", module="Detection", function="detect")
    
TODO:
    - 添加日志查询功能
    - 实现日志统计分析
    - 添加日志导出功能
    - 支持日志级别动态调整
"""


import os
import sys
from datetime import datetime
from loguru import logger as loguru_logger


class ExperimentManager:
    """
    实验编号管理器
    
    负责生成和管理实验编号，方便后续实验数据分析和论文记录。
    
    Attributes:
        current_experiment_id: 当前实验编号
        experiment_counter: 实验计数器
        experiment_history: 实验历史记录
    """
    
    def __init__(self, start_counter=1):
        """
        初始化实验管理器
        
        参数:
            start_counter: 起始计数器（默认从1开始）
        """
        self.current_experiment_id = None
        self.experiment_counter = start_counter
        self.experiment_history = []
        
        # 从日志目录读取最大编号
        self._load_last_experiment_id()
    
    def _load_last_experiment_id(self):
        """
        从日志目录读取最后一个实验编号
        """
        log_dir = "output/logs/"
        if os.path.exists(log_dir):
            max_counter = 0
            for date_folder in os.listdir(log_dir):
                if os.path.isdir(os.path.join(log_dir, date_folder)):
                    info_file = os.path.join(log_dir, date_folder, "info.log")
                    if os.path.exists(info_file):
                        with open(info_file, 'r', encoding='utf-8') as f:
                            for line in f:
                                if "EXP_" in line:
                                    try:
                                        exp_id = line.split("EXP_")[1].split()[0]
                                        counter = int(exp_id)
                                        if counter > max_counter:
                                            max_counter = counter
                                    except:
                                        pass
            if max_counter > 0:
                self.experiment_counter = max_counter + 1
    
    def start_new_experiment(self):
        """
        开始新的实验，生成新的实验编号
        
        返回:
            str: 实验编号（如 EXP_001）
        """
        self.current_experiment_id = f"EXP_{self.experiment_counter:03d}"
        self.experiment_counter += 1
        
        # 记录实验开始
        self.experiment_history.append({
            'experiment_id': self.current_experiment_id,
            'start_time': datetime.now(),
            'end_time': None
        })
        
        return self.current_experiment_id
    
    def end_current_experiment(self):
        """
        结束当前实验
        """
        if self.current_experiment_id:
            for exp in self.experiment_history:
                if exp['experiment_id'] == self.current_experiment_id and exp['end_time'] is None:
                    exp['end_time'] = datetime.now()
                    break
    
    def get_current_experiment_id(self):
        """
        获取当前实验编号
        
        返回:
            str: 当前实验编号，如果没有则返回None
        """
        return self.current_experiment_id
    
    def get_experiment_history(self):
        """
        获取实验历史记录
        
        返回:
            list: 实验历史列表
        """
        return self.experiment_history
    
    def reset_counter(self, new_counter=1):
        """
        重置实验计数器
        
        参数:
            new_counter: 新的起始计数器
        """
        self.experiment_counter = new_counter


class Logger:
    """
    日志类
    
    基于loguru实现统一的日志系统，支持按日期分级存储。
    
    Attributes:
        config: 配置对象
        logger: loguru logger实例
        log_level: 日志级别
        log_dir: 日志根目录
        is_initialized: 是否已初始化
        experiment_manager: 实验管理器
    """
    
    def __init__(self, config=None):
        """
        初始化日志器
        
        参数:
            config: 配置对象（可选）
        """
        self.config = config
        self.logger = loguru_logger
        self.is_initialized = False
        self.experiment_manager = ExperimentManager()
        
        # 从配置获取参数，如果没有配置则使用默认值
        if config and hasattr(config, 'logging'):
            self.log_level = config.logging.level
            self.log_dir = config.logging.file_path
            self.max_size = config.logging.max_size
            self.backup_count = config.logging.backup_count
            self.use_color = config.logging.use_color
            self.console_output = config.logging.console_output
            self.file_output = config.logging.file_output
        else:
            # 默认配置
            self.log_level = "INFO"
            self.log_dir = "output/logs/"
            self.max_size = 10
            self.backup_count = 7
            self.use_color = True
            self.console_output = True
            self.file_output = True
    
    def _clean_old_logs(self):
        """
        清理过期日志文件
        
        根据backup_count配置，删除超过指定天数的日志目录
        """
        if not os.path.exists(self.log_dir):
            return
        
        today = datetime.now()
        for date_folder in os.listdir(self.log_dir):
            if os.path.isdir(os.path.join(self.log_dir, date_folder)):
                try:
                    folder_date = datetime.strptime(date_folder, "%Y-%m-%d")
                    days_diff = (today - folder_date).days
                    if days_diff > self.backup_count:
                        folder_path = os.path.join(self.log_dir, date_folder)
                        import shutil
                        shutil.rmtree(folder_path)
                except ValueError:
                    pass
    
    def init(self):
        """
        初始化日志系统
        
        设置日志格式、级别、输出方式等。
        
        返回:
            bool: 初始化是否成功
        """
        try:
            # 移除默认的控制台输出（后续按需重新添加）
            self.logger.remove()
            
            # 确保日志根目录存在
            os.makedirs(self.log_dir, exist_ok=True)
            
            # 清理过期日志
            self._clean_old_logs()
            
            # 获取当前日期目录
            today = datetime.now().strftime("%Y-%m-%d")
            today_dir = os.path.join(self.log_dir, today)
            os.makedirs(today_dir, exist_ok=True)
            
            # 日志格式
            log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[module]: <12} | {extra[function]: <20} | {extra[experiment]: <10} | {message}"
            
            # 添加控制台输出（显示所有级别）
            if self.console_output:
                console_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module]: <12} | {message}"
                self.logger.add(
                    sink=lambda msg: print(msg, end=''),
                    format=console_format,
                    level="DEBUG",
                    colorize=self.use_color,
                    filter=lambda record: record["level"].no >= self._get_level_number(self.log_level)
                )
            
            # 根据配置的日志级别，只记录WARNING及以上级别
            log_file = os.path.join(today_dir, "app.log")
            self.logger.add(
                sink=log_file,
                format=log_format,
                level=self.log_level,
                rotation=self.max_size * 1024 * 1024,
                retention=self.backup_count,
                compression="zip"
            )
            
            self.is_initialized = True
            
            # 记录日志系统初始化
            self.info(
                "日志系统初始化完成",
                module="Logger",
                function="init"
            )
            self.info(
                f"日志级别: {self.log_level}",
                module="Logger",
                function="init"
            )
            self.info(
                f"日志目录: {os.path.abspath(self.log_dir)}",
                module="Logger",
                function="init"
            )
            
            return True
            
        except Exception as e:
            print(f"日志系统初始化失败: {e}")
            return False
    
    def _get_level_number(self, level):
        """
        获取日志级别的数字值
        
        参数:
            level: 日志级别字符串
            
        返回:
            int: 日志级别数字值
        """
        level_map = {
            "DEBUG": 10,
            "INFO": 20,
            "WARNING": 30,
            "ERROR": 40,
            "CRITICAL": 50
        }
        return level_map.get(level.upper(), 20)
    
    def _get_today_dir(self):
        """
        获取今天的日志目录，如果不存在则创建
        
        返回:
            str: 今天的日志目录路径
        """
        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = os.path.join(self.log_dir, today)
        os.makedirs(today_dir, exist_ok=True)
        return today_dir
    
    def debug(self, message, module="Unknown", function="Unknown"):
        """
        记录DEBUG级别日志
        
        使用场景:
            仅在开发调试阶段使用，记录详细的调试信息
            
        参数:
            message: 日志消息
            module: 模块名称（默认Unknown）
            function: 函数名称（默认Unknown）
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        self.logger.bind(module=module, function=function, experiment=exp_id).debug(message)
    
    def info(self, message, module="Unknown", function="Unknown"):
        """
        记录INFO级别日志
        
        使用场景:
            记录系统正常运行的关键流程节点
            
        需要记录的关键节点:
            - 系统启动/关闭
            - Camera: 连接成功/失败、图片采集成功/保存失败
            - AI模块: 模型加载、检测完成、检测结果、置信度、识别失败
            - Calibration: 标定成功、坐标转换结果
            - Communication: 串口连接、发送命令、接收反馈
            - Planning: 清洗开始/结束、复检结果
            
        参数:
            message: 日志消息
            module: 模块名称（默认Unknown）
            function: 函数名称（默认Unknown）
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        self.logger.bind(module=module, function=function, experiment=exp_id).info(message)
    
    def warning(self, message, module="Unknown", function="Unknown"):
        """
        记录WARNING级别日志
        
        使用场景:
            记录异常但不影响系统继续运行的问题
            
        参数:
            message: 日志消息
            module: 模块名称（默认Unknown）
            function: 函数名称（默认Unknown）
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        self.logger.bind(module=module, function=function, experiment=exp_id).warning(message)
    
    def error(self, message, module="Unknown", function="Unknown"):
        """
        记录ERROR级别日志
        
        使用场景:
            记录模块运行错误，需要关注但不影响系统整体运行
            
        参数:
            message: 日志消息
            module: 模块名称（默认Unknown）
            function: 函数名称（默认Unknown）
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        self.logger.bind(module=module, function=function, experiment=exp_id).error(message)
    
    def critical(self, message, module="Unknown", function="Unknown"):
        """
        记录CRITICAL级别日志
        
        使用场景:
            记录导致系统无法继续工作的严重错误
            
        参数:
            message: 日志消息
            module: 模块名称（默认Unknown）
            function: 函数名称（默认Unknown）
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        self.logger.bind(module=module, function=function, experiment=exp_id).critical(message)
    
    def exception(self, message, module="Unknown", function="Unknown", *args, **kwargs):
        """
        记录异常日志（自动包含堆栈信息）
        
        使用场景:
            捕获到异常时记录详细的错误信息和堆栈
            
        参数:
            message: 日志消息
            module: 模块名称（默认Unknown）
            function: 函数名称（默认Unknown）
            *args: 额外参数
            **kwargs: 额外关键字参数
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        self.logger.bind(module=module, function=function, experiment=exp_id).exception(message, *args, **kwargs)
    
    def log_detection_result(self, count, confidence, module="Detection", function="detect"):
        """
        记录检测结果（专用方法）
        
        参数:
            count: 检测到的目标数量
            confidence: 置信度
            module: 模块名称
            function: 函数名称
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        message = f"检测完成 - 目标数量: {count}, 平均置信度: {confidence:.4f}"
        self.logger.bind(module=module, function=function, experiment=exp_id).info(message)
    
    def log_cleaning_result(self, targets_cleaned, effectiveness, module="Planning", function="clean"):
        """
        记录清洗结果（专用方法）
        
        参数:
            targets_cleaned: 已清洗目标数量
            effectiveness: 清洗效果
            module: 模块名称
            function: 函数名称
        """
        exp_id = self.experiment_manager.get_current_experiment_id() or "N/A"
        message = f"清洗完成 - 已清洗: {targets_cleaned}, 效果: {effectiveness:.2f}"
        self.logger.bind(module=module, function=function, experiment=exp_id).info(message)
    
    def set_level(self, level):
        """
        设置日志级别
        
        参数:
            level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        """
        self.log_level = level
        self.logger.remove()
        self.init()
    
    def get_level(self):
        """
        获取当前日志级别
        
        返回:
            str: 日志级别
        """
        return self.log_level
    
    def get_log_dir(self):
        """
        获取日志目录
        
        返回:
            str: 日志目录路径
        """
        return self.log_dir
    
    def start_new_experiment(self):
        """
        开始新的实验
        
        返回:
            str: 实验编号
        """
        exp_id = self.experiment_manager.start_new_experiment()
        self.info(f"实验开始: {exp_id}", module="Experiment", function="start")
        return exp_id
    
    def end_experiment(self):
        """
        结束当前实验
        """
        exp_id = self.experiment_manager.get_current_experiment_id()
        if exp_id:
            self.info(f"实验结束: {exp_id}", module="Experiment", function="end")
            self.experiment_manager.end_current_experiment()
    
    def get_current_experiment_id(self):
        """
        获取当前实验编号
        
        返回:
            str: 当前实验编号
        """
        return self.experiment_manager.get_current_experiment_id()
    
    def flush(self):
        """
        刷新日志缓冲区
        """
        self.logger.complete()


# 创建全局日志实例，供所有模块共享使用
# 注意：需要在main.py中调用init()方法初始化
logger = Logger()

# 创建全局实验管理器实例
experiment_manager = ExperimentManager()
