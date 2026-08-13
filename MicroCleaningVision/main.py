#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 主程序入口
=========================================================

功能描述:
    基于双显微镜（顶部+45°）的智能微污染检测与清洗系统主程序。
    
系统流程:
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
    
设计原则:
    1. 所有模块高度解耦，由main.py统一调度
    2. 所有参数统一放config.py
    3. 日志统一放utils/logger.py（基于loguru）
    4. 异常统一放utils/exceptions.py
    5. 方便多人协作开发
    6. 便于后续升级AI、YOLO、三维重建等功能
    
TODO:
    - 实现完整的清洗流程调度
    - 添加异常处理机制
    - 实现系统状态管理
    - 添加用户交互界面
    - 实现系统停止和退出功能
"""


class MainController:
    """
    主控制器类
    
    负责系统的整体调度和协调。
    
    Attributes:
        config: 配置对象
        logger: 日志对象（全局共享实例）
        experiment_manager: 实验管理器
        camera_manager: 相机管理器
        detector: 检测器
        planner: 规划器
        communicator: STM32通信器
        is_running: 是否正在运行
        current_experiment_id: 当前实验编号
    """
    
    def __init__(self):
        """
        初始化主控制器
        
        流程:
            1. 加载配置
            2. 初始化全局日志系统
            3. 开始新实验
            4. 标记运行状态
        """
        from config import Config
        from utils.logger import Logger, logger
        
        # 加载配置
        self.config = Config()
        
        # 初始化全局日志系统（只在main.py中调用一次）
        self.logger = Logger(self.config)
        self.logger.init()
        
        # 开始新实验
        self.current_experiment_id = self.logger.start_new_experiment()
        
        # 使用全局日志实例
        self.globallogger = logger
        
        # 模块实例（延迟初始化）
        self.camera_manager = None
        self.detector = None
        self.planner = None
        self.communicator = None
        
        # 运行状态
        self.is_running = False
        
        self.globallogger.info("主控制器初始化完成", module="Main", function="__init__")
    
    def initialize_modules(self):
        """
        初始化所有模块
        
        返回:
            bool: 初始化是否成功
        """
        self.globallogger.info("开始初始化各个模块...", module="Main", function="initialize_modules")
        
        try:
            # 初始化相机模块
            from camera.camera import CameraManager
            self.camera_manager = CameraManager(self.config, self.globallogger)
            self.camera_manager.connect()
            self.globallogger.info("相机模块初始化成功", module="Main", function="initialize_modules")
            
            # 初始化检测模块
            from detection.detector import Detector
            self.detector = Detector(self.config)
            self.detector.load_model(self.config.model.yolo_model_path)
            self.globallogger.info("检测模块初始化成功", module="Main", function="initialize_modules")
            
            # 初始化规划模块
            from planning.planner import Planner
            self.planner = Planner(self.config)
            self.globallogger.info("规划模块初始化成功", module="Main", function="initialize_modules")
            
            # 初始化通信模块
            from communication.stm32_comm import STM32Communicator
            self.communicator = STM32Communicator(self.config)
            self.communicator.connect()
            self.globallogger.info("通信模块初始化成功", module="Main", function="initialize_modules")
            
            self.globallogger.info("所有模块初始化完成", module="Main", function="initialize_modules")
            return True
            
        except Exception as e:
            self.globallogger.error(f"模块初始化失败: {str(e)}", module="Main", function="initialize_modules")
            return False
    
    def run_cleaning_cycle(self):
        """
        执行一次完整的清洗循环
        
        流程:
            1. 图像采集
            2. 污染检测
            3. 路径规划
            4. 发送指令
            5. 执行清洗
            6. 结果评估
        """
        self.globallogger.info("开始执行清洗循环...", module="Main", function="run_cleaning_cycle")
        
        try:
            # 步骤1: 图像采集
            self.globallogger.info("步骤1: 采集图像", module="Main", function="run_cleaning_cycle")
            top_image = self.camera_manager.capture("top")
            angle_image = self.camera_manager.capture("angle")
            
            # 步骤2: 污染检测
            self.globallogger.info("步骤2: AI污染检测", module="Main", function="run_cleaning_cycle")
            detections = self.detector.detect(top_image, angle_image)
            
            if not detections:
                self.globallogger.info("未检测到污染物，清洗循环结束", module="Main", function="run_cleaning_cycle")
                return
            
            # 步骤3: 坐标计算
            self.globallogger.info("步骤3: 坐标计算", module="Main", function="run_cleaning_cycle")
            world_coordinates = self.planner.calculate_coordinates(detections)
            
            # 步骤4: 路径规划
            self.globallogger.info("步骤4: 路径规划", module="Main", function="run_cleaning_cycle")
            cleaning_paths = self.planner.plan_paths(world_coordinates)
            
            # 步骤5: 发送指令执行清洗
            self.globallogger.info("步骤5: 执行清洗", module="Main", function="run_cleaning_cycle")
            for path in cleaning_paths:
                # 移动到目标位置
                self.communicator.send_move_command(path["x"], path["y"])
                
                # 喷射清洗
                self.communicator.send_spray_command(path["duration"])
            
            # 步骤6: 返回检测
            self.globallogger.info("步骤6: 返回检测", module="Main", function="run_cleaning_cycle")
            after_image = self.camera_manager.capture("top")
            after_detections = self.detector.detect(after_image)
            
            # 步骤7: 判断是否继续清洗
            self.globallogger.info("步骤7: 评估清洗效果", module="Main", function="run_cleaning_cycle")
            should_continue = self.planner.decide_continue_cleaning(after_detections)
            
            if should_continue:
                self.globallogger.info("清洗未完成，继续循环", module="Main", function="run_cleaning_cycle")
                self.run_cleaning_cycle()
            else:
                self.globallogger.info("清洗完成", module="Main", function="run_cleaning_cycle")
                
        except Exception as e:
            self.globallogger.error(f"清洗循环执行失败: {str(e)}", module="Main", function="run_cleaning_cycle")
    
    def start(self):
        """
        启动系统
        
        返回:
            bool: 启动是否成功
        """
        self.globallogger.info("启动MicroCleaningVision系统...", module="Main", function="start")
        
        if self.initialize_modules():
            self.is_running = True
            self.run_cleaning_cycle()
            return True
        
        return False
    
    def stop(self):
        """
        停止系统
        """
        self.globallogger.info("停止MicroCleaningVision系统...", module="Main", function="stop")
        
        self.is_running = False
        
        # 停止各个模块
        if self.communicator:
            self.communicator.disconnect()
            self.globallogger.info("通信模块已断开", module="Main", function="stop")
        
        if self.camera_manager:
            self.camera_manager.disconnect()
            self.globallogger.info("相机模块已断开", module="Main", function="stop")
        
        # 结束实验
        self.logger.end_experiment()
        
        self.globallogger.info("系统已停止", module="Main", function="stop")
    
    def shutdown(self):
        """
        关闭系统（清理资源）
        """
        self.stop()
        self.globallogger.info("MicroCleaningVision系统已关闭", module="Main", function="shutdown")


def main():
    """
    主函数
    
    程序入口，创建主控制器并启动系统。
    """
    controller = MainController()
    
    try:
        controller.start()
    except KeyboardInterrupt:
        controller.globallogger.info("用户中断，正在停止系统...", module="Main", function="main")
        controller.stop()
    except Exception as e:
        controller.globallogger.error(f"系统运行出错: {str(e)}", module="Main", function="main")
        controller.stop()


if __name__ == "__main__":
    main()
