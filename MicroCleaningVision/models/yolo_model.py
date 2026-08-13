#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from ultralytics import YOLO
import torch
import cv2
import numpy as np


class YOLOModel:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.model = None
        self.model_path = config.model.yolo_model_path
        self.confidence_threshold = config.detection.confidence_threshold
        self.iou_threshold = config.detection.iou_threshold
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        
        self.logger.info(f"YOLO模型模块初始化完成, 设备: {self.device}")

    def load(self):
        try:
            self.model = YOLO(self.model_path)
            self.model.to(self.device)
            self.logger.info(f"YOLO模型加载成功: {self.model_path}")
            return True
        except Exception as e:
            self.logger.error(f"YOLO模型加载失败: {str(e)}")
            return False

    def unload(self):
        if self.model is not None:
            self.model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            self.logger.info("YOLO模型已卸载")

    def predict(self, image):
        if self.model is None:
            self.logger.error("模型未加载")
            return []
        
        try:
            results = self.model(
                image,
                conf=self.confidence_threshold,
                iou=self.iou_threshold,
                device=self.device,
                verbose=False
            )
            
            detections = []
            for result in results:
                for box in result.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    cls = int(box.cls[0])
                    
                    area = (x2 - x1) * (y2 - y1)
                    
                    detections.append({
                        'bbox': [x1, y1, x2, y2],
                        'confidence': conf,
                        'class': cls,
                        'class_name': self.config.detection.class_names[cls] if cls < len(self.config.detection.class_names) else 'unknown',
                        'area': area
                    })
            
            return detections
        except Exception as e:
            self.logger.error(f"推理失败: {str(e)}")
            return []

    def predict_batch(self, images):
        results = []
        for image in images:
            results.append(self.predict(image))
        return results

    def set_confidence_threshold(self, threshold):
        self.confidence_threshold = threshold

    def set_iou_threshold(self, threshold):
        self.iou_threshold = threshold

    def get_classes(self):
        return self.config.detection.class_names

    def get_model_info(self):
        if self.model is None:
            return {'loaded': False}
        return {
            'loaded': True,
            'path': self.model_path,
            'device': self.device,
            'classes': self.get_classes()
        }

    def is_loaded(self):
        return self.model is not None

    def warmup(self):
        if self.model is not None:
            dummy_image = np.zeros((640, 640, 3), dtype=np.uint8)
            self.predict(dummy_image)
            self.logger.info("模型预热完成")
