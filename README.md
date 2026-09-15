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
cp config.example.json config.json
```

编辑 `config.json`，至少填写：

```json
{
  "visual_root": "/path/to/model-visualizations",
  "original_root": "/path/to/original-images",
  "output_root": "/path/to/review-export"
}
```

然后启动：

- Windows：双击或执行 `run_windows.bat`
- Ubuntu / macOS：执行 `bash run_unix.sh`

首次启动会在项目目录创建 `.venv` 并安装 Pillow，不会向系统 Python 安装依赖。
服务就绪后浏览器会打开本机地址；按 `Ctrl+C` 停止。

## 配置

`config.json` 相对路径以配置文件所在目录为基准。完整字段见
[`config.example.json`](config.example.json)：

- `visual_root`：模型生成的可视化图目录；
- `original_root`：对应原始图片目录；
- `label_root`：可选 YOLO 标签目录，未使用时为 `null`；
- `output_root`：审核导出目录；
- `manifest`：可选显式配对清单；
- `candidate_only`：为 `true` 时仅审核存在模型图的候选样本；
- `include_visualizations`：导出时是否保留模型可视化图；
- `allow_images_without_labels`：是否允许仅导出图片；
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
- 审核工具不编辑模型框或自动修正标签，导出的标签保持原样。

## License

[MIT](LICENSE)
