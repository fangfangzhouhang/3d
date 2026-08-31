# 人工污染标注：5 分钟上手

这里保存成员 A 用 Labelme 亲手确认的污染边界。人工标注是后续评价视觉算法的参照答案，不能由算法自动生成。

1. 在项目虚拟环境安装：`.\.venv\Scripts\python.exe -m pip install labelme`；启动：`& ".\.venv\Scripts\labelme.exe"`。
2. 点击 **Open Dir**，选择原图目录，例如 `data/raw_images/public/`。
3. 不规则区域使用 **Create Polygons**；近似圆形小斑点可以使用 **Create Circle**。当前转换工具支持这两种形状。
4. 标签统一填写 `contamination`。当前阶段不要增加其他标签。
5. JSON 与原图使用相同主文件名，例如 `public_001.jpg` 对应 `public_001.json`。
6. JSON 保存到 `data/annotations/labelme/`，不要覆盖原图。
7. 全部标注完成后，一条命令自动读取每份 JSON 的 `imagePath` 并批量转换：

   ```powershell
   .\.venv\Scripts\python.exe -m microcleaning.data_learning.annotation_tools --batch
   ```

批量报告保存到 `output/data_learning/annotation_conversion_report.json`。输出 Mask 中白色（255）是污染，黑色（0）是背景，尺寸必须与原图完全一致。没有污染的图片可以保留空的 `shapes` 列表，转换后会得到全黑 Mask。

程序转换成功只证明格式正确，不证明边界正确。请肉眼复核 Mask 后，再把 `metadata.csv` 中对应图片改为 `labeled`。
