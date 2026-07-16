#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - 检测后处理模块
=========================================================

功能描述:
    负责检测结果的过滤、筛选和后处理操作。
    
设计原则:
    1. 过滤低质量检测结果
    2. 计算目标面积和优先级
    3. 生成标准化的检测输出
    
实现状态:
    - ✅ 置信度过滤
    - ✅ 面积过滤
    - ✅ 多目标优先级排序
    - ✅ 检测结果合并
    - ✅ 坐标转换（像素坐标）
    - ✅ 生成标准化输出
"""


import cv2
import numpy as np
from typing import List, Tuple, Dict, Any, Optional


class DetectionPostprocessor:
    """
    检测后处理类
    
    负责对原始检测结果进行过滤和优化。
    
    Attributes:
        config: 配置对象
        logger: 日志对象
        min_area: 最小检测面积（像素）
        max_area: 最大检测面积（像素）
        confidence_threshold: 置信度阈值
    """
    
    def __init__(self, config, logger):
        """
        初始化检测后处理器
        
        参数:
            config: 配置对象
            logger: 日志对象
        """
        self.config = config
        self.logger = logger
        
        self.min_area = config.detection.min_stain_area
        self.max_area = config.detection.max_stain_area
        self.confidence_threshold = config.detection.confidence_threshold
        
        self.logger.info("检测后处理模块初始化完成", module="Postprocessing", function="__init__")
    
    def postprocess(self, raw_results: List[Dict]) -> List[Dict]:
        """
        执行完整后处理流程
        
        参数:
            raw_results: 原始检测结果列表
            
        返回:
            list: 后处理后的检测结果
        """
        if not raw_results:
            return []
        
        results = raw_results.copy()
        
        results = self.filter_by_confidence(results)
        results = self.filter_by_area(results)
        results = self.sort_by_priority(results)
        
        for result in results:
            result['center'] = self.calculate_center(result)
            result['area'] = self.calculate_area(result)
        
        self.logger.debug(f"后处理完成: 输入 {len(raw_results)} 个, 输出 {len(results)} 个", 
                         module="Postprocessing", function="postprocess")
        
        return results
    
    def filter_by_confidence(self, results: List[Dict]) -> List[Dict]:
        """
        按置信度过滤
        
        参数:
            results: 检测结果列表
            
        返回:
            list: 过滤后的结果
        """
        filtered = [r for r in results if r.get('confidence', 0) >= self.confidence_threshold]
        
        if len(filtered) < len(results):
            self.logger.debug(f"置信度过滤: {len(results)} -> {len(filtered)}", 
                             module="Postprocessing", function="filter_by_confidence")
        
        return filtered
    
    def filter_by_area(self, results: List[Dict]) -> List[Dict]:
        """
        按面积过滤
        
        参数:
            results: 检测结果列表
            
        返回:
            list: 过滤后的结果
        """
        filtered = []
        
        for result in results:
            area = self.calculate_area(result)
            if self.min_area <= area <= self.max_area:
                filtered.append(result)
        
        if len(filtered) < len(results):
            self.logger.debug(f"面积过滤: {len(results)} -> {len(filtered)}", 
                             module="Postprocessing", function="filter_by_area")
        
        return filtered
    
    def calculate_area(self, detection: Dict) -> float:
        """
        计算检测目标面积
        
        参数:
            detection: 单个检测结果（包含bbox字段）
            
        返回:
            float: 面积（像素）
        """
        bbox = detection.get('bbox', (0, 0, 0, 0))
        
        if len(bbox) == 4:
            x, y, w, h = bbox
            return float(w * h)
        elif len(bbox) == 2:
            return float(bbox[0] * bbox[1])
        else:
            return 0.0
    
    def calculate_center(self, detection: Dict) -> Tuple[int, int]:
        """
        计算检测目标中心坐标
        
        参数:
            detection: 单个检测结果（包含bbox字段）
            
        返回:
            tuple: (x, y) 中心坐标
        """
        bbox = detection.get('bbox', (0, 0, 0, 0))
        
        if len(bbox) == 4:
            x, y, w, h = bbox
            return (int(x + w / 2), int(y + h / 2))
        else:
            return (0, 0)
    
    def sort_by_priority(self, results: List[Dict]) -> List[Dict]:
        """
        按优先级排序（面积越大优先级越高）
        
        参数:
            results: 检测结果列表
            
        返回:
            list: 排序后的结果
        """
        results.sort(key=lambda r: self.calculate_area(r), reverse=True)
        
        self.logger.debug("检测结果已按面积优先级排序", 
                         module="Postprocessing", function="sort_by_priority")
        
        return results
    
    def merge_overlapping(self, results: List[Dict], iou_threshold: float = 0.5) -> List[Dict]:
        """
        合并重叠的检测结果
        
        参数:
            results: 检测结果列表
            iou_threshold: IoU阈值
            
        返回:
            list: 合并后的结果
        """
        if len(results) < 2:
            return results
        
        results_sorted = sorted(results, key=lambda r: self.calculate_area(r), reverse=True)
        merged = []
        
        while results_sorted:
            current = results_sorted.pop(0)
            to_merge = [current]
            
            remaining = []
            for result in results_sorted:
                iou = self._calculate_iou(current['bbox'], result['bbox'])
                if iou >= iou_threshold:
                    to_merge.append(result)
                else:
                    remaining.append(result)
            
            if len(to_merge) > 1:
                merged_result = self._merge_detections(to_merge)
                merged.append(merged_result)
                self.logger.debug(f"合并了 {len(to_merge)} 个重叠检测", 
                                 module="Postprocessing", function="merge_overlapping")
            else:
                merged.append(current)
            
            results_sorted = remaining
        
        return merged
    
    def _calculate_iou(self, bbox1: Tuple, bbox2: Tuple) -> float:
        """
        计算两个边界框的IoU（内部方法）
        
        参数:
            bbox1: 边界框1 (x, y, w, h)
            bbox2: 边界框2 (x, y, w, h)
            
        返回:
            float: IoU值
        """
        x1, y1, w1, h1 = bbox1
        x2, y2, w2, h2 = bbox2
        
        inter_x = max(x1, x2)
        inter_y = max(y1, y2)
        inter_w = min(x1 + w1, x2 + w2) - inter_x
        inter_h = min(y1 + h1, y2 + h2) - inter_y
        
        if inter_w <= 0 or inter_h <= 0:
            return 0.0
        
        inter_area = inter_w * inter_h
        union_area = w1 * h1 + w2 * h2 - inter_area
        
        return inter_area / union_area if union_area > 0 else 0.0
    
    def _merge_detections(self, detections: List[Dict]) -> Dict:
        """
        合并多个检测结果（内部方法）
        
        参数:
            detections: 待合并的检测结果列表
            
        返回:
            dict: 合并后的检测结果
        """
        bboxes = [d['bbox'] for d in detections]
        xs = [b[0] for b in bboxes]
        ys = [b[1] for b in bboxes]
        ws = [b[0] + b[2] for b in bboxes]
        hs = [b[1] + b[3] for b in bboxes]
        
        merged_bbox = (min(xs), min(ys), max(ws) - min(xs), max(hs) - min(ys))
        avg_confidence = sum(d.get('confidence', 0) for d in detections) / len(detections)
        
        return {
            'label': detections[0].get('label', 'merged'),
            'confidence': avg_confidence,
            'bbox': merged_bbox,
            'center': self.calculate_center({'bbox': merged_bbox}),
            'area': self.calculate_area({'bbox': merged_bbox}),
            'merged_count': len(detections)
        }
    
    def convert_coordinates(self, results: List[Dict], source_format: str, 
                           target_format: str) -> List[Dict]:
        """
        坐标转换（像素坐标内部转换）
        
        参数:
            results: 检测结果列表
            source_format: 源格式（如"pixel_xywh"）
            target_format: 目标格式（如"pixel_x1y1x2y2"）
            
        返回:
            list: 转换后的结果
        """
        converted = []
        
        for result in results:
            bbox = result.get('bbox', (0, 0, 0, 0))
            
            if source_format == 'pixel_xywh' and target_format == 'pixel_x1y1x2y2':
                x, y, w, h = bbox
                new_bbox = (x, y, x + w, y + h)
            elif source_format == 'pixel_x1y1x2y2' and target_format == 'pixel_xywh':
                x1, y1, x2, y2 = bbox
                new_bbox = (x1, y1, x2 - x1, y2 - y1)
            else:
                new_bbox = bbox
            
            converted_result = result.copy()
            converted_result['bbox'] = new_bbox
            converted.append(converted_result)
        
        self.logger.debug(f"坐标转换: {source_format} -> {target_format}", 
                         module="Postprocessing", function="convert_coordinates")
        
        return converted
    
    def generate_output(self, results: List[Dict]) -> Dict:
        """
        生成标准化输出
        
        参数:
            results: 检测结果列表
            
        返回:
            dict: 标准化输出格式
        """
        output = {
            'detection_count': len(results),
            'total_area': sum(self.calculate_area(r) for r in results),
            'targets': []
        }
        
        for idx, result in enumerate(results):
            target = {
                'id': f"target_{idx:03d}",
                'label': result.get('label', 'unknown'),
                'confidence': result.get('confidence', 0),
                'bbox': result.get('bbox', (0, 0, 0, 0)),
                'center': result.get('center', (0, 0)),
                'area': result.get('area', 0)
            }
            output['targets'].append(target)
        
        return output
    
    def process(self, raw_results: List[Dict]) -> List[Dict]:
        """
        执行完整后处理流程（与postprocess方法相同，提供统一接口）
        
        参数:
            raw_results: 原始检测结果
            
        返回:
            list: 后处理后的检测结果
        """
        return self.postprocess(raw_results)
    
    def merge_results(self, results1: List[Dict], results2: List[Dict]) -> List[Dict]:
        """
        合并两组检测结果
        
        参数:
            results1: 第一组检测结果
            results2: 第二组检测结果
            
        返回:
            list: 合并后的检测结果
        """
        combined = results1 + results2
        
        if len(combined) > 0:
            combined = self.merge_overlapping(combined)
            combined = self.sort_by_priority(combined)
        
        self.logger.debug(f"合并两组检测结果: {len(results1)} + {len(results2)} = {len(combined)}", 
                         module="Postprocessing", function="merge_results")
        
        return combined


if __name__ == "__main__":
    """
    检测后处理模块测试示例
    
    使用方法:
        python detection/postprocessing.py
    """
    import sys
    import os
    
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from config import Config
    
    config = Config()
    
    logger = __import__('logging').getLogger(__name__)
    logger.setLevel(__import__('logging').DEBUG)
    
    postprocessor = DetectionPostprocessor(config, logger)
    
    print("=" * 60)
    print("MicroCleaningVision - 检测后处理模块测试")
    print("=" * 60)
    
    test_results = [
        {'label': 'dust', 'confidence': 0.95, 'bbox': (100, 100, 50, 50)},
        {'label': 'oil', 'confidence': 0.80, 'bbox': (200, 200, 30, 30)},
        {'label': 'dust', 'confidence': 0.40, 'bbox': (300, 300, 40, 40)},
        {'label': 'scratch', 'confidence': 0.90, 'bbox': (105, 105, 40, 40)},
        {'label': 'residue', 'confidence': 0.92, 'bbox': (400, 400, 10, 10)},
    ]
    
    print("\n1. 测试置信度过滤...")
    filtered = postprocessor.filter_by_confidence(test_results)
    print(f"   输入: {len(test_results)} 个, 输出: {len(filtered)} 个")
    for r in filtered:
        print(f"   - {r['label']}: confidence={r['confidence']:.2f}")
    
    print("\n2. 测试面积过滤...")
    area_filtered = postprocessor.filter_by_area(test_results)
    print(f"   输入: {len(test_results)} 个, 输出: {len(area_filtered)} 个")
    
    print("\n3. 测试面积计算...")
    for r in test_results:
        area = postprocessor.calculate_area(r)
        print(f"   - {r['label']}: bbox={r['bbox']}, area={area:.0f}")
    
    print("\n4. 测试中心计算...")
    for r in test_results:
        center = postprocessor.calculate_center(r)
        print(f"   - {r['label']}: bbox={r['bbox']}, center={center}")
    
    print("\n5. 测试优先级排序...")
    sorted_results = postprocessor.sort_by_priority(test_results)
    print("   按面积从大到小排序:")
    for idx, r in enumerate(sorted_results):
        area = postprocessor.calculate_area(r)
        print(f"   {idx+1}. {r['label']}: area={area:.0f}")
    
    print("\n6. 测试重叠合并...")
    merged = postprocessor.merge_overlapping(test_results)
    print(f"   输入: {len(test_results)} 个, 输出: {len(merged)} 个")
    for r in merged:
        if 'merged_count' in r:
            print(f"   - {r['label']}(merged {r['merged_count']}个): area={r['area']:.0f}")
        else:
            print(f"   - {r['label']}: area={r['area']:.0f}")
    
    print("\n7. 测试坐标转换...")
    converted = postprocessor.convert_coordinates(test_results, 'pixel_xywh', 'pixel_x1y1x2y2')
    print("   xywh -> x1y1x2y2:")
    for r in converted[:2]:
        print(f"   - {r['label']}: {r['bbox']}")
    
    print("\n8. 测试完整后处理流程...")
    processed = postprocessor.postprocess(test_results)
    print(f"   输入: {len(test_results)} 个, 输出: {len(processed)} 个")
    for r in processed:
        print(f"   - {r['label']}: confidence={r['confidence']:.2f}, "
              f"center={r['center']}, area={r['area']:.0f}")
    
    print("\n9. 测试标准化输出...")
    output = postprocessor.generate_output(processed)
    print(f"   检测数量: {output['detection_count']}")
    print(f"   总面积: {output['total_area']:.0f}")
    print(f"   目标列表: {len(output['targets'])} 个")
    
    print("\n10. 测试合并两组结果...")
    results_a = [{'label': 'dust', 'confidence': 0.90, 'bbox': (100, 100, 50, 50)}]
    results_b = [{'label': 'dust', 'confidence': 0.85, 'bbox': (110, 110, 40, 40)}]
    merged = postprocessor.merge_results(results_a, results_b)
    print(f"   A组: {len(results_a)} 个, B组: {len(results_b)} 个, 合并后: {len(merged)} 个")
    
    print("\n" + "=" * 60)
    print("检测后处理模块测试完成")
    print("=" * 60)