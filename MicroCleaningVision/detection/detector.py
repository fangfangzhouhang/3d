#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 检测模块
=========================================================

功能描述:
    负责图像预处理、AI模型推理和检测结果后处理。
    
设计原则:
    1. 支持OpenCV传统检测和YOLO AI检测两种模式
    2. 提供统一的检测接口
    3. 与预处理和后处理模块紧密配合
    
实现状态:
    - ✅ OpenCV颜色检测（HSV颜色空间）
    - ✅ YOLO模型检测（预留，调用YOLOModel）
    - ✅ 单图像检测
    - ✅ 双图像检测（顶部+45度）
    - ✅ 批量检测
"""


import cv2
import numpy as np
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from models.yolo_model import YOLOModel
from .preprocessing import ImagePreprocessor as Preprocessor
from .postprocessing import DetectionPostprocessor as Postprocessor
from utils.logger import logger
from utils.types import Detection, DetectionResult


class Detector:
    """
    检测器类
    
    协调整个检测流程，支持OpenCV颜色检测和YOLO AI检测。
    
    Attributes:
        config: 配置对象
        model: YOLO模型实例（可选）
        preprocessor: 图像预处理实例
        postprocessor: 检测后处理实例
        is_model_loaded: 是否加载了YOLO模型
        mode: 检测模式（opencv/yolo）
    """
    
    def __init__(self, config, mode: str = "opencv"):
        """
        初始化检测器
        
        参数:
            config: 配置对象
            mode: 检测模式，可选 "opencv" 或 "yolo"
        """
        self.config = config
        self.model = None
        self.preprocessor = Preprocessor(config, logger)
        self.postprocessor = Postprocessor(config, logger)
        self.is_model_loaded = False
        self.mode = mode
        
        self._hsv_ranges = {
            'red': [
                ([0, 50, 50], [10, 255, 255]),
                ([170, 50, 50], [180, 255, 255])
            ],
            'orange': [
                ([10, 50, 50], [25, 255, 255])
            ],
            'yellow': [
                ([25, 50, 50], [35, 255, 255])
            ],
            'green': [
                ([35, 50, 50], [77, 255, 255])
            ],
            'blue': [
                ([100, 50, 50], [130, 255, 255])
            ],
            'purple': [
                ([130, 50, 50], [160, 255, 255])
            ],
            'white': [
                ([0, 0, 200], [180, 30, 255])
            ],
            'black': [
                ([0, 0, 0], [180, 255, 50])
            ]
        }
        
        logger.info(f"检测器初始化完成，模式: {mode}", module="Detection", function="__init__")

    def load_model(self, model_path=None):
        """
        加载YOLO模型
        
        参数:
            model_path: 模型文件路径，默认为配置中的路径
            
        返回:
            bool: 是否加载成功
        """
        if model_path is None:
            model_path = self.config.model.yolo_model_path
        
        try:
            self.model = YOLOModel(self.config, logger)
            success = self.model.load()
            
            if success:
                self.is_model_loaded = True
                self.model.warmup()
                logger.info(f"模型加载成功: {model_path}", module="Detection", function="load_model")
            else:
                logger.error(f"模型加载失败: {model_path}", module="Detection", function="load_model")
            
            return success
        except Exception as e:
            logger.error(f"模型加载异常: {str(e)}", module="Detection", function="load_model")
            return False

    def unload_model(self):
        """
        卸载YOLO模型
        """
        if self.model is not None:
            self.model.unload()
            self.model = None
            self.is_model_loaded = False
            logger.info("模型已卸载", module="Detection", function="unload_model")

    def detect(self, top_image, angle_image):
        """
        检测双图像（顶部+45度）
        
        参数:
            top_image: 顶部相机图像
            angle_image: 45度相机图像
            
        返回:
            dict: 包含顶部检测结果、角度检测结果和合并结果
        """
        top_result = self.detect_single(top_image, frame_id="top")
        angle_result = self.detect_single(angle_image, frame_id="angle")
        
        if top_result is not None and angle_result is not None:
            combined = self.postprocessor.merge_results(
                top_result.detections, 
                angle_result.detections
            )
        elif top_result is not None:
            combined = top_result.detections
        elif angle_result is not None:
            combined = angle_result.detections
        else:
            combined = []
        
        return {
            'top_detections': top_result,
            'angle_detections': angle_result,
            'combined': combined
        }

    def detect_single(self, image, frame_id: str = "frame"):
        """
        单图像检测
        
        参数:
            image: 输入图像（numpy数组）
            frame_id: 帧ID，用于标识检测结果
            
        返回:
            DetectionResult: 检测结果对象，如果检测失败返回None
        """
        if image is None:
            logger.error("输入图像为空", module="Detection", function="detect_single")
            return None
        
        try:
            start_time = datetime.now()
            
            if self.mode == "yolo":
                if not self.is_model_loaded:
                    logger.warning("YOLO模式但模型未加载，切换到OpenCV模式", 
                                   module="Detection", function="detect_single")
                    detections = self._detect_opencv(image)
                else:
                    preprocessed = self.preprocessor.process(image)
                    yolo_results = self.model.predict(preprocessed)
                    detections = self._convert_yolo_results(yolo_results)
            else:
                detections = self._detect_opencv(image)
            
            detections = self.postprocessor.postprocess(detections)
            
            inference_time = (datetime.now() - start_time).total_seconds() * 1000
            
            result = DetectionResult(
                result_id=f"result_{frame_id}_{int(datetime.now().timestamp())}",
                frame_id=frame_id,
                timestamp=datetime.now(),
                detections=detections,
                image_shape=image.shape,
                inference_time=inference_time,
                model_name=self.mode,
                success=True
            )
            
            logger.info(f"单图像检测完成: 检测到 {len(detections)} 个目标, 耗时 {inference_time:.2f}ms", 
                       module="Detection", function="detect_single")
            
            return result
            
        except Exception as e:
            logger.error(f"单图像检测失败: {str(e)}", module="Detection", function="detect_single")
            return None

    def _detect_opencv(self, image: np.ndarray) -> List[Dict]:
        """
        OpenCV颜色检测（内部方法）
        
        基于HSV颜色空间进行污渍检测。
        
        参数:
            image: 输入图像
            
        返回:
            list: 检测结果列表
        """
        if len(image.shape) != 3:
            return []
        
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        
        detections = []
        
        for color_name, ranges in self._hsv_ranges.items():
            mask = np.zeros(image.shape[:2], dtype=np.uint8)
            
            for lower, upper in ranges:
                lower = np.array(lower, dtype=np.uint8)
                upper = np.array(upper, dtype=np.uint8)
                color_mask = cv2.inRange(hsv, lower, upper)
                mask = cv2.bitwise_or(mask, color_mask)
            
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                if area < self.config.detection.min_stain_area:
                    continue
                
                x, y, w, h = cv2.boundingRect(contour)
                
                detection = {
                    'label': color_name,
                    'confidence': self._calculate_confidence(area, image.shape),
                    'bbox': (int(x), int(y), int(w), int(h)),
                    'center': (int(x + w / 2), int(y + h / 2)),
                    'area': float(area)
                }
                
                detections.append(detection)
        
        return detections
    
    def _calculate_confidence(self, area: float, image_shape: Tuple) -> float:
        """
        计算置信度（内部方法）
        
        根据目标面积与图像面积的比例计算置信度。
        
        参数:
            area: 目标面积
            image_shape: 图像形状
            
        返回:
            float: 置信度（0-1）
        """
        image_area = image_shape[0] * image_shape[1]
        ratio = min(area / image_area, 0.1)
        confidence = 0.5 + ratio * 5
        
        return min(max(confidence, 0.5), 0.95)

    def _convert_yolo_results(self, yolo_results) -> List[Dict]:
        """
        转换YOLO检测结果为标准格式（内部方法）
        
        参数:
            yolo_results: YOLO模型返回的原始结果
            
        返回:
            list: 标准化的检测结果列表
        """
        detections = []
        
        if yolo_results is None:
            return detections
        
        try:
            for result in yolo_results:
                for box in result.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    w = x2 - x1
                    h = y2 - y1
                    
                    detection = {
                        'label': result.names[int(box.cls[0])],
                        'confidence': float(box.conf[0]),
                        'bbox': (int(x1), int(y1), int(w), int(h)),
                        'center': (int((x1 + x2) / 2), int((y1 + y2) / 2)),
                        'area': float(w * h)
                    }
                    
                    detections.append(detection)
        except Exception as e:
            logger.warning(f"YOLO结果转换失败: {str(e)}", 
                           module="Detection", function="_convert_yolo_results")
        
        return detections

    def detect_batch(self, images):
        """
        批量检测
        
        参数:
            images: 图像列表
            
        返回:
            list: 检测结果列表
        """
        results = []
        for idx, image in enumerate(images):
            result = self.detect_single(image, frame_id=f"batch_{idx:04d}")
            results.append(result)
        return results

    def set_confidence_threshold(self, threshold):
        """
        设置置信度阈值
        
        参数:
            threshold: 置信度阈值（0-1）
        """
        self.config.detection.confidence_threshold = threshold
        self.postprocessor.confidence_threshold = threshold
        
        if self.model is not None:
            self.model.set_confidence_threshold(threshold)
        
        logger.info(f"置信度阈值设置为: {threshold}", module="Detection", function="set_confidence_threshold")

    def get_confidence_threshold(self):
        """
        获取当前置信度阈值
        
        返回:
            float: 置信度阈值
        """
        if self.model is not None:
            return self.model.confidence_threshold
        return self.config.detection.confidence_threshold

    def set_iou_threshold(self, threshold):
        """
        设置IoU阈值
        
        参数:
            threshold: IoU阈值（0-1）
        """
        self.config.detection.iou_threshold = threshold
        
        if self.model is not None:
            self.model.set_iou_threshold(threshold)

    def get_model_info(self):
        """
        获取模型信息
        
        返回:
            dict: 模型信息
        """
        if self.model is not None:
            return self.model.get_model_info()
        return {'loaded': False, 'mode': self.mode}

    def is_model_ready(self):
        """
        检查模型是否就绪
        
        返回:
            bool: 是否就绪
        """
        if self.mode == "yolo":
            return self.is_model_loaded and self.model is not None
        return True

    def warmup(self):
        """
        预热模型（YOLO模式）
        """
        if self.model is not None:
            self.model.warmup()

    def set_detection_mode(self, mode: str):
        """
        设置检测模式
        
        参数:
            mode: 检测模式，可选 "opencv" 或 "yolo"
        """
        if mode in ["opencv", "yolo"]:
            self.mode = mode
            logger.info(f"检测模式切换为: {mode}", module="Detection", function="set_detection_mode")
            
            if mode == "yolo" and not self.is_model_loaded:
                logger.warning("切换到YOLO模式但模型未加载，建议先调用load_model()", 
                               module="Detection", function="set_detection_mode")
        else:
            logger.error(f"无效的检测模式: {mode}", module="Detection", function="set_detection_mode")


if __name__ == "__main__":
    """
    检测模块测试示例
    
    使用方法:
        python detection/detector.py
    """
    import sys
    import os
    
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from config import Config
    
    config = Config()
    
    print("=" * 60)
    print("MicroCleaningVision - 检测模块测试")
    print("=" * 60)
    
    print("\n1. 初始化检测器（OpenCV模式）...")
    detector = Detector(config, mode="opencv")
    print(f"   检测模式: {detector.mode}")
    print(f"   模型就绪: {detector.is_model_ready()}")
    
    print("\n2. 创建测试图像...")
    test_image = np.zeros((480, 640, 3), dtype=np.uint8)
    
    cv2.circle(test_image, (200, 200), 30, (0, 0, 255), -1)
    cv2.circle(test_image, (400, 300), 20, (0, 255, 0), -1)
    cv2.rectangle(test_image, (100, 350), (150, 400), (255, 0, 0), -1)
    
    print(f"   图像尺寸: {test_image.shape}")
    print("   包含: 红色圆形、绿色圆形、蓝色矩形")
    
    print("\n3. 测试单图像检测...")
    result = detector.detect_single(test_image)
    if result is not None:
        print(f"   检测成功!")
        print(f"   检测数量: {len(result.detections)}")
        print(f"   推理时间: {result.inference_time:.2f}ms")
        print(f"   使用模型: {result.model_name}")
        
        print("\n   检测结果:")
        for det in result.detections:
            print(f"   - {det['label']}: confidence={det['confidence']:.2f}, "
                  f"center={det['center']}, area={det['area']:.0f}")
    else:
        print("   检测失败")
    
    print("\n4. 测试置信度阈值设置...")
    detector.set_confidence_threshold(0.7)
    print(f"   新阈值: {detector.get_confidence_threshold()}")
    
    result2 = detector.detect_single(test_image)
    if result2 is not None:
        print(f"   检测数量: {len(result2.detections)}")
    
    print("\n5. 测试批量检测...")
    images = [test_image, test_image]
    results = detector.detect_batch(images)
    print(f"   输入: {len(images)} 张图像")
    print(f"   成功: {sum(1 for r in results if r is not None)} 个结果")
    
    print("\n6. 测试检测模式切换...")
    detector.set_detection_mode("yolo")
    print(f"   当前模式: {detector.mode}")
    print(f"   模型就绪: {detector.is_model_ready()}")
    
    detector.set_detection_mode("opencv")
    print(f"   当前模式: {detector.mode}")
    print(f"   模型就绪: {detector.is_model_ready()}")
    
    print("\n" + "=" * 60)
    print("检测模块测试完成")
    print("=" * 60)