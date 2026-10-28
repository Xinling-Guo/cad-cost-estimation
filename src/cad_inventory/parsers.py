"""CAD readers. DWG conversion only touches a temporary copy."""
from collections import Counter
from pathlib import Path
import re
import shutil
import tempfile

import ezdxf
from ezdxf import recover
from ezdxf.addons import odafc

GEOMETRY_TYPES = {
    'LINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'LWPOLYLINE', 'POLYLINE', 'SPLINE',
    'POINT', 'HATCH', 'SOLID', 'TRACE', '3DFACE', 'MESH', '3DSOLID',
    'REGION', 'BODY', 'RAY', 'XLINE', 'HELIX', 'SURFACE',
}


class MissingConverterError(RuntimeError):
    pass


def identify(path: Path) -> dict:
    with path.open('rb') as stream:
        head = stream.read(4096)
    suffix_type = {'.dwg': 'dwg', '.dxf': 'dxf', '.stp': 'step', '.step': 'step'}.get(path.suffix.lower(), 'unknown')
    detected = 'unknown'
    version = None
    if re.match(rb'AC\d{4}', head):
        detected, version = 'dwg', head[:6].decode('ascii')
    elif head.startswith(b'AutoCAD Binary DXF') or re.match(rb'\s*0\s+SECTION\b', head):
        detected = 'dxf'
    elif b'ISO-10303-21;' in head.upper():
        detected = 'step'
    return {'extension': path.suffix.lower(), 'extension_type': suffix_type,
            'detected_type': detected, 'type': detected if detected != 'unknown' else suffix_type,
            'dwg_version': version,
            'extension_mismatch': detected != 'unknown' and detected != suffix_type}


def audit_details(auditor) -> dict:
    def items(values):
        return [{'code': int(item.code), 'message': str(item.message)} for item in values]
    return {'errors': items(auditor.errors), 'fixes': items(auditor.fixes)}


def read_dxf(path: Path):
    try:
        doc = ezdxf.readfile(path)
        return doc, {'reader': 'ezdxf', 'audit': audit_details(doc.audit())}
    except ezdxf.DXFError as error:
        doc, auditor = recover.readfile(path, errors='strict')
        return doc, {'reader': 'ezdxf.recover', 'initial_error': str(error),
                     'audit': audit_details(auditor)}


def extract_drawing(doc) -> dict:
    """Layout counts exclude block definitions; definitions are reported separately."""
    counts = Counter()
    texts, dimensions, inserts, layouts = [], [], [], []
    referenced_names = Counter()
    def collect_text(entity, scope):
        kind = entity.dxftype()
        if kind in {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}:
            texts.append({'entity_type': kind, 'scope': scope,
                          'handle': entity.dxf.get('handle'),
                          'layer': entity.dxf.get('layer', '0'),
                          'text': entity.plain_text() if kind == 'MTEXT' else entity.dxf.get('text', '')})
    for layout in doc.layouts:
        local = Counter()
        for entity in layout:
            kind = entity.dxftype()
            counts[kind] += 1
            local[kind] += 1
            collect_text(entity, f'layout:{layout.name}')
            if kind == 'INSERT':
                referenced_names[entity.dxf.name] += 1
                inserts.append({'name': entity.dxf.name, 'layer': entity.dxf.layer,
                                'layout': layout.name, 'handle': entity.dxf.handle})
                for attrib in entity.attribs:
                    collect_text(attrib, f'insert:{entity.dxf.handle}')
            if kind == 'DIMENSION':
                dimensions.append({'handle': entity.dxf.handle, 'layer': entity.dxf.layer,
                                   'layout': layout.name, 'dimtype': entity.dxf.dimtype,
                                   'text_override': entity.dxf.get('text', ''),
                                   'dimstyle': entity.dxf.dimstyle})
        layouts.append({'name': layout.name, 'entity_type_counts': dict(sorted(local.items()))})
    blocks = []
    for block in doc.blocks:
        block_counts = Counter(entity.dxftype() for entity in block)
        blocks.append({'name': block.name, 'entity_count': len(block),
                       'entity_type_counts': dict(sorted(block_counts.items()))})
        for entity in block:
            collect_text(entity, f'block:{block.name}')
            if entity.dxftype() == 'INSERT':
                for attrib in entity.attribs:
                    collect_text(attrib, f'block:{block.name}/insert:{entity.dxf.handle}')
    return {
        'dxf_version': doc.dxfversion, 'units_code': doc.header.get('$INSUNITS', 0),
        'count_scope': 'modelspace + paperspace; block definitions excluded; INSERT not expanded',
        'entity_type_counts': dict(sorted(counts.items())), 'entity_count': sum(counts.values()),
        'layers': [{'name': layer.dxf.name, 'color': layer.dxf.color,
                    'linetype': layer.dxf.linetype, 'is_off': layer.is_off(),
                    'is_frozen': layer.is_frozen()} for layer in doc.layers],
        'texts': texts, 'text_count': len(texts),
        'dimension_count': counts['DIMENSION'], 'dimensions': dimensions,
        'block_count': counts['INSERT'], 'block_inserts': inserts,
        'block_definition_count': len(blocks), 'block_definitions': blocks,
        'referenced_block_names': dict(sorted(referenced_names.items())),
        'geometry_count': sum(counts[kind] for kind in GEOMETRY_TYPES),
        'geometry_types': sorted(GEOMETRY_TYPES), 'layouts': layouts,
        'tolerance_text_candidates': [t for t in texts if any(token in t['text'].lower()
                                      for token in ('公差', '±', '%%p', 'tolerance'))],
        'limitations': ['文字候选需人工确认；不推断一般公差适用范围。',
                       '不展开块实例；块定义中的尺寸与几何见独立统计。',
                       '未执行OCR；炸开为线条的文字和尺寸无法按文字/尺寸实体识别。'],
    }


def parse_drawing(path: Path, kind: str, converter: str = '') -> dict:
    if kind == 'dxf':
        doc, metadata = read_dxf(path)
    else:
        if converter:
            ezdxf.options.set('odafc-addon', 'unix_exec_path', converter)
        if not odafc.is_installed():
            raise MissingConverterError('未安装 ODAFileConverter；安装 Linux 转换器或从 Windows 导出 DXF 后重新运行。')
        with tempfile.TemporaryDirectory(prefix='cad_inventory_') as temp:
            staged = Path(temp) / 'input.dwg'
            shutil.copyfile(path, staged)
            doc = odafc.readfile(staged, audit=False)
            metadata = {'reader': 'ODAFileConverter + ezdxf', 'audit': audit_details(doc.audit())}
    features = extract_drawing(doc)
    return {'status': 'partial' if metadata['audit']['errors'] else 'parsed',
            'parser': metadata, 'features': features}


def inspect_step(path: Path) -> dict:
    """Lexical probe only; no B-rep/topology/volume validation."""
    content = path.read_text(encoding='ascii', errors='strict')
    if not content.lstrip().upper().startswith('ISO-10303-21;') or 'END-ISO-10303-21;' not in content.upper():
        raise ValueError('STEP Part 21 header/footer missing')
    # Remove comments and quoted strings before counting entity declarations.
    scrubbed = re.sub(r"'(?:''|[^'])*'|/\*.*?\*/", ' ', content, flags=re.S)
    counts = Counter(re.findall(r'#\d+\s*=\s*([A-Z][A-Z0-9_]*)\s*\(', scrubbed, flags=re.I))
    if not counts:
        raise ValueError('未识别到 STEP 实体声明；可能是复合实体格式或无有效数据')
    schema = re.search(r'FILE_SCHEMA\s*\((.*?)\)\s*;', content, re.S | re.I)
    return {'status': 'inspected', 'parser': {'reader': 'STEP lexical probe'},
            'features': {'schema_declaration': schema.group(1).strip() if schema else None,
                         'entity_declaration_count': sum(counts.values()),
                         'entity_type_counts': dict(sorted(counts.items())),
                         'solid_declaration_count': counts['MANIFOLD_SOLID_BREP'],
                         'advanced_face_declaration_count': counts['ADVANCED_FACE'],
                         'geometry_validated': False,
                         'limitations': ['仅扫描 STEP 实体声明，非三维CAD内核解析；不提供体积、尺寸、拓扑有效性。']}}
