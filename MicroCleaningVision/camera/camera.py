#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 相机管理模块
=========================================================

功能描述:
    负责管理顶部和45度两个工业相机的连接、采集和控制。
    
设计原则:
    1. 提供统一的相机接口
    2. 支持多相机管理
    3. 与标定模块紧密配合
    
实现状态:
    - ✅ 相机连接和断开
    - ✅ 图像采集功能
    - ✅ 相机参数设置
    - ✅ 相机状态监控
    - ⏳ 多相机同步采集（Phase 3）
"""


import cv2
import numpy as np
from datetime import datetime
from typing import Optional, Tuple
from utils.logger import logger
from utils.types import CameraInfo, Frame


class CameraManager:
    """
    相机管理器类
    
    负责管理顶部和45度两个工业相机。
    
    Attributes:
        config: 配置对象
        logger: 日志对象
        top_camera: 顶部相机实例 (cv2.VideoCapture)
        angle_camera: 45度相机实例 (cv2.VideoCapture)
        is_connected: 是否连接成功
        frame_count: 已采集帧数
    """
    
    def __init__(self, config, logger=None):
        """
        初始化相机管理器
        
        参数:
            config: 配置对象
            logger: 日志对象（可选，不传入时使用全局日志实例）
        """
        self.config = config
        if logger is None:
            from utils.logger import logger
        self.logger = logger
        
        # 相机实例（延迟初始化）
        self.top_camera: Optional[cv2.VideoCapture] = None
        self.angle_camera: Optional[cv2.VideoCapture] = None
        
        # 连接状态
        self.is_connected = False
        self.frame_count = 0
        
        self.logger.info("相机管理器初始化完成", module="Camera", function="__init__")
    
    def connect(self) -> bool:
        """
        连接所有相机
        
        返回:
            bool: 连接是否成功
        """
        try:
            self.top_camera = cv2.VideoCapture(self.config.camera.top_camera_id)
            
            if self.top_camera.isOpened():
                self._configure_camera(self.top_camera)
                self.logger.info(f"顶部相机连接成功 (ID: {self.config.camera.top_camera_id})", 
                               module="Camera", function="connect")
            else:
                self.logger.warning(f"顶部相机连接失败 (ID: {self.config.camera.top_camera_id})", 
                                   module="Camera", function="connect")
            
            self.angle_camera = cv2.VideoCapture(self.config.camera.angle_camera_id)
            if self.angle_camera.isOpened():
                self._configure_camera(self.angle_camera)
                self.logger.info(f"45度相机连接成功 (ID: {self.config.camera.angle_camera_id})", 
                               module="Camera", function="connect")
            else:
                self.logger.warning(f"45度相机连接失败 (ID: {self.config.camera.angle_camera_id})", 
                                   module="Camera", function="connect")
            
            self.is_connected = self.top_camera.isOpened() or self.angle_camera.isOpened()
            
            if self.is_connected:
                self.logger.info("相机连接完成", module="Camera", function="connect")
            else:
                self.logger.error("所有相机连接失败", module="Camera", function="connect")
            
            return self.is_connected
            
        except Exception as e:
            self.logger.error(f"相机连接异常: {str(e)}", module="Camera", function="connect")
            return False
    
    def _configure_camera(self, camera: cv2.VideoCapture):
        """
        配置相机参数（内部方法）
        
        参数:
            camera: 相机实例
        """
        try:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.camera.image_width)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camera.image_height)
            camera.set(cv2.CAP_PROP_FPS, self.config.camera.fps)
            
            if not self.config.camera.auto_exposure:
                camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
                camera.set(cv2.CAP_PROP_EXPOSURE, self.config.camera.exposure)
            
            camera.set(cv2.CAP_PROP_BRIGHTNESS, self.config.camera.brightness)
            camera.set(cv2.CAP_PROP_CONTRAST, self.config.camera.contrast)
            camera.set(cv2.CAP_PROP_SATURATION, self.config.camera.saturation)
            
            self.logger.debug("相机参数配置完成", module="Camera", function="_configure_camera")
            
        except Exception as e:
            self.logger.warning(f"相机参数配置部分失败: {str(e)}", 
                               module="Camera", function="_configure_camera")
    
    def disconnect(self):
        """
        断开所有相机连接
        """
        if self.top_camera is not None:
            self.top_camera.release()
            self.top_camera = None
            self.logger.info("顶部相机已断开", module="Camera", function="disconnect")
        
        if self.angle_camera is not None:
            self.angle_camera.release()
            self.angle_camera = None
            self.logger.info("45度相机已断开", module="Camera", function="disconnect")
        
        self.is_connected = False
        self.logger.info("所有相机连接已断开", module="Camera", function="disconnect")
    
    def capture(self, camera_type: str) -> Optional[Frame]:
        """
        采集图像
        
        参数:
            camera_type: 相机类型（top/angle）
            
        返回:
            Frame: 图像帧对象，如果采集失败返回None
        """
        camera = self._get_camera(camera_type)
        if camera is None or not camera.isOpened():
            self.logger.error(f"{camera_type}相机未连接", module="Camera", function="capture")
            return None
        
        try:
            ret, image = camera.read()
            
            if not ret:
                self.logger.warning(f"{camera_type}相机采集失败", module="Camera", function="capture")
                return None
            
            self.frame_count += 1
            
            frame = Frame(
                frame_id=f"frame_{self.frame_count:06d}_{camera_type}",
                camera_type=camera_type,
                image=image,
                timestamp=datetime.now(),
                width=image.shape[1],
                height=image.shape[0],
                channels=image.shape[2] if len(image.shape) == 3 else 1,
                format="BGR",
                camera_info=self.get_camera_info(camera_type)
            )
            
            self.logger.debug(f"{camera_type}相机采集成功 (帧ID: {frame.frame_id})", 
                           module="Camera", function="capture")
            
            return frame
            
        except Exception as e:
            self.logger.error(f"{camera_type}相机采集异常: {str(e)}", 
                           module="Camera", function="capture")
            return None
    
    def capture_both(self) -> Tuple[Optional[Frame], Optional[Frame]]:
        """
        同步采集两个相机的图像
        
        返回:
            tuple: (顶部相机图像帧, 45度相机图像帧)
        """
        top_frame = self.capture("top")
        angle_frame = self.capture("angle")
        
        return top_frame, angle_frame
    
    def _get_camera(self, camera_type: str) -> Optional[cv2.VideoCapture]:
        """
        获取相机实例（内部方法）
        
        参数:
            camera_type: 相机类型（top/angle）
            
        返回:
            cv2.VideoCapture: 相机实例
        """
        if camera_type == "top":
            return self.top_camera
        elif camera_type == "angle":
            return self.angle_camera
        else:
            self.logger.error(f"未知相机类型: {camera_type}", module="Camera", function="_get_camera")
            return None
    
    def start_stream(self, camera_type: str) -> bool:
        """
        启动视频流（实际上OpenCV的VideoCapture本身就是流式的）
        
        参数:
            camera_type: 相机类型（top/angle）
            
        返回:
            bool: 是否成功
        """
        camera = self._get_camera(camera_type)
        if camera is not None and camera.isOpened():
            self.logger.info(f"{camera_type}相机视频流已启动", module="Camera", function="start_stream")
            return True
        else:
            self.logger.error(f"{camera_type}相机未连接，无法启动视频流", 
                           module="Camera", function="start_stream")
            return False
    
    def stop_stream(self, camera_type: str):
        """
        停止视频流（实际上只需断开连接）
        
        参数:
            camera_type: 相机类型（top/angle）
        """
        self.logger.info(f"{camera_type}相机视频流已停止", module="Camera", function="stop_stream")
    
    def set_parameter(self, camera_type: str, parameter: str, value) -> bool:
        """
        设置相机参数
        
        参数:
            camera_type: 相机类型（top/angle）
            parameter: 参数名称（width/height/fps/brightness/contrast/saturation/exposure）
            value: 参数值
            
        返回:
            bool: 设置是否成功
        """
        camera = self._get_camera(camera_type)
        if camera is None or not camera.isOpened():
            self.logger.error(f"{camera_type}相机未连接", module="Camera", function="set_parameter")
            return False
        
        prop_map = {
            "width": cv2.CAP_PROP_FRAME_WIDTH,
            "height": cv2.CAP_PROP_FRAME_HEIGHT,
            "fps": cv2.CAP_PROP_FPS,
            "brightness": cv2.CAP_PROP_BRIGHTNESS,
            "contrast": cv2.CAP_PROP_CONTRAST,
            "saturation": cv2.CAP_PROP_SATURATION,
            "exposure": cv2.CAP_PROP_EXPOSURE,
            "gain": cv2.CAP_PROP_GAIN
        }
        
        if parameter not in prop_map:
            self.logger.error(f"未知参数: {parameter}", module="Camera", function="set_parameter")
            return False
        
        try:
            success = camera.set(prop_map[parameter], value)
            
            if success:
                self.logger.info(f"{camera_type}相机参数 {parameter} 设置为 {value}", 
                               module="Camera", function="set_parameter")
            else:
                self.logger.warning(f"{camera_type}相机参数 {parameter} 设置失败（可能不支持）", 
                                   module="Camera", function="set_parameter")
            
            return success
            
        except Exception as e:
            self.logger.error(f"{camera_type}相机参数设置异常: {str(e)}", 
                           module="Camera", function="set_parameter")
            return False
    
    def get_parameter(self, camera_type: str, parameter: str):
        """
        获取相机参数
        
        参数:
            camera_type: 相机类型（top/angle）
            parameter: 参数名称
            
        返回:
            any: 参数值，如果失败返回None
        """
        camera = self._get_camera(camera_type)
        if camera is None or not camera.isOpened():
            self.logger.error(f"{camera_type}相机未连接", module="Camera", function="get_parameter")
            return None
        
        prop_map = {
            "width": cv2.CAP_PROP_FRAME_WIDTH,
            "height": cv2.CAP_PROP_FRAME_HEIGHT,
            "fps": cv2.CAP_PROP_FPS,
            "brightness": cv2.CAP_PROP_BRIGHTNESS,
            "contrast": cv2.CAP_PROP_CONTRAST,
            "saturation": cv2.CAP_PROP_SATURATION,
            "exposure": cv2.CAP_PROP_EXPOSURE,
            "gain": cv2.CAP_PROP_GAIN
        }
        
        if parameter not in prop_map:
            self.logger.error(f"未知参数: {parameter}", module="Camera", function="get_parameter")
            return None
        
        try:
            value = camera.get(prop_map[parameter])
            self.logger.debug(f"{camera_type}相机参数 {parameter} = {value}", 
                           module="Camera", function="get_parameter")
            return value
            
        except Exception as e:
            self.logger.error(f"{camera_type}相机参数获取异常: {str(e)}", 
                           module="Camera", function="get_parameter")
            return None
    
    def get_camera_info(self, camera_type: str) -> CameraInfo:
        """
        获取相机信息
        
        参数:
            camera_type: 相机类型（top/angle）
            
        返回:
            CameraInfo: 相机信息对象
        """
        camera = self._get_camera(camera_type)
        is_opened = camera is not None and camera.isOpened()
        
        info = CameraInfo(
            camera_id=self.config.camera.top_camera_id if camera_type == "top" else self.config.camera.angle_camera_id,
            camera_type=camera_type,
            manufacturer="OpenCV VideoCapture",
            model="USB Camera",
            resolution=(
                int(self.get_parameter(camera_type, "width")) if is_opened else 0,
                int(self.get_parameter(camera_type, "height")) if is_opened else 0
            ),
            fps=int(self.get_parameter(camera_type, "fps")) if is_opened else 0,
            is_connected=is_opened,
            is_streaming=is_opened
        )
        
        return info
    
    def is_camera_available(self, camera_type: str) -> bool:
        """
        检查相机是否可用
        
        参数:
            camera_type: 相机类型（top/angle）
            
        返回:
            bool: 是否可用
        """
        camera = self._get_camera(camera_type)
        return camera is not None and camera.isOpened()
    
    def switch_camera(self, camera_type: str):
        """
        切换当前活动相机（预留接口）
        
        参数:
            camera_type: 相机类型（top/angle）
        """
        self.logger.info(f"切换到{camera_type}相机", module="Camera", function="switch_camera")
    
    def release(self):
        """
        释放所有资源（别名方法）
        """
        self.disconnect()


if __name__ == "__main__":
    """
    相机模块测试示例
    
    使用方法:
        python camera/camera.py
    """
    from config import Config
    
    config = Config()
    camera_manager = CameraManager(config)
    
    print("=" * 60)
    print("MicroCleaningVision - 相机模块测试")
    print("=" * 60)
    
    print("\n1. 连接相机...")
    success = camera_manager.connect()
    
    if success:
        print("\n2. 获取相机信息...")
        top_info = camera_manager.get_camera_info("top")
        print(f"   顶部相机: ID={top_info.camera_id}, 分辨率={top_info.resolution}, "
              f"帧率={top_info.fps}, 连接状态={top_info.is_connected}")
        
        angle_info = camera_manager.get_camera_info("angle")
        print(f"   45度相机: ID={angle_info.camera_id}, 分辨率={angle_info.resolution}, "
              f"帧率={angle_info.fps}, 连接状态={angle_info.is_connected}")
        
        print("\n3. 采集图像...")
        for i in range(5):
            frame = camera_manager.capture("top")
            if frame is not None:
                print(f"   成功采集帧 {i+1}: ID={frame.frame_id}, "
                      f"尺寸={frame.width}x{frame.height}, "
                      f"时间={frame.timestamp.strftime('%H:%M:%S')}")
            else:
                print(f"   采集帧 {i+1} 失败")
        
        print("\n4. 显示采集的图像（按'q'退出）...")
        frame = camera_manager.capture("top")
        if frame is not None:
            cv2.imshow("Camera Test", frame.image)
            print("   按 'q' 键退出窗口")
            while True:
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
            cv2.destroyAllWindows()
    
    print("\n5. 断开相机...")
    camera_manager.disconnect()
    
    print("\n" + "=" * 60)
    print("相机模块测试完成")
    print("=" * 60)