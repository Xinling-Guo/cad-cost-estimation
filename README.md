# CAD 加工成本评估：第一阶段数据探查

当前只分析 CAD 数据可用性与结构化特征，不实现 LLM Agent、自动报价、2D 转 3D 或工艺规划。原始文件仅以只读方式打开；DWG 转换使用临时副本。

## 环境与项目结构

已在 WSL Ubuntu 使用 Python 3.10.12 创建 `.venv`。VSCode Remote 中选择解释器 `${workspaceFolder}/.venv/bin/python`（打开本项目作为工作目录时）。Windows 与 WSL 的 Python 虚拟环境不能混用。

```text
cad-cost-estimation/
├── src/cad_inventory/       # 解析、扫描、报告实现
├── scripts/                # 命令入口与环境安装脚本
├── tests/                  # 临时样例与失败路径验证
├── data/raw/               # 甲方原始文件，不写入、不覆盖
├── data/processed/         # cad_inventory.json
├── reports/                # cad_data_analysis.md、安装方案
├── logs/                   # cad_inventory.log
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── README.md
```

## 安装与运行

```bash
cd /home/gxl/code/cad-cost-estimation
bash scripts/setup_env.sh
source .venv/bin/activate
python scripts/explore_cad.py
```

`setup_env.sh` 安装依赖，且仅在 `.env` 不存在时从模板创建它。`.env` 和数据默认不纳入 Git。路径配置相对于项目根目录解析，可从任意当前目录调用入口。

```bash
.venv/bin/python scripts/explore_cad.py --raw-dir data/raw \
  --output data/processed/cad_inventory.json \
  --report reports/cad_data_analysis.md
```

配置项见 `.env.example`。程序拒绝将 JSON、日志、报告写入 raw 目录或它的子目录；每次运行替换 JSON 和报告，日志追加。不要把手工导出的 DXF 覆盖到原 DWG 上。

## 支持范围与计数定义

- 递归扫描所有文件，识别扩展名与文件头，并记录类型不一致、大小和 SHA-256。符号链接记录错误，不跟随读取。
- DXF 使用 `ezdxf`，结构错误时尝试 `recover`，审计错误/修复记录在 JSON 中，内存修复不写回原图。
- DWG 使用 ODAFileConverter 转换临时副本后解析。未安装转换器时记录 `blocked`、错误类型与安装提示，特征为 `null`，不是零实体。
- STP/STEP 仅做 Part 21 文本探查：schema、实体声明类型/数量、实体声明中的实体和面数量。状态 `inspected`，非三维内核解析。复合实体、非 ASCII 格式等可能不支持，失败会记录原因。
- `entity_type_counts`、`dimension_count`、`geometry_count` 是模型空间及全部图纸空间的顶层实体计数，不展开 INSERT。
- `block_count` 是顶层 INSERT 数量；`block_definition_count` 是块定义数量，含匿名块和布局内部块。每个块另有实体统计。
- `texts` 包含布局文字、块定义文字与属性文字，保留 scope/layer/handle。定义文字不是每个插入实例的展开文字，`text_count` 不能直接解释为屏幕上文字数量。
- `dimension_count` 不包括已炸开为线条的尺寸。几何类型白名单在 JSON 的 `geometry_types` 中，不等于完整制造特征。
- 公差文字候选通过 `公差`、`±`、`%%p`、`tolerance` 搜索；不做 OCR、尺寸实测或一般公差规则推断。

JSON 包含 `schema_version`、生成时间、`summary` 和逐文件 `files`；每个文件含 `status`、`features`、`errors`。状态为 `parsed` / `partial` / `inspected` / `blocked` / `error` / `unsupported`。单文件失败不会中断批次。

命令退出 0 表示扫描及报告写入完成，**不表示所有 CAD 都解析成功**；必须查看 `summary.status_counts` 和逐文件错误。路径配置错误退出 2，批次失败退出 1。空目录会生成 0 文件报告，目录不存在报错。

## 缺失依赖方案

见 [dependency_installation.md](reports/dependency_installation.md)。Python 依赖已安装；DWG 转换器仍需安装或在 Windows 中导出 DXF。没有转换后的数据，不能判断 179 个零件的图框、公差是否完整可提取。

## 验证

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

验证覆盖实体提取（包含块内公差文字及纸空间）、原始文件内容不变、损坏文件隔离、缺失 DWG 转换器、输出目录保护及符号链接处理。

技术依据：[ezdxf DWG 转换支持](https://ezdxf.readthedocs.io/en/stable/addons/odafc.html)、[DXF 恢复读取](https://ezdxf.readthedocs.io/en/stable/drawing/recover.html)。

## 第二阶段：DXF 深度探查

```bash
.venv/bin/python scripts/analyze_dxf.py
```

入口只扫描 `data/raw` 下的 DXF，不尝试再次转换 DWG，也不调用 LLM。结果分别写入：

- `data/processed/dxf_inventory.json`：文件结构、指定实体统计、实例文字及块定义文字、WCS 坐标、属性、表格/尺寸显示块、尺寸样式及覆盖、公差/精度、图框与多阈值空间聚类。
- `reports/dxf_deep_analysis.md`：工程信息可用性、公差风险、179 张图识别证据与下一步建议。
- `reports/dxf_frame_map.svg`：大图模型空间的图框边界示意，悬停显示图号和页码。
- `data/processed/dxf_deep_analysis.log`：逐文件运行日志。

原始 DXF 在成功或失败后均复核 SHA-256。单文件异常记录后继续扫描其余文件；有文件错误时命令退出 1。`partial` 表示审计或提取存在问题；`parsed` 也不表示公差语义、字体、比例或成本已经验证。

本阶段 `block_count` 是块定义数，`insert_count` 是顶层块引用数。实例文字展开嵌套块，附属 ATTRIB 及 ACAD_TABLE/DIMENSION 的已有图面显示块；定义 ATTDEF 默认值另存。它不执行 OCR，不猜测特殊字体符号，不重新生成尺寸图形。坐标以来源 scope 和 block_path 定位，图框归属基于插入点。完整数值搜索区分 0.002 和 0.0025。

## 历史工艺、工时与 CAD 关联分析

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/analyze_manufacturing.py
```

旧版 `.xls` 使用 `xlrd`，分类 `.xlsx` 使用 `openpyxl`，说明 `.pptx` 使用 `python-pptx`；依赖仅安装于本项目 `.venv`。全部分析本地完成，不调用外部大模型，不训练模型。

输入为 `doc/机加图档工艺工时说明.pptx`、`data/raw/manufacturing/` 下的三张工艺表与已有/新增 CAD。新增18组与旧6组按文件哈希及图号证据去重；不同格式和多页不增加主体件数。保留原工作表、行号、字段原文、存储类型、图号/属性坐标及关联证据。连续相同工序仍为独立发生，不作为重复数据删除。

结果为三份报告：

- `reports/manufacturing_data_analysis.md`
- `reports/cad_process_matching_report.md`
- `reports/agent_feasibility_update.md`

结构化结果及运行日志保存在 `data/processed/manufacturing/`。其中 `source_integrity.json` 复核 `doc`、`data/raw` 的所有原始文件，`cad_process_matches.json` 区分图号严格匹配、框内零件明细证据及待确认星号别名；`SMX0100-F20A_dependency.json` 区分主体顺序、电极支线、放电归属和逐来源行台账。

新增PPT明确了模具类型：CMY1000A为冲压模具，SMX0100为注塑模具，两者独立统计。表头“工时定额”尚未证明属于实际工时；全表单位、批量、费率、电极成本归属和图纸/工艺版本仍需确认。当前结果用于判断后续受限原型的可行性，不形成自动报价。
