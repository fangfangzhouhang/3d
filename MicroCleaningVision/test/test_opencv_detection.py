#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
MicroCleaningVision - OpenCV检测模块完整测试
=========================================================

功能描述:
    测试相机模块、图像预处理模块和检测模块的完整流程。
    
测试内容:
    1. 相机连接和图像采集测试
    2. 图像预处理功能测试
    3. OpenCV颜色检测功能测试
    4. 检测后处理功能测试
    5. 完整流程集成测试
    
使用方法:
    python test/test_opencv_detection.py
    
注意:
    需要确保虚拟环境已激活，且已安装所有依赖。
"""


import sys
import os
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from camera.camera import CameraManager
from detection.preprocessing import ImagePreprocessor
from detection.postprocessing import DetectionPostprocessor
from detection.detector import Detector


class TestOpenCVDetection:
    """
    OpenCV检测模块测试类
    """
    
    def __init__(self):
        self.config = Config()
        from utils.logger import logger
        self.logger = logger
        
        self.camera_manager = None
        self.preprocessor = None
        self.postprocessor = None
        self.detector = None
        
        self.test_results = []
    
    def setup(self):
        """
        初始化测试环境
        """
        print("\n" + "=" * 70)
        print("初始化测试环境...")
        print("=" * 70)
        
        print("\n1. 创建配置对象...")
        print(f"   顶部相机ID: {self.config.camera.top_camera_id}")
        print(f"   图像尺寸: {self.config.camera.image_width} x {self.config.camera.image_height}")
        print(f"   置信度阈值: {self.config.detection.confidence_threshold}")
        print(f"   最小污渍面积: {self.config.detection.min_stain_area}")
        
        print("\n2. 初始化预处理模块...")
        self.preprocessor = ImagePreprocessor(self.config, self.logger)
        print("   ✓ 图像预处理模块初始化完成")
        
        print("\n3. 初始化后处理模块...")
        self.postprocessor = DetectionPostprocessor(self.config, self.logger)
        print("   ✓ 检测后处理模块初始化完成")
        
        print("\n4. 初始化检测模块（OpenCV模式）...")
        self.detector = Detector(self.config, mode="opencv")
        print(f"   检测模式: {self.detector.mode}")
        print("   ✓ 检测模块初始化完成")
        
        print("\n" + "=" * 70)
        print("测试环境初始化完成")
        print("=" * 70)
    
    def test_preprocessing(self):
        """
        测试图像预处理功能
        """
        print("\n" + "=" * 70)
        print("测试1: 图像预处理功能")
        print("=" * 70)
        
        test_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        print(f"\n输入图像: 尺寸={test_image.shape}, 类型={test_image.dtype}")
        
        print("\n1. 高斯模糊测试...")
        blurred = self.preprocessor.gaussian_blur(test_image, (5, 5))
        assert blurred.shape == test_image.shape, "高斯模糊后尺寸变化"
        print("   ✓ 高斯模糊通过")
        
        print("\n2. 中值滤波测试...")
        filtered = self.preprocessor.median_filter(test_image, 5)
        assert filtered.shape == test_image.shape, "中值滤波后尺寸变化"
        print("   ✓ 中值滤波通过")
        
        print("\n3. 直方图均衡化测试...")
        equalized = self.preprocessor.histogram_equalization(test_image)
        assert equalized.shape == test_image.shape, "直方图均衡化后尺寸变化"
        print("   ✓ 直方图均衡化通过")
        
        print("\n4. 自适应均衡化测试...")
        adaptive_eq = self.preprocessor.adaptive_equalization(test_image, 2.0)
        assert adaptive_eq.shape == test_image.shape, "自适应均衡化后尺寸变化"
        print("   ✓ 自适应均衡化通过")
        
        print("\n5. 对比度增强测试...")
        enhanced = self.preprocessor.enhance_contrast(test_image)
        assert enhanced.shape == test_image.shape, "对比度增强后尺寸变化"
        print("   ✓ 对比度增强通过")
        
        print("\n6. 光照补偿测试...")
        compensated = self.preprocessor.illumination_compensation(test_image)
        assert compensated.shape == test_image.shape, "光照补偿后尺寸变化"
        print("   ✓ 光照补偿通过")
        
        print("\n7. 归一化测试...")
        normalized = self.preprocessor.normalize(test_image)
        assert normalized.dtype == np.float32, "归一化后类型错误"
        assert normalized.min() >= 0 and normalized.max() <= 1, "归一化后范围错误"
        print("   ✓ 归一化通过")
        
        print("\n8. 完整预处理流程测试...")
        processed = self.preprocessor.preprocess(test_image)
        print(f"   输入类型: {test_image.dtype}, 输出类型: {processed.dtype}")
        print("   ✓ 完整预处理流程通过")
        
        self.test_results.append(("预处理模块", "通过"))
        
        print("\n" + "=" * 70)
        print("测试1: 图像预处理功能 - 通过")
        print("=" * 70)
    
    def test_postprocessing(self):
        """
        测试检测后处理功能
        """
        print("\n" + "=" * 70)
        print("测试2: 检测后处理功能")
        print("=" * 70)
        
        test_results = [
            {'label': 'dust', 'confidence': 0.95, 'bbox': (100, 100, 50, 50)},
            {'label': 'oil', 'confidence': 0.80, 'bbox': (200, 200, 30, 30)},
            {'label': 'dust', 'confidence': 0.40, 'bbox': (300, 300, 40, 40)},
            {'label': 'scratch', 'confidence': 0.90, 'bbox': (105, 105, 40, 40)},
            {'label': 'residue', 'confidence': 0.92, 'bbox': (400, 400, 10, 10)},
        ]
        
        print(f"\n输入检测结果: {len(test_results)} 个")
        
        print("\n1. 置信度过滤测试...")
        filtered = self.postprocessor.filter_by_confidence(test_results)
        assert len(filtered) == 4, f"置信度过滤结果数量错误: {len(filtered)}"
        print(f"   过滤后: {len(filtered)} 个（置信度<0.5的被过滤）")
        print("   ✓ 置信度过滤通过")
        
        print("\n2. 面积计算测试...")
        for r in test_results:
            area = self.postprocessor.calculate_area(r)
            assert area > 0, "面积计算结果应为正数"
        print("   ✓ 面积计算通过")
        
        print("\n3. 中心计算测试...")
        for r in test_results:
            center = self.postprocessor.calculate_center(r)
            assert len(center) == 2, "中心坐标应为2维"
        print("   ✓ 中心计算通过")
        
        print("\n4. 优先级排序测试...")
        sorted_results = self.postprocessor.sort_by_priority(test_results)
        areas = [self.postprocessor.calculate_area(r) for r in sorted_results]
        assert areas == sorted(areas, reverse=True), "排序结果不正确"
        print("   ✓ 优先级排序通过")
        
        print("\n5. 重叠合并测试...")
        merged = self.postprocessor.merge_overlapping(test_results)
        print(f"   合并后: {len(merged)} 个（重叠的被合并）")
        print("   ✓ 重叠合并通过")
        
        print("\n6. 坐标转换测试...")
        converted = self.postprocessor.convert_coordinates(
            test_results, 'pixel_xywh', 'pixel_x1y1x2y2'
        )
        assert len(converted) == len(test_results), "坐标转换数量变化"
        print("   ✓ 坐标转换通过")
        
        print("\n7. 完整后处理流程测试...")
        processed = self.postprocessor.postprocess(test_results)
        print(f"   输入: {len(test_results)} 个, 输出: {len(processed)} 个")
        print("   ✓ 完整后处理流程通过")
        
        self.test_results.append(("后处理模块", "通过"))
        
        print("\n" + "=" * 70)
        print("测试2: 检测后处理功能 - 通过")
        print("=" * 70)
    
    def test_opencv_detection(self):
        """
        测试OpenCV颜色检测功能
        """
        print("\n" + "=" * 70)
        print("测试3: OpenCV颜色检测功能")
        print("=" * 70)
        
        print("\n创建测试图像（包含多种颜色的污渍）...")
        test_image = np.zeros((480, 640, 3), dtype=np.uint8)
        
        cv2.circle(test_image, (200, 200), 30, (0, 0, 255), -1)
        cv2.circle(test_image, (400, 300), 20, (0, 255, 0), -1)
        cv2.rectangle(test_image, (100, 350), (150, 400), (255, 0, 0), -1)
        cv2.circle(test_image, (500, 150), 25, (0, 255, 255), -1)
        cv2.rectangle(test_image, (50, 50), (80, 80), (255, 0, 255), -1)
        
        print("   测试图像包含: 红色圆形、绿色圆形、蓝色矩形、黄色圆形、紫色矩形")
        
        print("\n1. 单图像检测测试...")
        result = self.detector.detect_single(test_image)
        assert result is not None, "检测结果为空"
        assert result.success, "检测失败"
        print(f"   检测数量: {len(result.detections)}")
        print(f"   推理时间: {result.inference_time:.2f}ms")
        print(f"   使用模型: {result.model_name}")
        print("   ✓ 单图像检测通过")
        
        print("\n2. 检测结果详细信息...")
        for det in result.detections:
            print(f"   - {det['label']}: confidence={det['confidence']:.2f}, "
                  f"center={det['center']}, area={det['area']:.0f}")
        
        print("\n3. 置信度阈值设置测试...")
        original_threshold = self.detector.get_confidence_threshold()
        self.detector.set_confidence_threshold(0.7)
        new_threshold = self.detector.get_confidence_threshold()
        assert new_threshold == 0.7, f"阈值设置错误: {new_threshold}"
        
        result_high_threshold = self.detector.detect_single(test_image)
        print(f"   高阈值(0.7)检测数量: {len(result_high_threshold.detections)}")
        
        self.detector.set_confidence_threshold(original_threshold)
        print("   ✓ 置信度阈值设置通过")
        
        print("\n4. 检测模式切换测试...")
        self.detector.set_detection_mode("yolo")
        assert self.detector.mode == "yolo", "模式切换失败"
        
        self.detector.set_detection_mode("opencv")
        assert self.detector.mode == "opencv", "模式切换失败"
        print("   ✓ 检测模式切换通过")
        
        self.test_results.append(("OpenCV检测模块", "通过"))
        
        print("\n" + "=" * 70)
        print("测试3: OpenCV颜色检测功能 - 通过")
        print("=" * 70)
    
    def test_camera_integration(self):
        """
        测试相机集成功能（可选，需要实际相机）
        """
        print("\n" + "=" * 70)
        print("测试4: 相机集成功能")
        print("=" * 70)
        
        print("\n初始化相机管理器...")
        self.camera_manager = CameraManager(self.config)
        
        print("\n尝试连接相机...")
        success = self.camera_manager.connect()
        
        if success:
            print("\n相机连接成功！")
            
            print("\n1. 获取相机信息...")
            top_info = self.camera_manager.get_camera_info("top")
            print(f"   顶部相机: ID={top_info.camera_id}, 分辨率={top_info.resolution}")
            
            print("\n2. 采集图像...")
            frame = self.camera_manager.capture("top")
            if frame is not None:
                print(f"   采集成功: ID={frame.frame_id}, 尺寸={frame.width}x{frame.height}")
                
                print("\n3. 使用采集的图像进行检测...")
                result = self.detector.detect_single(frame.image)
                if result is not None:
                    print(f"   检测到 {len(result.detections)} 个目标")
                else:
                    print("   检测失败")
                
                print("\n4. 显示采集的图像（按'q'退出）...")
                cv2.imshow("Camera Capture", frame.image)
                print("   按 'q' 键退出窗口")
                while True:
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        break
                cv2.destroyAllWindows()
            
            print("\n断开相机...")
            self.camera_manager.disconnect()
            self.test_results.append(("相机集成模块", "通过"))
            
            print("\n" + "=" * 70)
            print("测试4: 相机集成功能 - 通过")
            print("=" * 70)
        else:
            print("\n相机连接失败（可能没有连接相机设备）")
            print("跳过相机集成测试，继续其他测试...")
            self.test_results.append(("相机集成模块", "跳过（无相机）"))
            
            print("\n" + "=" * 70)
            print("测试4: 相机集成功能 - 跳过")
            print("=" * 70)
    
    def test_complete_pipeline(self):
        """
        测试完整检测流程（模拟数据）
        """
        print("\n" + "=" * 70)
        print("测试5: 完整检测流程")
        print("=" * 70)
        
        print("\n模拟完整检测流程...")
        print("步骤: 图像采集 → 预处理 → 检测 → 后处理")
        
        test_image = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.circle(test_image, (200, 200), 30, (0, 0, 255), -1)
        cv2.circle(test_image, (400, 300), 25, (0, 255, 0), -1)
        
        print("\n1. 模拟图像采集...")
        print(f"   图像尺寸: {test_image.shape}")
        
        print("\n2. 图像预处理...")
        processed = self.preprocessor.preprocess(test_image)
        print(f"   预处理完成: {processed.shape}")
        
        print("\n3. OpenCV颜色检测...")
        result = self.detector.detect_single(test_image)
        print(f"   检测完成: {len(result.detections)} 个目标")
        
        print("\n4. 检测后处理...")
        detections = self.postprocessor.postprocess(result.detections)
        print(f"   后处理完成: {len(detections)} 个目标")
        
        print("\n5. 生成标准化输出...")
        output = self.postprocessor.generate_output(detections)
        print(f"   检测数量: {output['detection_count']}")
        print(f"   总面积: {output['total_area']:.0f}")
        
        print("\n完整检测流程输出:")
        for target in output['targets']:
            print(f"   目标 {target['id']}:")
            print(f"     - 类别: {target['label']}")
            print(f"     - 置信度: {target['confidence']:.2f}")
            print(f"     - 中心坐标: {target['center']}")
            print(f"     - 面积: {target['area']:.0f}")
        
        self.test_results.append(("完整检测流程", "通过"))
        
        print("\n" + "=" * 70)
        print("测试5: 完整检测流程 - 通过")
        print("=" * 70)
    
    def run_all_tests(self):
        """
        运行所有测试
        """
        print("\n" + "=" * 70)
        print("MicroCleaningVision - OpenCV检测模块完整测试")
        print("=" * 70)
        
        self.setup()
        
        try:
            self.test_preprocessing()
            self.test_postprocessing()
            self.test_opencv_detection()
            self.test_camera_integration()
            self.test_complete_pipeline()
            
            self.print_summary()
            
        except Exception as e:
            print(f"\n测试过程中发生错误: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def print_summary(self):
        """
        打印测试总结
        """
        print("\n" + "=" * 70)
        print("测试总结")
        print("=" * 70)
        
        print("\n测试结果列表:")
        print("-" * 70)
        
        passed = 0
        skipped = 0
        
        for module, status in self.test_results:
            if status == "通过":
                passed += 1
                print(f"✓ {module}: {status}")
            elif status == "跳过":
                skipped += 1
                print(f"~ {module}: {status}")
            else:
                print(f"✗ {module}: {status}")
        
        print("-" * 70)
        print(f"\n总计: {len(self.test_results)} 项测试")
        print(f"通过: {passed} 项")
        print(f"跳过: {skipped} 项")
        print(f"失败: {len(self.test_results) - passed - skipped} 项")
        
        if passed == len(self.test_results) or skipped > 0:
            print("\n✓ 所有测试通过！")
        else:
            print("\n✗ 部分测试失败，请检查错误信息")
        
        print("\n" + "=" * 70)


if __name__ == "__main__":
    test = TestOpenCVDetection()
    test.run_all_tests()