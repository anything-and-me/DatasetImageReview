# Dataset Image Review

本地、浏览器式图像审核工具，用于逐张对照模型可视化图与原始图，并把确认保留的
样本安全导出为数据集。适用于目标检测、分割、分类等模型的预标注审核流程。

工具不上传图片、不依赖云服务，默认仅监听 `127.0.0.1`。

## 功能

- 左右并列查看模型可视化图和原图，支持缩放、筛选与键盘快捷键；
- 按相对路径、唯一文件名 stem 或显式 CSV/JSON/JSONL/NDJSON manifest 配对；
- 明确报告缺失配对、重复 stem、缺失/错误 YOLO 标签；
- 审核状态原子持久化，重启后可恢复，并支持撤销；
- 仅复制“保留”的原图、可选 YOLO 标签和可视化图；从不移动、删除或覆盖源文件；
- 导出前按样本预检哈希，重复导出幂等，冲突文件不会被覆盖；
- 提供 Windows、Ubuntu 与 macOS 可运行的 ZIP 打包与启动脚本。

## 快速开始

要求 Python 3.10+。

```bash
git clone https://github.com/anything-and-me/DatasetImageReview.git
cd DatasetImageReview
```

然后启动：

- Windows：双击或执行 `run_windows.bat`
- Ubuntu / macOS：执行 `bash run_unix.sh`

首次启动会在项目目录创建 `.venv` 并安装 Pillow，不会向系统 Python 安装依赖。
服务就绪后浏览器会打开本机地址。首次没有 `config.json` 时，页面会提供四个
“选择文件夹”按钮，依次选择模型可视化图、原图、可选标签和导出目录，再点击
“确认文件夹并开始审核”。目录选择器是本机系统窗口，路径不会发送到云端；按
`Ctrl+C` 停止服务。

## 网页选定文件夹

网页选择模式依赖 Python 自带的 `tkinter` 打开系统目录选择器：

- Windows 和 python.org / Homebrew 的 macOS Python 通常已包含它；
- Ubuntu 如提示缺少 `tkinter`，安装与当前 Python 版本对应的 `python3-tk` 后重启；
- 无法使用图形目录选择器的无桌面环境，可继续使用下方 JSON 配置模式。

每次点击“重新选择文件夹”后，只有再次确认才会切换审核会话。切换不会移动、删除或
修改任何原始图片、标签或模型可视化图。

## JSON / 命令行配置（兼容模式）

如需使用显式配对清单、自动化启动或无图形环境，可复制
[`config.example.json`](config.example.json) 为 `config.json`。相对路径以配置文件所在
目录为基准。完整字段：

- `visual_root`、`original_root`、`output_root`：必填目录；
- `label_root`：可选 YOLO 标签目录；
- `manifest`：可选 CSV/JSON/JSONL/NDJSON 显式配对清单；
- `candidate_only`、`include_visualizations`、`allow_images_without_labels`：审核与导出选项；
- `port`：本机端口，默认 `8765`。

## 审核与导出

- 保留：`Y` 或 `Enter`
- 不保留：`N` 或 `Delete`
- 上一张 / 下一张：`←` / `→`
- 撤销：`Z`

导出结构：

```text
output_root/
├── images/
├── labels/
├── visualizations/
├── review_manifest.jsonl
└── export_summary.json
```

审核状态默认保存为 `output_root/.review_state.json`。该文件、数据与导出目录均不会
包含在本仓库或跨平台 ZIP 中。

## 生成可发送的跨平台 ZIP

```bash
python3 build_portable_package.py
```

输出位于 `dist/`，包含 ZIP 和 SHA-256 校验文件。若同名交付物内容不同，构建会拒绝
覆盖；确认替换时使用 `--force`。ZIP 内仅包含程序、文档、配置样例和启动脚本。

## 测试

```bash
python3 -m unittest discover -s . -p 'test_*.py' -v
```

测试覆盖配对、状态恢复、导出安全、跨平台配置、ZIP 内容、校验和与解压后的服务健康
检查。

## 隐私与限制

- 项目不包含示例图片、模型权重、标签、审核状态、真实路径、访问令牌或密钥；
- 不提供远程访问、认证或多用户协作；
- 网页目录选择器需要在运行审核服务的本机桌面会话中使用，不能从远程浏览器操作；
- 审核工具不编辑模型框或自动修正标签，导出的标签保持原样。

## License

[MIT](LICENSE)
