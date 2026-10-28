"""DXF-only read-only batch analysis and report entry point."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time

import ezdxf
from .deep_analysis import analyze_document, ENTITY_TYPES, KEYWORDS, TOKENS
from .deep_spatial import frame_svg
from .parsers import read_dxf
from .pipeline import ROOT, atomic_write, project_path, sha256

LOGGER = logging.getLogger(__name__)


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


def table(headers, rows):
    return ['| ' + ' | '.join(map(cell, headers)) + ' |',
            '| ' + ' | '.join('---' for _ in headers) + ' |'] + [
            '| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]


def examples(file, limit=8):
    texts = file['analysis']['texts']
    selected = []
    seen = set()
    for text in texts:
        if text['keyword_matches'] or any(k in text['tolerance_search_matches'] for k in ['±', 'H7', 'h7', '0.01', '0.002']):
            signature = text['plain_text']
            if signature not in seen:
                seen.add(signature)
                selected.append([text['id'], text['scope'], text['plain_text'][:220], text['layer'], text['position']])
    # Place material/hardness and technical notes before repeated template tolerances.
    selected.sort(key=lambda row: not any(k in row[2].upper() for k in ['SKD', 'HRC', '技术要求', '公差', '材料']))
    return selected[:limit]


def report_text(inventory):
    parsed = [f for f in inventory['files'] if f.get('analysis')]
    lines = ['# DXF 深度数据探查报告', '', f"生成时间（UTC）：{inventory['generated_at']}", '',
             '分析方式：ezdxf 结构化读取、已有显示块展开、规则搜索、矩形边界检测及 DBSCAN 空间聚类；未调用 LLM、未开发 Agent。', '',
             '## 1. 数据概览', '',
             f"扫描 DXF：{inventory['summary']['total_files']} 个；处理状态：{inventory['summary']['status_counts']}。", '',
             '实体总数为模型空间与纸空间的顶层实体合计，不包含属性附属实体和块内部实体；块数为块定义数，含匿名尺寸/表格/布局块。INSERT 数另外列出，不能把块定义数解释为零件数。', '']
    lines += table(['文件', '字节', 'DXF', '顶层实体', '图层', '布局', '块定义', 'INSERT'], [
        [f['path'], f['size_bytes'], f['analysis']['file_information']['dxf_version'],
         f['analysis']['file_information']['entity_total'], f['analysis']['file_information']['layer_count'],
         f['analysis']['file_information']['layout_count'], f['analysis']['file_information']['block_count'],
         f['analysis']['file_information']['insert_count']] for f in parsed])
    lines += ['', '### 实体统计', '']
    lines += table(['文件'] + ENTITY_TYPES, [[f['path']] + [f['analysis']['entity_type_counts'][k] for k in ENTITY_TYPES] for f in parsed])
    lines += ['', '## 2. DXF 结构质量判断', '']
    lines += table(['文件', '读取器', '审计错误/修复', '提取警告', '外参块', '原始哈希不变'], [
        [f['path'], f['parser']['reader'], f"{len(f['parser']['audit']['errors'])}/{len(f['parser']['audit']['fixes'])}",
         f['analysis']['warning_counts'], len(f['analysis']['external_reference_blocks']), f['source_unchanged']] for f in parsed])
    lines += ['', '可读且能提取实体只证明结构可用。审计修复发生在内存中，不写回原图；字体符号、显示块、尺寸与绘图尺度仍需人工图面核对。本次未取得 DWG 侧的同字段基准，不能证明转换前后信息完全一致。', '']
    for f in inventory['files']:
        if f['errors']:
            lines.append(f"- `{f['path']}`：{f['errors']}")
    lines += ['', '## 3. 是否保留了工程信息', '',
              '布局文字、块实例文字、附属 ATTRIB、ACAD_TABLE 显示块和 DIMENSION 显示文字均纳入实例提取。未引用块定义及 ATTDEF 默认值另存，不当作实际材料/硬度。MTEXT 保留控制码原文，同时输出 plain_text、图层、WCS 坐标及来源 handle。', '']
    lines += table(['文件', '实例文字', '属性文字', '表格显示文字', '尺寸显示文字', '特殊字体候选'], [
        [f['path'], f['analysis']['text_summary']['instance_text_count'],
         f['analysis']['text_summary']['instance_scope_counts'].get('block_attribute', 0),
         f['analysis']['text_summary']['instance_scope_counts'].get('table_graphic', 0),
         f['analysis']['text_summary']['instance_scope_counts'].get('dimension_graphic', 0),
         f['analysis']['text_summary']['special_font_text_count']] for f in parsed])
    lines += ['', '### 工程关键词命中', '', '搜索结果是文字候选，标签命中不证明字段值完整；材料和热处理值需结合相邻字段/属性标签归属。Ra 使用独立词或后接数值规则，避免误命中 DRAW 等普通字符串。', '']
    lines += table(['文件'] + KEYWORDS, [[f['path']] + [f['analysis']['text_summary']['keyword_match_counts'][k] for k in KEYWORDS] for f in parsed])
    lines += ['', '材料/硬度与制造要求的实际提取结果：', '']
    for f in parsed:
        materials = sorted({t['plain_text'].strip() for t in f['analysis']['texts'] if 'SKD' in t['keyword_matches']})
        hardness = sorted({t['plain_text'].strip() for t in f['analysis']['texts'] if 'HRC' in t['keyword_matches']})
        attrs = f['analysis']['attribute_value_counts']
        lines.append(f"- `{f['path']}`：SKD 候选 {materials}；HRC 候选 {hardness}。")
        if 'MATL' in attrs:
            lines.append(f"  材料属性 MATL 分布：{attrs['MATL']}；热处理/硬度属性 FINISH 分布：{attrs.get('FINISH', {})}。")
    lines += ['', '小图中的中文“材料”关键词为零，但 MATERIAL 表格里的实际材料值仍可提取；关键词未命中不能当作字段缺失。下列证据及 JSON 保留其图层、坐标和显示块来源。', '']
    for f in parsed:
        lines += ['', f"### 工程文字证据：{f['path']}", '']
        lines += table(['ID', '来源', '文字（节选）', '图层', '坐标'], examples(f))
    lines += ['', '## 4. 公差提取可行性', '',
              '公差同时来自尺寸样式/单实体覆盖值、尺寸已有显示文字、图框一般公差和技术说明。本次按这些来源分别保存，未自动推断适用范围。DIMTOL/DIMLIM 关闭时，DIMTP/DIMTM 中保存的数值不当作已应用公差。', '']
    lines += table(['文件', '尺寸实例', '激活公差/极限', '缓存值≠缩放几何值', '纯数值覆盖≠缩放几何值', 'DIMDEC 分布'], [
        [f['path'], f['analysis']['dimension_summary']['instance_dimension_count'],
         f['analysis']['dimension_summary']['active_tolerance_count'],
         f['analysis']['dimension_summary']['cached_vs_geometry_mismatch_count'],
         f['analysis']['dimension_summary']['numeric_override_vs_geometry_mismatch_count'],
         f['analysis']['dimension_summary']['precision_counts']] for f in parsed])
    lines += ['', '数值差异是核对候选，可能来自编辑覆盖、缓存、视图尺度和绘图约定，不直接判为 CAD 错误。尺寸数值分为几何 measurement、DIMLFAC 后的值、缓存 actual_measurement、明确文本覆盖及已有显示块文本。角度和坐标尺寸另外保留类型/单位，不混为长度。DIMDEC 是样式精度，不能替代显示原文中的小数位。', '',
              '不能把“无激活 DIMTOL”解释为“无公差”，还必须检查图框及技术要求中的规则。', '']
    for f in parsed:
        summary = f['analysis']['dimension_summary']
        if f['analysis']['drawing_structure']['frame_count'] and summary['active_tolerance_count'] == 0:
            lines += [f"`{f['path']}` 的 {summary['instance_dimension_count']} 个尺寸公差/极限开关均未激活，但图框公差文字仍可提取，需结合图框恢复一般公差。", '']
    notes = [f['path'] for f in parsed if any('成型高度公差' in t['plain_text'] for t in f['analysis']['texts'])]
    if notes:
        lines += [f"以下文件技术说明出现成型高度 ±0.005、其余 ±0.01：{notes}。这些规则与图框规则的组合/优先级仍需客户确认。", '']
    lines += ['### 尺寸证据样例', '']
    dimension_examples = []
    for f in parsed:
        dims = f['analysis']['dimensions']
        selected = next((d for d in dims if d['numeric_override_vs_geometry_mismatch']), None)
        selected = selected or next((d for d in dims if d['tolerance']['active']), None)
        if selected:
            dimension_examples.append([f['path'], selected['source_handle'], selected['dimension_type'],
                                       selected['annotation_override_raw'], [t['plain_text'] for t in selected['annotation_visible_texts']],
                                       selected['scaled_geometric_value'], selected['cached_actual_measurement'],
                                       selected['tolerance']['upper_magnitude'], selected['tolerance']['lower_deviation'], selected['precision']['linear']])
    lines += table(['文件', 'handle', '类型', '覆盖原文', '已有显示文字', '缩放几何值', '缓存值', '上偏差', '下偏差', '精度'], dimension_examples)
    lines += ['',
              '### 所有实例文字中的指定符号/数值', '',
              '`+`、`-` 也会命中图号、页码或普通连接符，不等于公差。H7 与 h7 区分大小写；0.01/0.002 按完整数值边界搜索，不把 0.0025 算作 0.002。', '']
    lines += table(['文件'] + TOKENS, [[f['path']] + [f['analysis']['text_summary']['tolerance_token_counts'][k] for k in TOKENS] for f in parsed])
    lines += ['', '### DIMENSION 覆盖/显示文字中的指定符号/数值', '']
    lines += table(['文件'] + TOKENS, [[f['path']] + [f['analysis']['dimension_summary']['search_token_counts'][k] for k in TOKENS] for f in parsed])
    lines += ['', '特殊字体注意：部分 MTEXT 使用 gdt 字体，去格式后出现反引号/波浪号等字符。本工具保留原文并标记 glyph_decoding_required，不把这些字符擅自映射为 ± 或角度符号。因此 ± 搜索计数可能少于图面显示数量。需结合字体映射、图面和人工确认恢复语义。', '']
    lines += ['## 5. 179 张图识别结果', '']
    large = next((f for f in parsed if Path(f['path']).name == 'CMY1000A-图档179张.dxf'), None)
    if large:
        analysis, structure = large['analysis'], large['analysis']['drawing_structure']
        lines += table(['layout', '实体数'], [[l['name'], l['entity_count']] for l in analysis['layouts']])
        lines += ['', f"结构判断：**{structure['classification']} 类**。检测到 {structure['frame_count']} 个带身份字段的矩形图框实例，{structure['unique_drawing_number_count']} 个不同非空图号。", '',
                  '图框检测依据为块中的完整轴对齐矩形边界及图号/标题/页码属性，实例边界通过 INSERT 矩阵转换到模型空间；不是仅按块名猜测。', '']
        lines += table(['图框块', '矩形方法', '定义矩形边界'], [[b['block_name'], b['rectangle']['method'], b['rectangle']['bounds']] for b in structure['frame_block_candidates']])
        lines += ['', f"页码分布：{structure['sheet_value_counts']}；所有图号页码集合完整：{structure['page_groups_complete']}。", '',
                  '图号重复次数分布：' + str(dict(sorted(Counter(structure['drawing_number_frequencies'].values()).items()))) + '（键=每图号页数，值=图号个数）。', '',
                  '图框数与图号数不同，需按图号及版本合并多页。179 个不同图号支持客户描述的图纸规模，但不能单凭图号证明 179 个独立待报价零件。', '',
                  '### 空间聚类交叉检查', '',
                  'DBSCAN 对图框中心与文字插入点进行多阈值实验；距离使用绘图坐标。聚类不能作为零件数量的唯一依据。', '']
        rows = []
        for family in ['frame_center_clustering', 'text_position_clustering']:
            for experiment in structure[family]:
                rows.append([family, f"{experiment['epsilon']:.4f}", experiment['min_points'], experiment['cluster_count'], experiment['noise_count'], sorted(experiment['cluster_sizes'].values(), reverse=True)[:10]])
        lines += table(['点集', 'epsilon', 'min_points', '簇数', '噪声点', '最大簇规模（前10）'], rows)
        lines += ['', f"文字位置落入图框数：{structure['text_positions_assigned_to_frames']}；同时落入多个图框：{structure['text_positions_in_multiple_frames']}。图框匹配按文字插入点，未验证每条文字包围盒。", '',
                  '边界示意图：[dxf_frame_map.svg](dxf_frame_map.svg)（编号对应 JSON 的 frames.id，悬停显示图号/页码；只是分区示意，不是完整 CAD 渲染）。', '']
        lines += ['### 多页图号证据', '']
        lines += table(['图号', '图框数', '页码', '总页数', '版本', '完整'], [[g['drawing_number'], g['frame_count'], g['pages'], g['declared_totals'], g['revision_values'], g['page_set_complete']] for g in structure['page_groups'] if g['frame_count'] > 1])
    else:
        lines += ['未成功解析目标大图，本次不能判断 A/B/C。', '']
    lines += ['## 6. 下一步建议', '',
              '结论：成功解析的 DXF 含可用于后续制造要求分析的实体、文字和尺寸字段；具备继续验证成本评估数据链路的基础。当前结果不支持直接自动报价，也不能证明转换完全保真。', '',
              '1. 优先核对一般公差表和技术要求的范围/优先级，取得 ±0.002→±0.01 案例的旧新图。特殊 gdt 符号必须按字体/图面验证后解释。',
              '2. 以图号+版本合并多页图框，关联材料/硬度/尺寸到同一零件；核对大图中冲模/板件命名与塑胶模具试点范围是否一致。',
              '3. 抽样人工核对尺寸覆盖、缓存值、缩放几何值与图面，明确单位和视图尺度。尤其不能从几何长度直接覆盖已有标注。',
              '4. 对同名 DWG/STP 对应关系进行身份/版本/几何验证；小图中“未注尺寸参考3D”的要求意味着仅 2D 不足以恢复全部制造尺寸。',
              '5. 获取可按图号/版本关联的历史工艺、准备/加工/检验工时、设备、批量和费率，明确成本口径，再建立可解释的估算基线。', '',
              '输出 JSON 保留完整实例文字与定义文字、尺寸字段、图框边界、聚类标签和错误。未调用 OCR；未保证隐藏属性、XCLIP、外部参照和字体环境的图面完整性。']
    return '\n'.join(lines) + '\n'


def analyze_files(raw):
    files = []
    for path in sorted(p for p in raw.rglob('*') if p.suffix.lower() == '.dxf' and (p.is_file() or p.is_symlink())):
        started = time.monotonic()
        item = {'path': path.relative_to(raw).as_posix(), 'status': 'error', 'analysis': None, 'errors': [], 'source_unchanged': None}
        files.append(item)
        LOGGER.info('Reading %s', item['path'])
        before = None
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(raw.resolve()):
                raise ValueError('Refusing symlink or file outside raw directory')
            before = sha256(path)
            item.update(size_bytes=path.stat().st_size, sha256=before)
            doc, metadata = read_dxf(path)
            item['parser'] = metadata
            item['analysis'] = analyze_document(doc)
            item['status'] = 'partial' if metadata['audit']['errors'] or item['analysis']['warnings'] else 'parsed'
            LOGGER.info('Extracted %s: %s', item['path'], item['analysis']['file_information'])
        except Exception as error:
            item['errors'].append({'code': type(error).__name__, 'message': str(error)})
            LOGGER.exception('Failed %s', item['path'])
        finally:
            if before is not None:
                try:
                    item['source_unchanged'] = sha256(path) == before
                    if not item['source_unchanged']:
                        item['status'] = 'error'
                        item['errors'].append({'code': 'source_changed', 'message': 'Source changed during scan; rerun required'})
                except Exception as error:
                    item['status'] = 'error'
                    item['errors'].append({'code': 'hash_verification_error', 'message': str(error)})
            item['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return {'schema_version': '2.0', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'raw_directory': str(raw), 'tool': {'name': 'dxf_deep_analysis', 'ezdxf_version': ezdxf.__version__, 'uses_llm': False},
            'summary': {'total_files': len(files), 'status_counts': dict(Counter(f['status'] for f in files)),
                        'total_bytes': sum(f.get('size_bytes', 0) for f in files),
                        'all_sources_unchanged': bool(files) and all(f['source_unchanged'] is True for f in files)},
            'files': files}


def main(argv=None):
    parser = argparse.ArgumentParser(description='DXF engineering information deep exploration, without LLMs')
    parser.add_argument('--raw-dir', default='data/raw')
    parser.add_argument('--output', default='data/processed/dxf_inventory.json')
    parser.add_argument('--report', default='reports/dxf_deep_analysis.md')
    parser.add_argument('--log-file', default='data/processed/dxf_deep_analysis.log')
    parser.add_argument('--frame-map', default='reports/dxf_frame_map.svg')
    args = parser.parse_args(argv)
    raw = project_path(args.raw_dir)
    output, report, log, frame_map = [project_path(v) for v in (args.output, args.report, args.log_file, args.frame_map)]
    allowed = [ROOT / 'data/processed', ROOT / 'reports']
    targets = [output, report, log, frame_map]
    for target in targets:
        if target.is_relative_to(raw) or not any(target.is_relative_to(root.resolve()) for root in allowed):
            parser.error('All outputs must be inside project data/processed or reports, and outside raw')
    if len(set(targets)) != len(targets):
        parser.error('Output paths must be distinct')
    if not raw.is_dir():
        parser.error(f'Raw directory does not exist: {raw}')
    log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(log, encoding='utf-8')], force=True)
    try:
        result = analyze_files(raw)
        atomic_write(output, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        large = next((f for f in result['files'] if Path(f['path']).name == 'CMY1000A-图档179张.dxf' and f['analysis']), None)
        if large:
            svg = frame_svg(large['analysis']['drawing_structure']['frames'])
            if svg:
                atomic_write(frame_map, svg)
        atomic_write(report, report_text(result))
        LOGGER.info('Saved JSON %s and report %s; summary=%s', output, report, result['summary'])
        return 1 if any(f['status'] == 'error' for f in result['files']) else 0
    except Exception:
        LOGGER.exception('Deep pipeline failed')
        return 1
