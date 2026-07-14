#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 检测模块测试
=========================================================

功能描述:
    测试detection模块的功能。
    
测试内容:
    - Detector初始化
    - 检测功能
    - 阈值设置
    
TODO:
    - 实现检测器初始化测试
    - 添加检测功能测试
    - 实现阈值设置测试
"""


import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDetection(unittest.TestCase):
    """
    检测模块测试类
    """
    
    def setUp(self):
        """
        测试前准备
        """
        from config import Config
        from utils.logger import Logger
        from detection.detector import Detector
        
        self.config = Config()
        self.logger = Logger(self.config)
        self.detector = Detector(self.config)
    
    def test_detector_initialization(self):
        """
        测试检测器初始化
        """
        self.assertIsNotNone(self.detector)
    
    def test_detection(self):
        """
        测试检测功能
        """
        import numpy as np
        test_top_image = np.zeros((480, 640, 3), dtype=np.uint8)
        test_angle_image = np.zeros((480, 640, 3), dtype=np.uint8)
        results = self.detector.detect(test_top_image, test_angle_image)
        self.assertIsNone(results)
    
    def test_set_threshold(self):
        """
        测试阈值设置
        """
        self.detector.set_confidence_threshold(0.5)
    
    def test_model_loading(self):
        """
        测试模型加载
        """
        result = self.detector.load_model("models/yolov8n.pt")
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
