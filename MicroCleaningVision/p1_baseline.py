import cv2
import numpy as np
import os
import glob

img_dir = "./data/raw_images/public/"
img_paths = sorted(glob.glob(os.path.join(img_dir, "*.jpg")))


def detect_stains(v, H, W, kernel):
    """Otsu主检测 + 自适应阈值兜底"""
    EDGE_MARGIN = 3
    MIN_AREA = 15
    MAX_AREA_RATIO = 0.05
    max_area = H * W * MAX_AREA_RATIO

    def filter_contours(mask, min_area=MIN_AREA):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        results = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            if x < EDGE_MARGIN or y < EDGE_MARGIN or x + w > W - EDGE_MARGIN or y + h > H - EDGE_MARGIN:
                continue
            results.append((x, y, w, h, round(area, 1)))
        return results

    # --- 主方法：高斯模糊 + Otsu + 形态学 ---
    v_blur = cv2.GaussianBlur(v, (5, 5), 0)
    _, mask = cv2.threshold(v_blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    stains = filter_contours(mask)

    # --- 兜底方法：Otsu检出0时，切换自适应阈值 ---
    if len(stains) == 0:
        mask2 = cv2.adaptiveThreshold(
            v, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 5)
        mask2 = cv2.morphologyEx(mask2, cv2.MORPH_OPEN, kernel, iterations=1)
        mask2 = cv2.morphologyEx(mask2, cv2.MORPH_CLOSE, kernel, iterations=1)
        stains = filter_contours(mask2, min_area=50)

    return stains


if not img_paths:
    print("未找到图片，检查运行目录！")
else:
    os.makedirs("./results", exist_ok=True)
    os.makedirs("./results/report/", exist_ok=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    for img_path in img_paths:
        img_name = os.path.basename(img_path)
        img = cv2.imread(img_path)
        if img is None:
            print(f"[{img_name}] 图片读取失败")
            continue

        H, W = img.shape[:2]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]

        stains = detect_stains(v, H, W, kernel)

        out_img = img.copy()
        for x, y, w, h, area in stains:
            cv2.rectangle(out_img, (x, y), (x + w, y + h), (0, 0, 255), 2)

        print(f"[{img_name}] 检测到污渍数量：{len(stains)}")
        for s in stains:
            print(f"  x={s[0]} y={s[1]} w={s[2]} h={s[3]} area={s[4]}")

        # 保存检测结果
        out_path = f"./results/result_{img_name}"
        cv2.imwrite(out_path, out_img)

        # 生成原图+检测并排对比
        target_w = 400
        scale = target_w / W
        target_h = int(H * scale)
        orig_r = cv2.resize(img, (target_w, target_h))
        result_r = cv2.resize(out_img, (target_w, target_h))
        cv2.putText(orig_r, "Original", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(result_r, "Detected", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        gap = np.full((target_h, 10, 3), 255, dtype=np.uint8)
        combined = np.hstack([orig_r, gap, result_r])
        cv2.imwrite(f"./results/report/compare_{img_name}", combined)

        print(f"  输出: {out_path} + 对比图")
def detect_stain_from_path(image_path: str):
    """
    【Vision v0.1 接口，严格对齐文档，给成员C调用】
    输入：图片路径字符串
    返回：和文档完全一致的字典
    """
    img = cv2.imread(image_path)
    if img is None:
        return {
            "污染存在": False,
            "污染区域": [],
            "中心": None
        }

    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    stains = detect_stains(v, W, H, kernel)

    if len(stains) == 0:
        return {
            "污染存在": False,
            "污染区域": [],
            "中心": None
        }

    # 取第一个污渍，对齐文档输出
    x, y, w, h, area = stains[0]
    cx = int(x + w / 2)
    cy = int(y + h / 2)

    bbox_list = []
    for item in stains:
        ix, iy, iw, ih, ia = item
        bbox_list.append((ix, iy, iw, ih))

    return {
        "污染存在": True,
        "污染区域": bbox_list,
        "中心": (cx, cy)
    }


# --------自测代码，用来验证接口是否正常工作--------
if __name__ == "__main__":
    test_img = r"D:\视觉成像\3d-1\3d\MicroCleaningVision\data\raw_images\public\public_003.jpg"
    test_result = detect_stain_from_path(test_img)
    print("接口输出结果：")
    print(test_result)

