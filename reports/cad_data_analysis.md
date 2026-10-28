# CAD 数据探查报告

生成时间（UTC）：2026-10-14T12:51:28.003853+00:00

文件总数：13；类型统计：{"dwg": 7, "step": 6}。
处理状态：{"blocked": 7, "inspected": 6}。

| 文件 | 类型 | 状态 | 结果/阻塞原因 |
| --- | --- | --- | --- |
| CMY1000A-图档179张.dwg | dwg | blocked | 未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。 |
| smx0100-m26a-a/smx0100-m22a-a.dwg | dwg | blocked | 未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。 |
| smx0100-m26a-a/smx0100-m22a-a.stp | step | inspected | STEP 实体声明 506；MANIFOLD_SOLID_BREP 1，ADVANCED_FACE 12；未验证三维几何 |
| smx0100-m26a-a/smx0100-m22b-a.dwg | dwg | blocked | 未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。 |
| smx0100-m26a-a/smx0100-m22b-a.stp | step | inspected | STEP 实体声明 676；MANIFOLD_SOLID_BREP 1，ADVANCED_FACE 16；未验证三维几何 |
| smx0100-m26a-a/smx0100-m24a-a.dwg | dwg | blocked | 未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。 |
| smx0100-m26a-a/smx0100-m24a-a.stp | step | inspected | STEP 实体声明 802；MANIFOLD_SOLID_BREP 1，ADVANCED_FACE 18；未验证三维几何 |
| smx0100-m26a-a/smx0100-m24b-a.dwg | dwg | blocked | 未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。 |
| smx0100-m26a-a/smx0100-m24b-a.stp | step | inspected | STEP 实体声明 1338；MANIFOLD_SOLID_BREP 1，ADVANCED_FACE 30；未验证三维几何 |
| smx0100-m26a-a/smx0100-m25a-a.dwg | dwg | blocked | 未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。 |
| smx0100-m26a-a/smx0100-m25a-a.stp | step | inspected | STEP 实体声明 1164；MANIFOLD_SOLID_BREP 1，ADVANCED_FACE 28；未验证三维几何 |
| smx0100-m26a-a/smx0100-m26a-a.dwg | dwg | blocked | 未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。 |
| smx0100-m26a-a/smx0100-m26a-a.stp | step | inspected | STEP 实体声明 1276；MANIFOLD_SOLID_BREP 1，ADVANCED_FACE 30；未验证三维几何 |

## 数据可用性判断

DWG：仅识别文件头不能证明图纸可解析。状态为 blocked 时尚未提取文字、公差或几何，需要转换器或甲方导出的 DXF。
DXF：parsed 表示可提取 CAD 实体；仍需检查图框一般公差、块内文字、外部参照和字体。partial 表示存在未修复审计错误。
STEP：inspected 仅表示可识别 Part 21 格式与实体声明，不能证明三维模型有效或可用于加工特征识别。
甲方说明大图包含 179 个零件；该数量尚未通过 CAD 分区验证，文件数量也不等于零件数量。

## 下一步

1. 安装 DWG 转换器或在 Windows CAD 软件中导出 DXF，重新运行探查。
2. 抽样核对图框文字与一般公差适用规则，尤其是 ±0.002 与 ±0.01 的变更。
3. 对多零件大图划分图框与零件区域；按文件名配对的 DWG/STP 仍需人工验证对应关系。
4. 在独立阶段引入三维 CAD 内核检查 STEP 拓扑、尺寸、体积和加工特征，再评估成本估算所需字段。

本阶段不实现 LLM Agent、2D 转 3D、工艺规划或加工报价。
