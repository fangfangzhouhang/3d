#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from models.yolo_model import YOLOModel
from detection.preprocessing import ImagePreprocessor as Preprocessor
from detection.postprocessing import DetectionPostprocessor as Postprocessor
from utils.logger import logger


class Detector:
    def __init__(self, config):
        self.config = config
        self.model = None
        self.preprocessor = Preprocessor(config, logger)
        self.postprocessor = Postprocessor(config, logger)
        self.is_model_loaded = False
        
        logger.info("检测器初始化完成")

    def load_model(self, model_path=None):
        if model_path is None:
            model_path = self.config.models.yolo_model_path
        
        try:
            self.model = YOLOModel(self.config, logger)
            success = self.model.load()
            
            if success:
                self.is_model_loaded = True
                self.model.warmup()
                logger.info(f"模型加载成功: {model_path}")
            else:
                logger.error(f"模型加载失败: {model_path}")
            
            return success
        except Exception as e:
            logger.error(f"模型加载异常: {str(e)}")
            return False

    def unload_model(self):
        if self.model is not None:
            self.model.unload()
            self.model = None
            self.is_model_loaded = False
            logger.info("模型已卸载")

    def detect(self, top_image, angle_image):
        if not self.is_model_loaded:
            logger.error("模型未加载")
            return None
        
        try:
            top_image_preprocessed = self.preprocessor.process(top_image)
            angle_image_preprocessed = self.preprocessor.process(angle_image)
            
            top_detections = self.model.predict(top_image_preprocessed)
            angle_detections = self.model.predict(angle_image_preprocessed)
            
            combined_detections = self.postprocessor.merge_results(top_detections, angle_detections)
            
            return {
                'top_detections': top_detections,
                'angle_detections': angle_detections,
                'combined': combined_detections
            }
        except Exception as e:
            logger.error(f"检测失败: {str(e)}")
            return None

    def detect_single(self, image):
        if not self.is_model_loaded:
            logger.error("模型未加载")
            return None
        
        try:
            preprocessed = self.preprocessor.process(image)
            detections = self.model.predict(preprocessed)
            return self.postprocessor.process(detections)
        except Exception as e:
            logger.error(f"单图像检测失败: {str(e)}")
            return None

    def detect_batch(self, images):
        results = []
        for image in images:
            result = self.detect_single(image)
            results.append(result)
        return results

    def set_confidence_threshold(self, threshold):
        if self.model is not None:
            self.model.set_confidence_threshold(threshold)
            logger.info(f"置信度阈值设置为: {threshold}")

    def get_confidence_threshold(self):
        if self.model is not None:
            return self.model.confidence_threshold
        return self.config.detection.confidence_threshold

    def set_iou_threshold(self, threshold):
        if self.model is not None:
            self.model.set_iou_threshold(threshold)

    def get_model_info(self):
        if self.model is not None:
            return self.model.get_model_info()
        return {'loaded': False}

    def is_model_ready(self):
        return self.is_model_loaded and self.model is not None

    def warmup(self):
        if self.model is not None:
            self.model.warmup()
