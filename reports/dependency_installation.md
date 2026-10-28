# 依赖检查与安装方案

检查环境：WSL Ubuntu，Python 3.10.12，pip 可用，venv 创建成功。检查时未安装 ezdxf，也未找到 ODAFileConverter / dwgread。

## 已完成的 Python 安装

项目 `.venv` 中已安装 `requirements.txt` 和 `requirements-dev.txt`。业务依赖为 ezdxf 1.4.4、python-dotenv 1.1.1；pytest 仅用于验证。

重建命令：

```bash
cd /home/gxl/code/cad-cost-estimation
bash scripts/setup_env.sh
.venv/bin/python -m pip install -r requirements-dev.txt
```

若新机器缺少 venv：`sudo apt-get update` 后执行 `sudo apt-get install python3-venv python3-pip`，再运行安装脚本。当前机器无需该操作。

## DWG 转换器：尚未安装

Python pip 依赖不会安装 DWG 转换器。可选择以下路线。

### 路线 A：WSL 内安装 Linux ODAFileConverter

从 [ODA 官方下载页](https://www.opendesign.com/guestfiles/oda_file_converter) 获取适用的 Linux 包或 AppImage，按该包要求安装。AppImage 示例（替换实际文件名）：

```bash
mkdir -p "$HOME/Apps"
# 将下载的 AppImage 放入此目录
chmod +x "$HOME/Apps/ODAFileConverter.AppImage"
```

在 `.env` 中设置实际绝对路径：

```dotenv
ODA_FILE_CONVERTER=/home/gxl/Apps/ODAFileConverter.AppImage
```

WSL 中若无可用图形显示，按官方说明可安装 `xvfb`：`sudo apt-get install xvfb`。AppImage 运行时依赖以实际下载版本为准；本项目尚未验证外部转换器环境。程序只将临时 DWG 副本交给转换器。

### 路线 B：Windows 上导出 DXF

在 Windows CAD 软件或 Windows ODAFileConverter 中，将 DWG 副本导出为 DXF，推荐 R2010 或更新版本保留 Unicode 文字。导出到独立目录，例如项目 `data/converted`，保留原始 DWG。

```bash
.venv/bin/python scripts/explore_cad.py --raw-dir data/converted \
  --output data/processed/converted_inventory.json \
  --report reports/converted_analysis.md
```

此扫描只分析导出的目录，不与原始目录报告合并。请在转换后抽样核对中文图框、字体、块属性、公差和外部参照。WSL 配置项指向 Linux 程序；不要直接填 Windows `.exe` 路径期待自动处理 Windows/WSL 路径转换。

STP 初探无需额外依赖；体积、拓扑与加工特征分析需后续另行引入三维 CAD 内核。

依据：[ezdxf ODAFileConverter 官方文档](https://ezdxf.readthedocs.io/en/stable/addons/odafc.html)。
