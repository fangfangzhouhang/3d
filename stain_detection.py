#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微米级3D打印后处理智能清洗系统 - 视觉识别模块

功能说明:
1. 读取并显示显微镜摄像头画面
2. 在画面中央画辅助十字线
3. 识别红色污渍并标记位置
4. 计算污渍与画面中心的偏差
5. 自动触发喷水（串口发送）
6. 评估清洗效果
7. 阈值可调（全局变量）
8. 键盘控制（q退出, s手动喷水, +-调整阈值）

适用环境: Python 3.8+ + OpenCV + pyserial
硬件: USB显微镜 + STM32串口控制蠕动泵
"""

# --------------------------
# 1. 导入必要的库
# --------------------------
import cv2
import numpy as np
import serial
import time

# --------------------------
# 2. 全局变量定义（方便调试）
# --------------------------

# HSV颜色空间中红色的阈值范围
# 红色在HSV中跨越0度附近，所以需要两个范围
# H: 色调 (0-180), S: 饱和度 (0-255), V: 亮度 (0-255)
RED_HSV_LOWER1 = np.array([0, 120, 70])    # 红色范围1的下限
RED_HSV_UPPER1 = np.array([10, 255, 255])  # 红色范围1的上限
RED_HSV_LOWER2 = np.array([170, 120, 70])  # 红色范围2的下限
RED_HSV_UPPER2 = np.array([180, 255, 255]) # 红色范围2的上限

# 喷水触发区域半径（像素）
SPRAY_RADIUS = 20

# 串口设置
SERIAL_PORT = 'COM3'    # STM32连接的串口，根据实际情况修改
BAUD_RATE = 115200

# 画面中心标记十字线长度（像素）
CROSSHAIR_LENGTH = 40

# --------------------------
# 3. 状态机状态定义
# --------------------------
# 状态机用于管理喷水和评估流程，避免阻塞摄像头画面
STATE_NORMAL = 0        # 正常检测状态
STATE_SPRAYING = 1      # 正在喷水状态
STATE_EVALUATING = 2    # 正在评估清洗效果状态

# --------------------------
# 4. 全局状态变量
# --------------------------
current_state = STATE_NORMAL
serial_connected = False  # 串口是否连接成功
serial_port = None        # 串口对象
pre_spray_area = 0        # 喷水前的污渍面积
spray_start_time = 0      # 喷水开始时间
message_text = ""         # 要在画面上显示的消息
message_start_time = 0    # 消息开始显示的时间
message_duration = 1.0    # 消息显示持续时间（秒）

# --------------------------
# 5. 初始化函数
# --------------------------

def init_serial():
    """初始化串口连接"""
    global serial_port, serial_connected
    try:
        # 尝试打开串口，超时时间1秒
        serial_port = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        # 等待串口初始化完成
        time.sleep(0.5)
        serial_connected = True
        print(f"✓ 串口连接成功: {SERIAL_PORT} @ {BAUD_RATE}")
    except serial.SerialException as e:
        # 如果串口打开失败，记录错误但继续运行
        serial_connected = False
        serial_port = None
        print(f"✗ 串口连接失败: {e}")
        print(f"  程序将继续运行，但不会发送喷水指令")
        print(f"  请检查串口是否正确连接，当前设置: {SERIAL_PORT}")

def init_camera():
    """
    初始化USB显微镜摄像头
    
    USB显微镜摄像头是外置设备，需要尝试不同的OpenCV后端和摄像头ID
    常见的后端：
    - cv2.CAP_DSHOW: Windows DirectShow，适合大多数USB摄像头
    - cv2.CAP_MSMF: Windows Media Foundation，更现代的后端
    - cv2.CAP_ANY: 自动选择后端
    """
    # 定义要尝试的后端列表
    backends = [
        (cv2.CAP_DSHOW, "CAP_DSHOW"),
        (cv2.CAP_MSMF, "CAP_MSMF"),
        (cv2.CAP_ANY, "CAP_ANY"),
    ]
    
    # 定义要尝试的摄像头ID范围（通常外置USB摄像头ID会是0、1、2）
    camera_ids = [0, 1, 2]
    
    print("\n正在搜索可用的摄像头设备...")
    print("=" * 50)
    
    # 记录所有成功打开的摄像头
    available_cameras = []
    
    # 遍历所有组合尝试打开摄像头
    for camera_id in camera_ids:
        for backend, backend_name in backends:
            # 创建VideoCapture对象，指定后端
            cap = cv2.VideoCapture(camera_id, backend)
            
            if cap.isOpened():
                # 读取一帧验证是否真的能获取画面
                ret, _ = cap.read()
                if ret:
                    print(f"✓ 找到摄像头: ID={camera_id}, 后端={backend_name}")
                    available_cameras.append((camera_id, backend, backend_name))
                # 释放资源，后面会重新打开
                cap.release()
    
    print("=" * 50)
    
    # 如果没有找到任何摄像头
    if len(available_cameras) == 0:
        print("\n✗ 没有找到任何可用的摄像头！")
        print("  请检查：")
        print("  1. USB显微镜是否正确连接电脑")
        print("  2. 是否使用USB数据线直接连接（不要用USB集线器）")
        print("  3. 摄像头驱动是否已安装（有些USB显微镜需要单独安装驱动）")
        print("  4. 是否有其他程序占用了摄像头（如微信、钉钉、系统相机等）")
        print("  5. 尝试更换USB接口（推荐使用USB 3.0接口）")
        print("  6. 在设备管理器中检查摄像头是否被识别")
        return None
    
    # 如果找到多个摄像头，让用户手动选择
    if len(available_cameras) > 1:
        print(f"\n找到 {len(available_cameras)} 个可用摄像头：")
        for i, (camera_id, backend, backend_name) in enumerate(available_cameras):
            print(f"  {i+1}. ID={camera_id}, 后端={backend_name}")
        
        # 让用户输入摄像头ID来选择
        print("\n请输入你要使用的摄像头ID（输入0或1）：")
        while True:
            try:
                user_input = input("输入摄像头ID: ")
                selected_id = int(user_input)
                
                # 检查用户输入的ID是否在可用摄像头列表中
                found = False
                for camera_id, backend, backend_name in available_cameras:
                    if camera_id == selected_id:
                        selected_camera = (camera_id, backend, backend_name)
                        found = True
                        break
                
                if found:
                    print(f"\n你选择了摄像头 ID={selected_id}")
                    break
                else:
                    print(f"❌ 无效的摄像头ID！请从以下ID中选择: {[cam[0] for cam in available_cameras]}")
            except ValueError:
                print("❌ 请输入有效的数字（0或1）！")
    else:
        # 只有一个摄像头，直接使用
        selected_camera = available_cameras[0]
    
    # 打开选中的摄像头
    camera_id, backend, backend_name = selected_camera
    cap = cv2.VideoCapture(camera_id, backend)
    
    if cap.isOpened():
        print(f"\n✓ 成功打开 USB显微镜摄像头")
        print(f"  摄像头ID: {camera_id}")
        print(f"  使用后端: {backend_name}")
        
        # 设置摄像头参数（可选）
        # 设置分辨率（如果摄像头支持）
        # cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        # cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        
        return cap
    else:
        print(f"\n✗ 打开摄像头失败！")
        return None

# --------------------------
# 6. 核心功能函数
# --------------------------

def detect_red_stain(frame):
    """
    检测画面中的红色污渍
    
    参数:
        frame: 原始BGR图像
    
    返回:
        contours: 检测到的红色区域轮廓列表
        center: 污渍中心坐标 (x, y)，未检测到返回None
        area: 污渍面积（像素），未检测到返回0
    """
    # 将BGR图像转换为HSV颜色空间
    # BGR是OpenCV默认的颜色格式，HSV更适合颜色识别
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # 创建两个红色范围的掩膜（mask）
    # inRange函数返回二值图像：在范围内的像素为白色(255)，否则为黑色(0)
    mask1 = cv2.inRange(hsv, RED_HSV_LOWER1, RED_HSV_UPPER1)
    mask2 = cv2.inRange(hsv, RED_HSV_LOWER2, RED_HSV_UPPER2)
    
    # 将两个掩膜合并（因为红色跨越了0度）
    # bitwise_or：只要任一掩膜中对应像素为白色，结果就是白色
    mask = cv2.bitwise_or(mask1, mask2)
    
    # 形态学开运算：先腐蚀后膨胀，用于去除小的噪声点
    # 这是一种简单但有效的去噪方法
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    
    # 形态学闭运算：先膨胀后腐蚀，用于填充污渍内部的小洞
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    
    # 查找轮廓
    # findContours返回轮廓列表和层级信息（我们不需要层级，用_忽略）
    # RETR_EXTERNAL: 只检测最外层轮廓
    # CHAIN_APPROX_SIMPLE: 压缩轮廓点，节省内存
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # 如果没有检测到轮廓，返回空结果
    if len(contours) == 0:
        return contours, None, 0
    
    # 找到面积最大的轮廓（假设最大的就是我们要找的污渍）
    max_area = 0
    max_contour = None
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > max_area:
            max_area = area
            max_contour = contour
    
    # 如果最大面积太小，可能是噪声，忽略掉
    if max_area < 50:  # 面积小于50像素认为是噪声
        return [], None, 0
    
    # 计算轮廓的中心坐标
    # moments函数计算轮廓的矩，用于求中心
    M = cv2.moments(max_contour)
    if M["m00"] != 0:
        center_x = int(M["m10"] / M["m00"])
        center_y = int(M["m01"] / M["m00"])
        center = (center_x, center_y)
    else:
        center = None
    
    return [max_contour], center, max_area

def draw_crosshair(frame):
    """
    在画面中央画绿色十字线
    
    参数:
        frame: 要绘制的图像
    """
    # 获取画面尺寸
    height, width = frame.shape[:2]
    
    # 计算画面中心坐标
    center_x = width // 2
    center_y = height // 2
    
    # 绘制水平线（横线）
    # line(图像, 起点, 终点, 颜色, 线宽)
    # 颜色使用BGR格式，(0, 255, 0)表示绿色
    cv2.line(frame, 
             (center_x - CROSSHAIR_LENGTH, center_y),  # 起点：中心左侧
             (center_x + CROSSHAIR_LENGTH, center_y),  # 终点：中心右侧
             (0, 255, 0),  # 绿色
             2)             # 线宽2像素
    
    # 绘制垂直线（竖线）
    cv2.line(frame, 
             (center_x, center_y - CROSSHAIR_LENGTH),  # 起点：中心上方
             (center_x, center_y + CROSSHAIR_LENGTH),  # 终点：中心下方
             (0, 255, 0),  # 绿色
             2)             # 线宽2像素

def draw_stain_marker(frame, contours, center):
    """
    在画面上标记污渍位置
    
    参数:
        frame: 要绘制的图像
        contours: 污渍轮廓列表
        center: 污渍中心坐标
    """
    # 绘制污渍轮廓（绿色圆圈）
    # drawContours(图像, 轮廓列表, 轮廓索引(-1表示所有), 颜色, 线宽)
    cv2.drawContours(frame, contours, -1, (0, 255, 0), 2)
    
    # 在中心画红色圆点
    if center is not None:
        # circle(图像, 圆心, 半径, 颜色, 厚度(-1表示填充))
        cv2.circle(frame, center, 5, (0, 0, 255), -1)

def calculate_deviation(center, frame_center):
    """
    计算污渍中心与画面中心的偏差
    
    参数:
        center: 污渍中心坐标 (x, y)
        frame_center: 画面中心坐标 (x, y)
    
    返回:
        dx: x方向偏差（像素）
        dy: y方向偏差（像素）
    """
    if center is None:
        return None, None
    
    dx = center[0] - frame_center[0]
    dy = center[1] - frame_center[1]
    
    return dx, dy

def send_spray_command():
    """发送喷水指令到串口"""
    global message_text, message_start_time
    
    if serial_connected and serial_port is not None:
        try:
            # 发送字符 '1' 到串口
            serial_port.write(b'1')
            print("→ 已发送喷水指令")
            message_text = "Spraying!"
        except serial.SerialException as e:
            print(f"✗ 发送指令失败: {e}")
            message_text = "Send failed!"
    else:
        # 串口未连接，只显示消息
        message_text = "Spraying! (Simulated)"
    
    message_start_time = time.time()

def update_message():
    """更新画面上显示的消息"""
    global message_text
    
    # 如果消息显示时间超过持续时间，清除消息
    if message_text != "" and time.time() - message_start_time > message_duration:
        message_text = ""

def draw_info(frame, center, dx, dy, area, before_area=0):
    """
    在画面左上角绘制信息文字
    
    参数:
        frame: 要绘制的图像
        center: 污渍中心坐标
        dx, dy: 偏差值
        area: 当前污渍面积
        before_area: 喷水前的污渍面积
    """
    # 定义文字显示的起始位置
    start_y = 30
    line_height = 30
    
    # 设置文字样式
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.8
    font_thickness = 2
    
    # 显示污渍中心坐标
    if center is not None:
        text = f"Center: ({center[0]}, {center[1]})"
        cv2.putText(frame, text, (20, start_y), font, font_scale, (0, 255, 0), font_thickness)
    else:
        text = "No target detected"
        cv2.putText(frame, text, (20, start_y), font, font_scale, (0, 0, 255), font_thickness)
    
    # 显示偏差值
    if dx is not None and dy is not None:
        text = f"Deviation: dx={dx:3d}, dy={dy:3d}"
        cv2.putText(frame, text, (20, start_y + line_height), font, font_scale, (255, 0, 0), font_thickness)
    
    # 显示当前面积
    if area > 0:
        text = f"Area: {area} px"
        cv2.putText(frame, text, (20, start_y + line_height * 2), font, font_scale, (0, 255, 255), font_thickness)
    
    # 显示清洗效果评估
    if before_area > 0:
        text = f"Before: {before_area} px → After: {area} px"
        cv2.putText(frame, text, (20, start_y + line_height * 3), font, font_scale, (128, 128, 128), font_thickness)
        
        # 计算面积减少比例
        reduction_ratio = (before_area - area) / before_area * 100
        
        if reduction_ratio > 30:
            text = "Cleaning effective!"
            color = (0, 255, 0)
        else:
            text = "Try adjust parameters"
            color = (0, 0, 255)
        
        cv2.putText(frame, text, (20, start_y + line_height * 4), font, font_scale, color, font_thickness)
    
    # 显示喷水消息
    if message_text != "":
        cv2.putText(frame, message_text, (20, start_y + line_height * 5), 
                    font, 1.5, (0, 0, 255), 3)

# --------------------------
# 7. 主程序入口
# --------------------------

def main():
    global current_state, pre_spray_area, spray_start_time
    
    print("=" * 60)
    print("  微米级3D打印后处理智能清洗系统 - 视觉识别模块")
    print("=" * 60)
    
    # 初始化串口
    init_serial()
    
    # 初始化摄像头
    cap = init_camera()
    if cap is None:
        print("✗ 无法继续，摄像头打开失败")
        return
    
    print("\n操作说明:")
    print("  q - 退出程序")
    print("  s - 手动触发一次喷水（测试用）")
    print("  + - 增加红色识别灵敏度（降低饱和度下限）")
    print("  - - 降低红色识别灵敏度（提高饱和度下限）")
    print("\n等待摄像头画面...")
    
    # 主循环
    while True:
        # 读取一帧画面
        # ret: 读取是否成功（布尔值）
        # frame: 图像帧（BGR格式）
        ret, frame = cap.read()
        
        # 如果读取失败，跳出循环
        if not ret:
            print("✗ 无法读取摄像头画面")
            break
        
        # 获取画面尺寸和中心
        height, width = frame.shape[:2]
        frame_center = (width // 2, height // 2)
        
        # 检测红色污渍
        contours, center, area = detect_red_stain(frame)
        
        # 绘制辅助十字线
        draw_crosshair(frame)
        
        # 如果检测到污渍，绘制标记
        if len(contours) > 0 and center is not None:
            draw_stain_marker(frame, contours, center)
        
        # 计算偏差
        dx, dy = calculate_deviation(center, frame_center)
        
        # 状态机处理
        if current_state == STATE_NORMAL:
            # 正常检测状态
            # 如果检测到污渍，且在喷水范围内，自动触发喷水
            if center is not None and dx is not None and dy is not None:
                distance = (dx**2 + dy**2) ** 0.5  # 计算距离
                if distance <= SPRAY_RADIUS:
                    # 进入喷水状态
                    pre_spray_area = area
                    send_spray_command()
                    spray_start_time = time.time()
                    current_state = STATE_SPRAYING
                    print(f"\n→ 检测到目标在喷水范围内，自动触发喷水")
                    print(f"  污渍面积: {pre_spray_area} 像素")
        
        elif current_state == STATE_SPRAYING:
            # 正在喷水状态，等待1秒后进入评估状态
            if time.time() - spray_start_time >= 1.0:
                current_state = STATE_EVALUATING
                spray_start_time = time.time()
                print("→ 进入清洗效果评估阶段")
        
        elif current_state == STATE_EVALUATING:
            # 正在评估状态，等待1秒后计算清洗效果
            if time.time() - spray_start_time >= 1.0:
                # 计算面积变化
                if pre_spray_area > 0:
                    reduction_ratio = (pre_spray_area - area) / pre_spray_area * 100
                    print(f"→ 清洗效果评估完成")
                    print(f"  清洗前: {pre_spray_area} 像素")
                    print(f"  清洗后: {area} 像素")
                    print(f"  面积减少: {reduction_ratio:.1f}%")
                
                # 显示评估结果消息（持续2秒）
                message_text = "Evaluating..."
                message_start_time = time.time()
                message_duration = 2.0
                
                # 回到正常检测状态
                current_state = STATE_NORMAL
                pre_spray_area = 0
        
        # 更新消息显示
        update_message()
        
        # 绘制信息文字
        draw_info(frame, center, dx, dy, area, pre_spray_area)
        
        # 在窗口中显示画面
        cv2.imshow('Microscope View', frame)
        
        # 等待键盘输入（1毫秒超时）
        key = cv2.waitKey(1) & 0xFF
        
        # 处理键盘输入
        if key == ord('q'):
            # 按 q 键退出
            print("\n→ 用户按下q键，退出程序")
            break
        
        elif key == ord('s'):
            # 按 s 键手动喷水
            if current_state == STATE_NORMAL:
                contours, center, area = detect_red_stain(frame)
                pre_spray_area = area
                send_spray_command()
                spray_start_time = time.time()
                current_state = STATE_SPRAYING
                print(f"\n→ 用户手动触发喷水")
                print(f"  当前污渍面积: {pre_spray_area} 像素")
        
        elif key == ord('+') or key == ord('='):
            # 按 + 键增加灵敏度（降低饱和度下限）
            global RED_HSV_LOWER1, RED_HSV_LOWER2
            new_s = max(0, RED_HSV_LOWER1[1] - 10)
            RED_HSV_LOWER1[1] = new_s
            RED_HSV_LOWER2[1] = new_s
            print(f"\n→ 降低饱和度下限: {new_s}")
        
        elif key == ord('-') or key == ord('_'):
            # 按 - 键降低灵敏度（提高饱和度下限）
            new_s = min(255, RED_HSV_LOWER1[1] + 10)
            RED_HSV_LOWER1[1] = new_s
            RED_HSV_LOWER2[1] = new_s
            print(f"\n→ 提高饱和度下限: {new_s}")
    
    # 释放资源
    print("\n→ 释放摄像头资源...")
    cap.release()
    cv2.destroyAllWindows()
    
    # 关闭串口
    if serial_connected and serial_port is not None:
        print("→ 关闭串口连接...")
        serial_port.close()
    
    print("→ 程序已退出")

# --------------------------
# 8. 程序启动
# --------------------------

if __name__ == "__main__":
    main()

# --------------------------
# 附录：HSV阈值调节指南
# --------------------------
"""
HSV颜色空间说明:
H (Hue)    - 色调，范围0-180
             0/180: 红色
             30: 黄色
             60: 绿色
             90: 青色
             120: 蓝色
             150: 品红

S (Saturation) - 饱和度，范围0-255
                 0: 灰色（无颜色）
                 255: 最鲜艳

V (Value) - 亮度，范围0-255
            0: 黑色
            255: 最亮

红色马克笔调节建议:
1. 先固定H范围: [0,10] 和 [170,180]（这是红色的标准范围）
2. 调节S下限:
   - 如果识别太灵敏（把其他颜色也识别成红色），增大S下限
   - 如果识别不到红色，减小S下限
3. 调节V下限:
   - 如果灯光很暗，减小V下限
   - 如果环境光太强，增大V下限

调节步骤:
1. 运行程序，观察是否能识别红色污渍
2. 如果识别不到：
   - 尝试减小 RED_HSV_LOWER1[1] 和 RED_HSV_LOWER2[1]（饱和度下限）
   - 尝试减小 RED_HSV_LOWER1[2] 和 RED_HSV_LOWER2[2]（亮度下限）
3. 如果误识别：
   - 尝试增大 RED_HSV_LOWER1[1] 和 RED_HSV_LOWER2[1]（饱和度下限）
   - 尝试增大 RED_HSV_LOWER1[2] 和 RED_HSV_LOWER2[2]（亮度下限）
4. 可以使用程序中的 '+' 和 '-' 键实时调节饱和度下限

注意事项:
- 显微镜LED灯亮度会影响识别效果，建议先固定灯光亮度
- 不同品牌的红色马克笔颜色略有差异，可能需要微调
- 白色载玻片反光可能造成误识别，可以适当增大S下限
"""

# --------------------------
# 附录：常见问题排查
# --------------------------
"""
1. 摄像头打不开
   - 检查USB线是否插好，尝试更换USB接口
   - 确保没有其他程序占用摄像头（如微信、钉钉视频通话等）
   - 检查设备管理器中是否有摄像头设备
   - 尝试修改代码中的摄像头ID（0或1）
   - 安装摄像头驱动（如果是免驱摄像头则不需要）

2. 识别不到红色污渍
   - 确保红色马克笔污渍在画面中，并且清晰可见
   - 调节显微镜焦距，确保画面清晰
   - 检查HSV阈值是否合适（参考上面的调节指南）
   - 用 '+' 键降低饱和度下限，增加识别灵敏度
   - 确保摄像头画面没有严重曝光或过暗

3. 误识别（把非红色区域识别成红色）
   - 用 '-' 键提高饱和度下限，降低识别灵敏度
   - 检查是否有红色反光或其他红色物体进入画面
   - 增大形态学操作的kernel尺寸（当前是5x5）
   - 增加面积过滤阈值（当前是50像素）

4. 串口报错
   - 检查STM32是否正确连接电脑
   - 在设备管理器中查看COM口编号，修改代码中的SERIAL_PORT
   - 确保串口波特率与STM32程序一致（当前是115200）
   - 关闭其他占用该串口的程序（如串口调试助手）
   - 如果串口暂时未连接，程序会自动跳过发送，不影响其他功能

5. 喷水功能不触发
   - 确保污渍在画面中心半径20像素范围内
   - 检查串口是否连接成功（看程序启动时的提示）111
   - 手动按 's' 键测试喷水功能
   - 检查STM32端是否正确接收 '1' 字符并触发水泵

6. 清洗效果评估不准确
   - 确保喷水后污渍位置没有移动
   - 评估时画面要保持稳定
   - 如果面积变化不明显，可以调整评估阈值（当前是30%）
"""