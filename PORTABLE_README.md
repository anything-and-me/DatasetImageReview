# 数据集图像审核工具（跨平台包）

这是一个不包含图片、标签、审核状态或任何密钥的本地审核工具包。解压后仅在本机
启动浏览器界面；不会上传图片，也不会移动或删除源文件。

## 前置条件

- Windows 10/11、Ubuntu 20.04+ 或 macOS 12+
- Python 3.10 或更高版本
- 首次启动需要能通过 `pip` 安装 `Pillow`；工具会创建解压目录内的 `.venv`，不会向系统
  Python 安装依赖

## 配置

1. 将 `config.example.json` 复制为 `config.json`。
2. 编辑 `config.json` 中的 `visual_root`、`original_root`、`output_root`；如有 YOLO
   标签，填写 `label_root`，否则设为 `null`。
3. Windows 路径可使用正斜杠，例如 `D:/datasets/originals`。相对路径相对于
   `config.json` 所在目录。

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
sha256sum -c dataset-image-review-portable-1.1.0.zip.sha256
```

Windows PowerShell：

```powershell
(Get-FileHash .\dataset-image-review-portable-1.1.0.zip -Algorithm SHA256).Hash
```

将 PowerShell 输出与 `.sha256` 文件中的哈希值比较。
