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

1. 选择模型可视化图文件夹；
2. 选择对应原图文件夹；
3. 可选选择 YOLO 标签文件夹；
4. 选择已有的导出父目录；
5. 点击“确认文件夹并开始审核”。

目录选择器只在本机显示，路径和文件不会上传。它依赖 Python 的 `tkinter`：
Windows 与多数 macOS Python 通常已包含；Ubuntu 如提示缺少，请安装与当前 Python
对应的 `python3-tk`。无桌面或没有 `tkinter` 的环境可使用下面的兼容配置模式。

## JSON 配置（兼容模式）

将 `config.example.json` 复制为 `config.json`，填写 `visual_root`、`original_root`、
`output_root`，如有 YOLO 标签则填写 `label_root`。Windows 路径可使用正斜杠，例如
`D:/datasets/originals`；相对路径相对于 `config.json`。

`candidate_only: true` 表示只审核有模型可视化图的候选样本；设为 `false` 时，原图
缺少可视化图会显示为配对异常。工具固定监听 `127.0.0.1`，不对局域网开放。

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

## 校验 ZIP

压缩包同目录提供 `.sha256` 文件。

Ubuntu / macOS：

```bash
cd /path/to/the/zip-directory
sha256sum -c dataset-image-review-portable-1.2.0.zip.sha256
```

Windows PowerShell：

```powershell
(Get-FileHash .\dataset-image-review-portable-1.1.0.zip -Algorithm SHA256).Hash
```

将 PowerShell 输出与 `.sha256` 文件中的哈希值比较。
