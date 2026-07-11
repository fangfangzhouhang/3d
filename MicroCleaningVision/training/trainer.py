#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import yaml
import os
from ultralytics import YOLO
import torch


def load_config(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def train_model(data_yaml, model_type='yolov8n', epochs=100, batch_size=8, img_size=640):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"使用设备: {device}")
    print(f"CUDA可用: {torch.cuda.is_available()}")
    
    model = YOLO(f'{model_type}.pt')
    
    results = model.train(
        data=data_yaml,
        epochs=epochs,
        batch=batch_size,
        imgsz=img_size,
        device=device,
        verbose=True,
        workers=4,
        optimizer='AdamW',
        lr0=0.01,
        patience=10,
        save=True,
        save_period=5,
        val=True,
        plots=True
    )
    
    return results


def evaluate_model(model_path, data_yaml):
    model = YOLO(model_path)
    
    results = model.val(
        data=data_yaml,
        verbose=True
    )
    
    return results


def main():
    parser = argparse.ArgumentParser(description='MicroCleaningVision 模型训练')
    parser.add_argument('--train', action='store_true', help='执行训练')
    parser.add_argument('--evaluate', action='store_true', help='执行评估')
    parser.add_argument('--model', type=str, default='yolov8n', help='模型类型')
    parser.add_argument('--epochs', type=int, default=100, help='训练轮次')
    parser.add_argument('--batch', type=int, default=8, help='批次大小')
    parser.add_argument('--imgsz', type=int, default=640, help='图像尺寸')
    parser.add_argument('--data', type=str, default='output/datasets/data.yaml', help='数据集配置文件')
    parser.add_argument('--weights', type=str, default='output/models/best.pt', help='模型权重路径')
    
    args = parser.parse_args()
    
    if args.train:
        print(f"开始训练模型: {args.model}")
        print(f"训练轮次: {args.epochs}")
        print(f"批次大小: {args.batch}")
        print(f"图像尺寸: {args.imgsz}")
        print(f"数据集: {args.data}")
        
        results = train_model(
            data_yaml=args.data,
            model_type=args.model,
            epochs=args.epochs,
            batch_size=args.batch,
            img_size=args.imgsz
        )
        
        print("训练完成!")
    
    if args.evaluate:
        print(f"开始评估模型: {args.weights}")
        results = evaluate_model(args.weights, args.data)
        print("评估完成!")


if __name__ == '__main__':
    main()
