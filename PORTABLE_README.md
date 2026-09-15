# 数据集图像审核工具（跨平台包）

这是一个不包含图片、标签、审核状态或任何密钥的本地审核工具包。解压后仅在本机
启动浏览器界面；不会上传图片，也不会移动或删除源文件。

## 前置条件

- Windows 10/11、Ubuntu 20.04+ 或 macOS 12+
- Python 3.10 或更高版本
- 首次启动需要能通过 `pip` 安装 `Pillow`；工具会创建解压目录内的 `.venv`，不会向系统
  Python 安装依赖

## 网页选定文件夹

默认不需要创建 `config.json`。启动后浏览器会打开“选择本机文件夹”页，点击每个
“选择文件夹”按钮会弹出当前电脑的系统目录选择器：

1. 选择对应原图文件夹（必填，至少含一张 `.jpg`、`.jpeg` 或 `.png`）；
2. 可选选择模型可视化图文件夹；
3. 可选选择 YOLO 检测框标签文件夹；
4. 选择已有的导出父目录；
5. 点击“确认文件夹并开始审核”。

目录选择器只在本机显示，路径和文件不会上传。它依赖 Python 的 `tkinter`：
Windows 与多数 macOS Python 通常已包含；Ubuntu 如提示缺少，请安装与当前 Python
对应的 `python3-tk`。无桌面或没有 `tkinter` 的环境可使用下面的兼容配置模式。

## JSON 配置（兼容模式）

将 `config.example.json` 复制为 `config.json`，填写必填的 `original_root`、`output_root`；
如有模型图或 YOLO 标签再填写 `visual_root`、`label_root`。Windows 路径可使用正斜杠，例如
`D:/datasets/originals`；相对路径相对于 `config.json`。

`candidate_only: true` 表示只审核有模型可视化图的候选样本；设为 `false` 时，原图
缺少可视化图会显示为配对异常。工具固定监听 `127.0.0.1`，不对局域网开放。

`class_names` 可选，填写类别名称数组且顺序必须与 YOLO class ID 一致。例如：

```json
{
  "class_names": ["bottle", "cup", "other_waste"]
}
```

未填写时，交付规范包会生成 `class_0`、`class_1` 等占位类别，请在发给标注团队前补全。
当前标签导出仅支持 YOLO 目标检测矩形框；不支持分割多边形或分类标签。

## 启动

### Windows

双击 `run_windows.bat`，或在命令提示符运行：

```bat
run_windows.bat
```

### Ubuntu / macOS

在解压目录运行：

```bash
bash run_unix.sh
```

如需指定 Python：

```bash
PYTHON_BIN=python3.11 bash run_unix.sh
```

首次运行会在包内 `.venv` 安装 `requirements.txt` 中的 Pillow，并自动打开本机浏览器。
按 `Ctrl+C` 停止服务。

## 标注前交付

审核完成后先点击“质量预检”。报告会检查损坏图、小尺寸、亮度/对比/细节异常和
精确/近似重复候选，并写入 `preflight_report.json`；预警不会自动删除文件。点击
“拒绝重复候选”会把非代表样本批量标为不保留，且仅修改可撤销的审核状态。

导出时可选择 YOLO、COCO、CVAT 和 Label Studio。每次导出都会额外生成
`annotation_guide/`，其中含类别表、标注规则、验收清单和任务 manifest。CVAT 使用
YOLO/Ultralytics 导入目录；Label Studio 需把其本地文件存储映射到任务中的
`images/...` 相对路径。

## 校验 ZIP

压缩包同目录提供 `.sha256` 文件。

Ubuntu / macOS：

```bash
cd /path/to/the/zip-directory
sha256sum -c dataset-image-review-portable-1.3.0.zip.sha256
```

Windows PowerShell：

```powershell
(Get-FileHash .\dataset-image-review-portable-1.3.0.zip -Algorithm SHA256).Hash
```

将 PowerShell 输出与 `.sha256` 文件中的哈希值比较。
