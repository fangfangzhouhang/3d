#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 图像预处理模块
=========================================================

功能描述:
    负责图像的去噪、增强、光照补偿等预处理操作。
    
设计原则:
    1. 支持多种预处理算法
    2. 可配置的预处理流程
    3. 保持图像质量和细节
    
实现状态:
    - ✅ 高斯模糊去噪
    - ✅ 中值滤波
    - ✅ 直方图均衡化
    - ✅ 自适应对比度增强
    - ✅ 光照补偿算法
    - ✅ 图像归一化
    - ✅ 图像缩放
"""


import cv2
import numpy as np
from typing import Tuple, Optional


class ImagePreprocessor:
    """
    图像预处理类
    
    负责对输入图像进行去噪、增强等预处理操作。
    
    Attributes:
        config: 配置对象
        logger: 日志对象
        enabled: 是否启用预处理
        pipeline: 预处理流程列表
    """
    
    def __init__(self, config, logger):
        """
        初始化图像预处理
        
        参数:
            config: 配置对象
            logger: 日志对象
        """
        self.config = config
        self.logger = logger
        self.enabled = config.detection.enable_preprocessing
        
        self.pipeline = [
            'denoise',
            'enhance_contrast',
            'normalize'
        ]
        
        self.logger.info("图像预处理模块初始化完成", module="Preprocessing", function="__init__")
    
    def preprocess(self, image: np.ndarray) -> np.ndarray:
        """
        执行完整预处理流程
        
        参数:
            image: 原始图像（numpy.ndarray）
            
        返回:
            numpy.ndarray: 预处理后的图像
        """
        if not self.enabled:
            return image
        
        processed = image.copy()
        
        for step in self.pipeline:
            try:
                method = getattr(self, step, None)
                if method is not None:
                    processed = method(processed)
            except Exception as e:
                self.logger.warning(f"预处理步骤 {step} 执行失败: {str(e)}", 
                                   module="Preprocessing", function="preprocess")
        
        self.logger.debug("图像预处理流程执行完成", module="Preprocessing", function="preprocess")
        
        return processed
    
    def denoise(self, image: np.ndarray) -> np.ndarray:
        """
        图像去噪（组合滤波）
        
        参数:
            image: 原始图像
            
        返回:
            numpy.ndarray: 去噪后的图像
        """
        return self.median_filter(image)
    
    def gaussian_blur(self, image: np.ndarray, kernel_size: Tuple[int, int] = (5, 5)) -> np.ndarray:
        """
        高斯模糊
        
        参数:
            image: 原始图像
            kernel_size: 卷积核大小，必须为奇数
            
        返回:
            numpy.ndarray: 模糊后的图像
        """
        if kernel_size[0] % 2 == 0 or kernel_size[1] % 2 == 0:
            self.logger.warning("高斯模糊核大小必须为奇数，已自动调整", 
                               module="Preprocessing", function="gaussian_blur")
            kernel_size = (kernel_size[0] + 1 if kernel_size[0] % 2 == 0 else kernel_size[0],
                          kernel_size[1] + 1 if kernel_size[1] % 2 == 0 else kernel_size[1])
        
        return cv2.GaussianBlur(image, kernel_size, 0)
    
    def median_filter(self, image: np.ndarray, kernel_size: int = 5) -> np.ndarray:
        """
        中值滤波（对椒盐噪声效果好）
        
        参数:
            image: 原始图像
            kernel_size: 滤波核大小，必须为奇数
            
        返回:
            numpy.ndarray: 滤波后的图像
        """
        if kernel_size % 2 == 0:
            kernel_size += 1
            self.logger.warning(f"中值滤波核大小必须为奇数，已调整为 {kernel_size}", 
                               module="Preprocessing", function="median_filter")
        
        return cv2.medianBlur(image, kernel_size)
    
    def bilateral_filter(self, image: np.ndarray, d: int = 9, 
                        sigma_color: float = 75, sigma_space: float = 75) -> np.ndarray:
        """
        双边滤波（保留边缘的同时去噪）
        
        参数:
            image: 原始图像
            d: 滤波核直径
            sigma_color: 颜色空间标准差
            sigma_space: 空间空间标准差
            
        返回:
            numpy.ndarray: 滤波后的图像
        """
        return cv2.bilateralFilter(image, d, sigma_color, sigma_space)
    
    def enhance_contrast(self, image: np.ndarray) -> np.ndarray:
        """
        对比度增强（自适应直方图均衡化）
        
        参数:
            image: 原始图像
            
        返回:
            numpy.ndarray: 增强后的图像
        """
        if len(image.shape) == 3:
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            h, s, v = cv2.split(hsv)
            v = self.adaptive_equalization(v)
            hsv = cv2.merge([h, s, v])
            return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        else:
            return self.adaptive_equalization(image)
    
    def histogram_equalization(self, image: np.ndarray) -> np.ndarray:
        """
        直方图均衡化
        
        参数:
            image: 原始图像（灰度图）
            
        返回:
            numpy.ndarray: 均衡化后的图像
        """
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            equalized = cv2.equalizeHist(gray)
            return cv2.cvtColor(equalized, cv2.COLOR_GRAY2BGR)
        else:
            return cv2.equalizeHist(image)
    
    def adaptive_equalization(self, image: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
        """
        自适应直方图均衡化（CLAHE）
        
        参数:
            image: 原始图像（灰度图）
            clip_limit: 对比度限制
            
        返回:
            numpy.ndarray: 均衡化后的图像
        """
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
        
        if len(image.shape) == 3:
            lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            l_equalized = clahe.apply(l)
            lab_equalized = cv2.merge([l_equalized, a, b])
            return cv2.cvtColor(lab_equalized, cv2.COLOR_LAB2BGR)
        else:
            return clahe.apply(image)
    
    def illumination_compensation(self, image: np.ndarray) -> np.ndarray:
        """
        光照补偿（解决不均匀光照问题）
        
        参数:
            image: 原始图像
            
        返回:
            numpy.ndarray: 补偿后的图像
        """
        if len(image.shape) != 3:
            return image
        
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        kernel_size = max(image.shape[0], image.shape[1]) // 10
        if kernel_size % 2 == 0:
            kernel_size += 1
        
        background = cv2.GaussianBlur(gray, (kernel_size, kernel_size), 0)
        
        diff = cv2.divide(gray, background, scale=255.0)
        
        return cv2.cvtColor(diff, cv2.COLOR_GRAY2BGR)
    
    def normalize(self, image: np.ndarray) -> np.ndarray:
        """
        图像归一化（将像素值归一化到0-1范围）
        
        参数:
            image: 原始图像
            
        返回:
            numpy.ndarray: 归一化后的图像
        """
        return image.astype(np.float32) / 255.0
    
    def resize(self, image: np.ndarray, target_size: Tuple[int, int]) -> np.ndarray:
        """
        图像缩放
        
        参数:
            image: 原始图像
            target_size: 目标尺寸 (width, height)
            
        返回:
            numpy.ndarray: 缩放后的图像
        """
        return cv2.resize(image, target_size, interpolation=cv2.INTER_LINEAR)
    
    def convert_to_gray(self, image: np.ndarray) -> np.ndarray:
        """
        转换为灰度图
        
        参数:
            image: 原始图像
            
        返回:
            numpy.ndarray: 灰度图像
        """
        if len(image.shape) == 3:
            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return image
    
    def sharpen(self, image: np.ndarray) -> np.ndarray:
        """
        图像锐化
        
        参数:
            image: 原始图像
            
        返回:
            numpy.ndarray: 锐化后的图像
        """
        kernel = np.array([[0, -1, 0],
                           [-1, 5, -1],
                           [0, -1, 0]], dtype=np.float32)
        return cv2.filter2D(image, -1, kernel)
    
    def apply_mask(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """
        应用掩码
        
        参数:
            image: 原始图像
            mask: 掩码图像
            
        返回:
            numpy.ndarray: 应用掩码后的图像
        """
        return cv2.bitwise_and(image, image, mask=mask)
    
    def process(self, image: np.ndarray) -> np.ndarray:
        """
        执行完整预处理流程（与preprocess方法相同，提供统一接口）
        
        参数:
            image: 原始图像
            
        返回:
            numpy.ndarray: 预处理后的图像
        """
        return self.preprocess(image)
    
    def set_pipeline(self, steps: list):
        """
        设置预处理流程
        
        参数:
            steps: 步骤列表，如 ['denoise', 'enhance_contrast', 'normalize']
        """
        valid_steps = ['denoise', 'gaussian_blur', 'median_filter', 'bilateral_filter',
                       'enhance_contrast', 'histogram_equalization', 'adaptive_equalization',
                       'illumination_compensation', 'normalize', 'resize', 'convert_to_gray',
                       'sharpen', 'apply_mask']
        
        self.pipeline = [step for step in steps if step in valid_steps]
        self.logger.info(f"预处理流程已更新: {self.pipeline}", 
                       module="Preprocessing", function="set_pipeline")


if __name__ == "__main__":
    """
    图像预处理模块测试示例
    
    使用方法:
        python detection/preprocessing.py
    """
    import sys
    import os
    
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from config import Config
    
    config = Config()
    config.detection.enable_preprocessing = True
    
    logger = __import__('logging').getLogger(__name__)
    logger.setLevel(__import__('logging').DEBUG)
    
    preprocessor = ImagePreprocessor(config, logger)
    
    print("=" * 60)
    print("MicroCleaningVision - 图像预处理模块测试")
    print("=" * 60)
    
    test_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    
    print("\n1. 测试高斯模糊...")
    blurred = preprocessor.gaussian_blur(test_image, (5, 5))
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {blurred.shape}")
    
    print("\n2. 测试中值滤波...")
    filtered = preprocessor.median_filter(test_image, 5)
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {filtered.shape}")
    
    print("\n3. 测试直方图均衡化...")
    equalized = preprocessor.histogram_equalization(test_image)
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {equalized.shape}")
    
    print("\n4. 测试自适应均衡化...")
    adaptive_eq = preprocessor.adaptive_equalization(test_image, 2.0)
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {adaptive_eq.shape}")
    
    print("\n5. 测试对比度增强...")
    enhanced = preprocessor.enhance_contrast(test_image)
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {enhanced.shape}")
    
    print("\n6. 测试光照补偿...")
    compensated = preprocessor.illumination_compensation(test_image)
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {compensated.shape}")
    
    print("\n7. 测试归一化...")
    normalized = preprocessor.normalize(test_image)
    print(f"   输入类型: {test_image.dtype}, 输出类型: {normalized.dtype}")
    print(f"   输入范围: [{test_image.min()}, {test_image.max()}]")
    print(f"   输出范围: [{normalized.min():.4f}, {normalized.max():.4f}]")
    
    print("\n8. 测试图像缩放...")
    resized = preprocessor.resize(test_image, (320, 240))
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {resized.shape}")
    
    print("\n9. 测试完整预处理流程...")
    processed = preprocessor.preprocess(test_image)
    print(f"   输入尺寸: {test_image.shape}, 输出尺寸: {processed.shape}")
    print(f"   输入类型: {test_image.dtype}, 输出类型: {processed.dtype}")
    
    print("\n" + "=" * 60)
    print("图像预处理模块测试完成")
    print("=" * 60)