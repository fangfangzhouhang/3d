#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 相机模块测试
=========================================================

功能描述:
    测试camera模块的功能。
    
测试内容:
    - CameraManager连接和断开
    - 图像采集功能
    - 相机信息获取
    
TODO:
    - 实现相机连接测试
    - 添加图像采集测试
    - 实现相机参数测试
"""


import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCamera(unittest.TestCase):
    """
    相机模块测试类
    """
    
    def setUp(self):
        """
        测试前准备
        """
        from config import Config
        from utils.logger import Logger
        from camera.camera import CameraManager
        
        self.config = Config()
        self.logger = Logger(self.config)
        self.camera_manager = CameraManager(self.config, self.logger)
    
    def test_camera_connection(self):
        """
        测试相机连接功能
        """
        result = self.camera_manager.connect()
        self.assertIsInstance(result, bool)
    
    def test_camera_disconnection(self):
        """
        测试相机断开功能
        """
        self.camera_manager.disconnect()
    
    def test_camera_capture(self):
        """
        测试图像采集功能
        """
        image = self.camera_manager.capture("top")
        self.assertIsNotNone(image)
    
    def test_camera_info(self):
        """
        测试相机信息获取
        """
        info = self.camera_manager.get_camera_info()
        self.assertIsInstance(info, dict)
    
    def tearDown(self):
        """
        测试后清理
        """
        self.camera_manager.disconnect()


if __name__ == "__main__":
    unittest.main()
