"""Recursive inventory, per-file error isolation, and evidence-based report."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

from dotenv import load_dotenv
from .parsers import identify, inspect_step, parse_drawing, MissingConverterError

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def scan(raw, converter=''):
    if not raw.is_dir():
        raise FileNotFoundError(f'原始数据目录不存在: {raw}')
    files = []
    for path in sorted(raw.rglob('*')):
        if not path.is_file() and not path.is_symlink():
            continue
        entry = {'path': path.relative_to(raw).as_posix(), 'status': 'error',
                 'features': None, 'errors': []}
        files.append(entry)
        LOGGER.info('Inspecting %s', entry['path'])
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(raw.resolve()):
                raise ValueError('跳过符号链接，避免读取 raw 目录外的数据')
            entry.update(size_bytes=path.stat().st_size, sha256=sha256(path))
            entry.update(identify(path))
            if entry['type'] in {'dwg', 'dxf'}:
                entry.update(parse_drawing(path, entry['type'], converter))
            elif entry['type'] == 'step':
                entry.update(inspect_step(path))
            else:
                entry['status'] = 'unsupported'
                entry['errors'].append({'code': 'unsupported_format', 'message': '未支持此文件类型'})
            if sha256(path) != entry['sha256']:
                raise RuntimeError('扫描期间文件内容变化，请重新运行')
        except Exception as error:
            entry['status'] = 'blocked' if isinstance(error, MissingConverterError) else 'error'
            entry['features'] = None
            entry['errors'].append({'code': type(error).__name__, 'message': str(error)})
            LOGGER.warning('%s: %s', entry['path'], error, exc_info=entry['status'] == 'error')
    types = Counter(item.get('type', 'unknown') for item in files)
    statuses = Counter(item['status'] for item in files)
    return {'schema_version': '1.0', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'raw_directory': str(raw.resolve()),
            'summary': {'total_files': len(files), 'total_bytes': sum(f.get('size_bytes', 0) for f in files),
                        'file_type_counts': dict(sorted(types.items())),
                        'status_counts': dict(sorted(statuses.items())),
                        'files_with_errors': sum(bool(f['errors']) for f in files)},
            'files': files}


def report_text(inventory):
    summary = inventory['summary']
    rows = ['# CAD 数据探查报告', '', f"生成时间（UTC）：{inventory['generated_at']}", '',
            f"文件总数：{summary['total_files']}；类型统计：{json.dumps(summary['file_type_counts'], ensure_ascii=False)}。",
            f"处理状态：{json.dumps(summary['status_counts'], ensure_ascii=False)}。", '',
            '| 文件 | 类型 | 状态 | 结果/阻塞原因 |', '| --- | --- | --- | --- |']
    for item in inventory['files']:
        detail = '; '.join(e['message'] for e in item['errors'])
        features = item['features']
        if features and item.get('type') in {'dwg', 'dxf'}:
            detail = (f"实体 {features['entity_count']}，尺寸 {features['dimension_count']}，"
                      f"块引用 {features['block_count']}，几何 {features['geometry_count']}，文字 {features['text_count']}")
        elif features:
            detail = (f"STEP 实体声明 {features['entity_declaration_count']}；"
                      f"MANIFOLD_SOLID_BREP {features['solid_declaration_count']}，"
                      f"ADVANCED_FACE {features['advanced_face_declaration_count']}；未验证三维几何")
        cells = [item['path'], item.get('type', 'unknown'), item['status'], detail]
        rows.append('| ' + ' | '.join(str(c).replace('|', '\\|').replace('\n', ' ') for c in cells) + ' |')
    rows.extend(['', '## 数据可用性判断', '',
                 'DWG：仅识别文件头不能证明图纸可解析。状态为 blocked 时尚未提取文字、公差或几何，需要转换器或甲方导出的 DXF。',
                 'DXF：parsed 表示可提取 CAD 实体；仍需检查图框一般公差、块内文字、外部参照和字体。partial 表示存在未修复审计错误。',
                 'STEP：inspected 仅表示可识别 Part 21 格式与实体声明，不能证明三维模型有效或可用于加工特征识别。',
                 '甲方说明大图包含 179 个零件；该数量尚未通过 CAD 分区验证，文件数量也不等于零件数量。', '',
                 '## 下一步', '',
                 '1. 安装 DWG 转换器或在 Windows CAD 软件中导出 DXF，重新运行探查。',
                 '2. 抽样核对图框文字与一般公差适用规则，尤其是 ±0.002 与 ±0.01 的变更。',
                 '3. 对多零件大图划分图框与零件区域；按文件名配对的 DWG/STP 仍需人工验证对应关系。',
                 '4. 在独立阶段引入三维 CAD 内核检查 STEP 拓扑、尺寸、体积和加工特征，再评估成本估算所需字段。', '',
                 '本阶段不实现 LLM Agent、2D 转 3D、工艺规划或加工报价。'])
    return '\n'.join(rows) + '\n'


def project_path(value):
    path = Path(value).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def main(argv=None):
    load_dotenv(ROOT / '.env', override=False)
    parser = argparse.ArgumentParser(description='只读 CAD 文件探查')
    parser.add_argument('--raw-dir', default=os.getenv('CAD_RAW_DIR', 'data/raw'))
    parser.add_argument('--output', default=os.getenv('CAD_OUTPUT_FILE', 'data/processed/cad_inventory.json'))
    parser.add_argument('--report', default=os.getenv('CAD_REPORT_FILE', 'reports/cad_data_analysis.md'))
    parser.add_argument('--log-file', default=os.getenv('CAD_LOG_FILE', 'logs/cad_inventory.log'))
    args = parser.parse_args(argv)
    raw = project_path(args.raw_dir)
    output, report, log = (project_path(p) for p in (args.output, args.report, args.log_file))
    for target in (output, report, log):
        if target == raw or target.is_relative_to(raw):
            parser.error('输出文件、报告和日志不能写入原始数据目录')
    if len({output, report, log}) != 3:
        parser.error('输出文件、报告和日志必须使用不同路径')
    log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(),
                        format='%(asctime)s %(levelname)s %(name)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(log, encoding='utf-8')], force=True)
    try:
        inventory = scan(raw, os.getenv('ODA_FILE_CONVERTER', ''))
        atomic_write(output, json.dumps(inventory, ensure_ascii=False, indent=2) + '\n')
        atomic_write(report, report_text(inventory))
        LOGGER.info('Inventory saved: %s; summary=%s', output, inventory['summary'])
        # A completed scan may contain blocked files; consult status_counts.
        return 0
    except Exception:
        LOGGER.exception('Pipeline failed')
        return 1
