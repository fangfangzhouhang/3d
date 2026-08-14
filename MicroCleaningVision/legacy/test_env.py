#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import torch
import cv2
import numpy as np
from ultralytics import YOLO

print("=" * 60)
print("MicroCleaningVision 环境测试")
print("=" * 60)

print("\n1. Python版本:")
import sys
print(f"   {sys.version}")

print("\n2. PyTorch版本:")
print(f"   {torch.__version__}")
print(f"   CUDA可用: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"   GPU名称: {torch.cuda.get_device_name(0)}")
    print(f"   CUDA版本: {torch.version.cuda}")

print("\n3. 关键依赖:")
import ultralytics
print(f"   ultralytics: {ultralytics.__version__}")
print(f"   OpenCV: {cv2.__version__}")
print(f"   NumPy: {np.__version__}")

print("\n4. 项目模块:")
from config import Config
from detection.detector import Detector
from models.yolo_model import YOLOModel
print("   config: OK")
print("   detector: OK")
print("   yolo_model: OK")

print("\n5. 模型测试:")
try:
    model = YOLO('yolov8n.pt')
    print("   YOLOv8n模型加载成功")
    
    dummy_img = np.zeros((640, 640, 3), dtype=np.uint8)
    results = model(dummy_img, verbose=False)
    print("   模型推理测试通过")
except Exception as e:
    print(f"   模型测试失败: {str(e)}")

print("\n" + "=" * 60)
print("测试完成!")
print("=" * 60)
