# API参考文档

本文档提供MicroCleaningVision系统的API参考。

## 目录结构

```
MicroCleaningVision/
├── camera/          # 相机模块
├── communication/   # 通信模块
├── dataset/         # 数据集模块
├── detection/       # 检测模块
├── docs/            # 文档目录
├── models/          # 模型模块
├── planning/        # 规划模块
├── reconstruction/  # 三维重建模块
├── training/        # 模型训练模块
├── utils/           # 工具模块
└── test/            # 测试模块
```

## 模块说明

### camera模块

负责相机连接、图像采集和校准。

#### CameraManager类

- `connect()` - 连接相机
- `disconnect()` - 断开连接
- `capture(camera_type)` - 采集图像
- `start_stream()` - 启动视频流
- `stop_stream()` - 停止视频流
- `get_camera_info()` - 获取相机信息
- `set_camera_parameter()` - 设置相机参数

### detection模块

负责图像预处理、AI检测和后处理。

#### Detector类

- `detect(image)` - 执行检测
- `load_model(model_path)` - 加载模型
- `set_threshold(threshold)` - 设置阈值
- `get_detection_results()` - 获取检测结果

### planning模块

负责路径规划、坐标计算和决策。

#### Planner类

- `plan_paths(targets)` - 规划路径
- `calculate_coordinates(detections)` - 计算目标坐标
- `decide_continue_cleaning(results)` - 判断是否继续清洗
- `optimize_path(path)` - 优化路径

#### CoordinateCalculator类

- `pixel_to_world(pixel_point)` - 像素坐标转世界坐标
- `world_to_pixel(world_point)` - 世界坐标转像素坐标
- `calculate_distance(point1, point2)` - 计算距离

#### SprayController类

- `control_spray(position, duration)` - 控制喷射
- `set_spray_params(params)` - 设置喷射参数
- `stop_spray()` - 停止喷射

#### DecisionMaker类

- `decide_cleaning_strategy(detections)` - 决策清洗策略
- `evaluate_cleaning_result(before, after)` - 评估清洗效果
- `should_continue_cleaning(result)` - 判断是否继续清洗

### communication模块

负责与STM32的串口通信。

#### STM32Communicator类

- `connect()` - 连接STM32
- `disconnect()` - 断开连接
- `send_command(command)` - 发送指令
- `receive_response()` - 接收应答
- `send_move_command(x, y)` - 发送移动指令
- `send_spray_command(duration)` - 发送喷水指令

### reconstruction模块

负责三维重建（后期开发）。

#### Reconstructor类

- `reconstruct(top_image, angle_image)` - 执行三维重建
- `generate_point_cloud(depth_map)` - 生成点云
- `save_mesh(file_path)` - 保存网格模型

### dataset模块

负责数据集管理和数据加载。

#### DatasetManager类

- `load_dataset(dataset_path)` - 加载数据集
- `split_dataset(train_ratio, val_ratio, test_ratio)` - 划分数据集
- `get_statistics()` - 获取统计信息

#### DataLoader类

- `load_image(image_path)` - 加载图像
- `load_batch(image_paths)` - 批量加载
- `__iter__()` - 返回迭代器
- `__len__()` - 获取长度

### models模块

负责AI模型管理和推理。

#### ModelManager类

- `load_model(model_name, model_path)` - 加载模型
- `switch_model(model_name)` - 切换模型
- `get_current_model()` - 获取当前模型

#### YOLOModel类

- `load()` - 加载模型
- `predict(image)` - 执行推理
- `set_confidence_threshold(threshold)` - 设置置信度阈值

### utils模块

提供通用工具函数。

#### Logger类

- `debug(message)` - 记录DEBUG日志
- `info(message)` - 记录INFO日志
- `warning(message)` - 记录WARNING日志
- `error(message)` - 记录ERROR日志
- `critical(message)` - 记录CRITICAL日志

#### ImageUtils类

- `read_image(file_path)` - 读取图像
- `write_image(image, file_path)` - 保存图像
- `resize_image(image, width, height)` - 调整大小
- `draw_bounding_box(image, bbox)` - 绘制边界框

#### CommonUtils类

- `get_current_time()` - 获取当前时间
- `create_directory(dir_path)` - 创建目录
- `generate_id()` - 生成唯一ID

## 数据流

系统的主要数据流如下：

1. **图像采集** → CameraManager.capture()
2. **图像预处理** → Preprocessor.preprocess()
3. **AI检测** → Detector.detect()
4. **坐标计算** → CoordinateCalculator.pixel_to_world()
5. **路径规划** → PathPlanner.plan_path()
6. **指令发送** → STM32Communicator.send_command()
7. **清洗执行** → STM32执行移动和喷射
8. **结果评估** → DecisionMaker.evaluate_cleaning_result()

## 配置参数

所有配置参数都在config.py中定义，包括：

- camera: 相机参数（分辨率、帧率、曝光等）
- detection: 检测参数（置信度阈值、IoU阈值等）
- communication: 通信参数（串口、波特率等）
- planning: 规划参数（移动速度、喷射时间等）
- logging: 日志参数（级别、文件路径等）
