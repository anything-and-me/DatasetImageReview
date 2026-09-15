# Dataset Image Review

本地、浏览器式图像审核工具，用于审核现场原图、可选对照模型预标注，并把确认保留的
样本安全导出为待标注或已预标注的数据集。当前原生标签处理和多格式导出仅支持
YOLO 目标检测矩形框。

工具不上传图片、不依赖云服务，默认仅监听 `127.0.0.1`。

## 功能

- 原图是唯一必填输入；模型可视化图和 YOLO 预标注均可选，缺失预标注不阻断审核或导出；
- 项目工作台：按公司、项目和现场数据一级分类管理待处理数据，分类状态可恢复；
- 左右并列查看模型可视化图（如提供）和原图，支持缩放、筛选与键盘快捷键；
- 按相对路径、唯一文件名 stem 或显式 CSV/JSON/JSONL/NDJSON manifest 配对；
- 明确报告缺失配对、重复 stem、缺失/错误 YOLO 标签；
- 审核状态原子持久化，重启后可恢复，并支持撤销；
- 仅复制“保留”的原图、可选 YOLO 标签和可视化图；从不移动、删除或覆盖源文件；
- 导出前按样本预检哈希，重复导出幂等，冲突文件不会被覆盖；
- 标注前质量预检：报告损坏图、小图、极暗/极亮、低对比、低细节及精确/近似重复候选；
- 一次导出 YOLO、COCO、CVAT（YOLO 导入结构）和 Label Studio 交付物；
- 自动生成可编辑的 `annotation_guide/`，包含类别表、标注规则、验收清单和任务 manifest；
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
“选择文件夹”按钮，选择必填的原图、导出目录，以及可选的模型可视化图和标签目录，再点击
“确认文件夹并开始审核”。目录选择器是本机系统窗口，路径不会发送到云端；按
`Ctrl+C` 停止服务。

## 项目工作台：管理现场待处理数据

如果现场硬盘采用“一级目录表示分类”的结构，例如：

```text
/data/现场项目/
├── 原始数据/
├── 分类A/
├── 分类B/
└── 分类C/
```

启动网页后，在“项目工作台”中选择：

1. 数据根目录：例如 `/data/现场项目`；
2. 公司：例如 `示例公司`；
3. 项目：例如 `现场数据项目`；
4. 点击“扫描项目分类”；
5. 点击某个分类卡片进入审核。

工具只读取分类目录并统计图片，不移动或改写原始数据。每个分类会独立保存审核状态、
预检报告和导出结果，并显示“待处理 / 审核中 / 已导出 / 导出过期”状态。审核结果变化后，
项目清单会立即刷新；如果审核状态已经不同于上次导出，界面会显示“导出过期”。默认工作区放在数据根目录
同级的 `现场项目__dataset_review_projects/`；也可以手动指定一个不在原始数据目录内的工作区。
项目清单保存在：

```text
<workspace>/<公司>/<项目>/project_manifest.json
```

当前分类识别规则是：数据根目录下的一级子目录，只要递归找到支持的图片，就作为一个分类；
没有图片的说明目录会被忽略。大目录统计有上限，界面中的 `数量+` 表示实际数量可能更多。

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

- `original_root`、`output_root`：必填目录；原图目录必须至少有一张 `.jpg`、`.jpeg` 或 `.png`；
- `visual_root`：可选模型可视化图目录；
- `label_root`：可选 YOLO 检测框标签目录；
- `manifest`：可选 CSV/JSON/JSONL/NDJSON 显式配对清单；
- `candidate_only`、`include_visualizations`、`allow_images_without_labels`：审核与导出选项；
- `class_names`：可选类别名称数组，顺序必须与 YOLO 标签的 class ID 对应；缺省时自动使用
  `class_0`、`class_1` 等占位名称；
- `port`：本机端口，默认 `8765`。

## 标注前预检、审核与导出

- 保留：`Y` 或 `Enter`
- 不保留：`N` 或 `Delete`
- 上一张 / 下一张：`←` / `→`
- 撤销：`Z`

审核完成后，先点击“质量预检”。它顺序读取每张图片，不会把所有图片一次性装入内存，
并将 `preflight_report.json` 写入导出目录。预警只用于筛选和人工决策，不会自动删除、
移动或拒绝样本。可点击“拒绝重复候选”将非代表样本批量标为不保留；这只写审核状态，
可逐条撤销。

随后勾选所需格式并点击“导出标注包”：

- **YOLO**：`images/`、`labels/`、`data.yaml` 与可选 `visualizations/`；
- **COCO**：`coco/images/` 与 `coco/annotations.json`；
- **CVAT**：`cvat/images/`、`cvat/labels/`、`cvat/data.yaml`，可用 CVAT 的 YOLO/Ultralytics
  导入功能导入；
- **Label Studio**：`label_studio/images/`、`tasks.json` 和 `label_config.xml`。导入任务时需在
  Label Studio 配置本地/文件存储，使任务中的 `images/...` 相对路径可访问。

无论选择什么格式，都会生成 `annotation_guide/`。请在分发前检查其中的
`classes.txt` 和 `README.md`，补充项目特有的类别定义、正反例和边界情况。

各次导出应使用单独的版本化输出目录。工具会拒绝覆盖内容不同的媒体、清单、平台元数据
和规范包文件；同一审核状态下重复导出则可复用完全相同的文件。

典型导出结构：

```text
output_root/
├── images/
├── labels/
├── visualizations/
├── data.yaml
├── preflight_report.json
├── coco/
│   ├── images/
│   └── annotations.json
├── cvat/
│   ├── images/
│   ├── labels/
│   └── data.yaml
├── label_studio/
│   ├── images/
│   ├── tasks.json
│   └── label_config.xml
├── annotation_guide/
│   ├── README.md
│   ├── classes.txt
│   └── task_manifest.jsonl
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

测试覆盖配对、状态恢复、质量/重复预检、多格式导出和标注规范包、导出安全、跨平台
配置、ZIP 内容、校验和与解压后的服务健康检查。

## 隐私与限制

- 项目不包含示例图片、模型权重、标签、审核状态、真实路径、访问令牌或密钥；
- 不提供远程访问、认证或多用户协作；
- 网页目录选择器需要在运行审核服务的本机桌面会话中使用，不能从远程浏览器操作；
- 审核工具不编辑模型框或自动修正标签；越界、空尺寸、分割多边形或分类标签会被标为不支持，
  不会被伪装成检测框导出。

## License

[MIT](LICENSE)
