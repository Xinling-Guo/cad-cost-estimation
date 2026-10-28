"""DXF engineering-information exploration using CAD APIs and deterministic rules."""
from collections import Counter, defaultdict
import logging
import math
import re
import statistics

import ezdxf
from ezdxf import bbox
from ezdxf.entities.dimstyle import DimStyle
from ezdxf.math import Vec3

from .deep_spatial import cluster_points, detect_rectangles

LOGGER = logging.getLogger(__name__)
ENTITY_TYPES = ['LINE', 'LWPOLYLINE', 'POLYLINE', 'CIRCLE', 'ARC', 'SPLINE',
                'INSERT', 'TEXT', 'MTEXT', 'DIMENSION', 'HATCH']
KEYWORDS = ['材料', '公差', '技术要求', '热处理', '表面', '硬度', 'TOL', 'TOLERANCE', 'HRC', 'SKD', 'SUS', 'Ra']
TOKENS = ['±', '+', '-', 'H7', 'h7', '0.01', '0.002']
DIM_TYPES = {0: 'rotated_linear', 1: 'aligned', 2: 'angular_2_lines', 3: 'diameter',
             4: 'radius', 5: 'angular_3_points', 6: 'ordinate', 7: 'arc_length', 8: 'arc_dimension'}
STYLE_FIELDS = ['dimtol', 'dimlim', 'dimtp', 'dimtm', 'dimdec', 'dimtdec', 'dimadec',
                'dimzin', 'dimtzin', 'dimazin', 'dimlfac', 'dimlunit', 'dimdsep',
                'dimrnd', 'dimpost', 'dimalt', 'dimaltf', 'dimaltd', 'dimalttd', 'dimalttz', 'dimscale']
TEXT_TYPES = {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}


def value_json(value):
    if isinstance(value, Vec3):
        return [float(v) for v in value]
    if isinstance(value, dict):
        return {str(k): value_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [value_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def original(entity):
    source = entity
    while source.source_of_copy is not None:
        source = source.source_of_copy
    return source


def keyword_matches(text):
    return [key for key in KEYWORDS if (
        bool(re.search(r'(?<![A-Za-z])Ra(?=\s*[0-9]|[^A-Za-z]|$)', text, re.I))
        if key == 'Ra' else key.casefold() in text.casefold())]


def token_matches(text):
    found = []
    for token in TOKENS:
        if token.startswith('0.'):
            match = re.search(r'(?<!\d)' + re.escape(token) + r'(?!\d)', text)
        elif token in {'H7', 'h7'}:
            # Fit codes commonly follow a numeric nominal size: 10H7, 8h7.
            match = re.search(r'(?<![A-Za-z])' + token + r'(?![A-Za-z0-9])', text)
        else:
            match = token in text
        if match:
            found.append(token)
    if '+/-' in text or '%%p' in text.lower():
        if '±' not in found:
            found.append('±')
    return found


def point_of(entity):
    for attr in ('text_midpoint', 'insert', 'center', 'start', 'defpoint'):
        if entity.dxf.hasattr(attr):
            point = entity.dxf.get(attr)
            if entity.dxftype() in {'TEXT', 'ATTRIB', 'ATTDEF', 'DIMENSION'} and attr in {'insert', 'text_midpoint'}:
                point = entity.ocs().to_wcs(point)
            return value_json(point)
    if entity.dxftype() in {'LWPOLYLINE', 'POLYLINE'}:
        try:
            if entity.dxftype() == 'LWPOLYLINE':
                return value_json(next(iter(entity.vertices_in_wcs())))
            return value_json(next(iter(entity.points_in_wcs())))
        except StopIteration:
            pass
    return None


def text_record(entity, context, doc):
    source = original(entity)
    raw = entity.text if entity.dxftype() == 'MTEXT' else entity.dxf.get('text', '')
    layer = entity.dxf.get('layer', '0')
    style = entity.dxf.get('style', 'Standard')
    font = ''
    if style in doc.styles:
        font = doc.styles.get(style).dxf.get('font', '')
    record = {**context, 'entity_type': entity.dxftype(), 'source_handle': source.dxf.get('handle'),
              'raw_text': raw, 'plain_text': entity.plain_text(),
              'layer': layer, 'effective_layer': context.get('effective_layer') if layer == '0' else layer,
              'position': point_of(entity), 'coordinate_system': 'WCS',
              'definition_position': point_of(source), 'style': style, 'font': font,
              'rotation': entity.dxf.get('rotation', 0) if entity.dxftype() != 'MTEXT' else None,
              'height': entity.dxf.get('char_height', 0) if entity.dxftype() == 'MTEXT' else entity.dxf.get('height', 0),
              'attribute_tag': entity.dxf.get('tag') if entity.dxftype() in {'ATTRIB', 'ATTDEF'} else None}
    record['keyword_matches'] = keyword_matches(record['plain_text'])
    record['tolerance_search_matches'] = token_matches(record['plain_text'])
    record['special_font_encoding'] = 'gdt' in (raw + ' ' + font).lower()
    # Do not map gdt backtick/~ to ±/degrees without a validated glyph mapping.
    record['glyph_decoding_required'] = record['special_font_encoding']
    return record


def dimension_record(entity, context, doc):
    source = original(entity)
    kind = source.dimtype
    override = source.override()
    effective = {field: value_json(override.get(field, DimStyle.DXFATTRIBS.get(field).default)) for field in STYLE_FIELDS}
    graphics = []
    for virtual in source.virtual_entities():
        if virtual.dxftype() in TEXT_TYPES:
            graphics.append({'raw_text': virtual.text if virtual.dxftype() == 'MTEXT' else virtual.dxf.text,
                             'plain_text': virtual.plain_text()})
    try:
        measurement = value_json(source.get_measurement())
        measurement_error = None
    except Exception as error:
        measurement, measurement_error = None, str(error)
    if kind == 6 and isinstance(measurement, list):
        geometric_scalar = measurement[0] if source.dxf.dimtype & 64 else measurement[1]
    elif isinstance(measurement, (float, int)):
        geometric_scalar = measurement
    else:
        geometric_scalar = None
    angular = kind in {2, 5}
    scaled = geometric_scalar if angular or geometric_scalar is None else geometric_scalar * effective['dimlfac']
    raw_override = source.dxf.get('text', '')
    visible = [text['plain_text'] for text in graphics]
    all_text = '\n'.join([raw_override] + visible)
    explicit_numeric = float(raw_override) if re.fullmatch(r'\s*[+-]?\d+(?:\.\d+)?\s*', raw_override) else None
    cached = source.dxf.get('actual_measurement')
    cached_mismatch = (isinstance(cached, (int, float)) and scaled is not None
                       and not math.isclose(cached, scaled, rel_tol=1e-5, abs_tol=1e-5))
    explicit_mismatch = explicit_numeric is not None and scaled is not None and not math.isclose(explicit_numeric, scaled, rel_tol=1e-5, abs_tol=1e-5)
    return {'source_handle': source.dxf.handle, 'dimension_type_code': kind,
            'dimension_type': DIM_TYPES.get(kind, 'unknown'), 'raw_dimtype_flags': source.dxf.dimtype,
            'layer': entity.dxf.layer, 'position': point_of(entity), **context,
            'dimstyle': source.dxf.dimstyle, 'annotation_override_raw': raw_override,
            'annotation_visible_texts': graphics, 'geometry_block': source.dxf.get('geometry'),
            'geometric_measurement': measurement, 'geometric_measurement_error': measurement_error,
            'measurement_coordinate_system': 'source entity WCS before block-instance transformation',
            'geometric_scalar': geometric_scalar, 'scaled_geometric_value': scaled,
            'cached_actual_measurement': cached, 'explicit_override_numeric_value': explicit_numeric,
            'value_unit': 'degree' if angular else 'drawing_unit; verify INSUNITS and annotation scale',
            'tolerance': {'enabled': bool(effective['dimtol']), 'limits_enabled': bool(effective['dimlim']),
                          'upper_magnitude': effective['dimtp'], 'lower_magnitude': effective['dimtm'],
                          'lower_deviation': -effective['dimtm'],
                          'active': bool(effective['dimtol'] or effective['dimlim']),
                          'note': 'inactive style values are not applied tolerances; visible text may carry additional requirements'},
            'precision': {'linear': effective['dimdec'], 'tolerance': effective['dimtdec'],
                          'angular': effective['dimadec'], 'zero_suppression': effective['dimzin']},
            'effective_dimstyle_fields': effective, 'entity_overrides': value_json(override.dimstyle_attribs),
            'style_explicit_fields': value_json(override.dimstyle.dxf.all_existing_dxf_attribs()),
            'search_matches': token_matches(all_text),
            'cached_vs_geometry_mismatch': cached_mismatch,
            'numeric_override_vs_geometry_mismatch': explicit_mismatch,
            'numeric_text_tokens': re.findall(r'[+-]?\d+(?:\.\d+)?', all_text),
            'limitations': 'Do not assume geometric/cached/displayed values are interchangeable; stacked text is not automatically interpreted.'}


def walk_entities(entities, doc, context, warnings, active_blocks=(), depth=0):
    """Walk visible instances and attached attributes, not unused block definitions."""
    if depth > 24:
        warnings.append({'code': 'block_depth_limit', **context})
        return
    for entity in entities:
        yield entity, context
        if entity.dxftype() == 'INSERT':
            name = entity.dxf.name
            source_handle = original(entity).dxf.get('handle')
            if name in active_blocks:
                warnings.append({'code': 'cyclic_block', 'block_name': name, **context})
                continue
            if name not in doc.blocks:
                warnings.append({'code': 'missing_block', 'block_name': name, **context})
                continue
            inserts = entity.multi_insert() if entity.mcount > 1 else [entity]
            for cell, insert in enumerate(inserts):
                path = context['block_path'] + [f'{name}@{source_handle}:{cell}']
                child = {**context, 'block_path': path, 'scope': 'block_instance',
                         'effective_layer': context.get('effective_layer') if insert.dxf.layer == '0' else insert.dxf.layer}
                if not math.isclose(abs(insert.dxf.xscale), abs(insert.dxf.yscale)):
                    warnings.append({'code': 'nonuniform_insert_scale', **child})
                # Attached attribute insert coordinates are already in the parent's WCS.
                for attribute in insert.attribs:
                    yield attribute, {**child, 'scope': 'block_attribute'}
                def skipped(skipped_entity, reason):
                    warnings.append({'code': 'virtual_entity_skipped', 'entity_type': skipped_entity.dxftype(),
                                     'reason': reason, **child})
                try:
                    yield from walk_entities(insert.virtual_entities(skipped_entity_callback=skipped), doc,
                                             child, warnings, active_blocks + (name,), depth + 1)
                except Exception as error:
                    warnings.append({'code': 'block_expansion_error', 'message': str(error), **child})
        elif entity.dxftype() == 'ACAD_TABLE':
            name = entity.dxf.get('geometry', '')
            child = {**context, 'scope': 'table_graphic',
                     'block_path': context['block_path'] + [f'table:{name}@{original(entity).dxf.get("handle")}']}
            if name not in doc.blocks:
                warnings.append({'code': 'missing_table_graphic', 'block_name': name, **child})
                continue
            extrusion = entity.dxf.get('extrusion', Vec3(0, 0, 1)) if entity.dxf.is_supported('extrusion') else Vec3(0, 0, 1)
            if not Vec3(extrusion).isclose(Vec3(0, 0, 1)):
                warnings.append({'code': 'unsupported_table_extrusion', **child})
                continue
            if not Vec3(entity.dxf.get('horizontal_direction', Vec3(1, 0, 0))).isclose(Vec3(1, 0, 0)):
                warnings.append({'code': 'table_direction_requires_visual_check', **child})
            try:
                # The API preserves table block geometry and insertion translation.
                # Native row/column values in these converted files are not trusted.
                from ezdxf.protocols import virtual_entities
                yield from walk_entities(virtual_entities(entity), doc, child, warnings,
                                         active_blocks + (name,), depth + 1)
            except Exception as error:
                warnings.append({'code': 'table_expansion_error', 'message': str(error), **child})
        elif entity.dxftype() == 'DIMENSION':
            child = {**context, 'scope': 'dimension_graphic',
                     'block_path': context['block_path'] + [f'dimension:{original(entity).dxf.get("handle")}']}
            try:
                for virtual in entity.virtual_entities():
                    if virtual.dxftype() in TEXT_TYPES:
                        yield virtual, child
            except Exception as error:
                warnings.append({'code': 'dimension_graphic_error', 'message': str(error), **child})


def frame_analysis(doc, texts):
    block_candidates, top_rectangles, frames = [], [], []
    for block in doc.blocks:
        if block.name.startswith('*'):
            continue
        definitions = list(block)
        labels = ' '.join(e.plain_text() for e in definitions if e.dxftype() in TEXT_TYPES)
        tags = {e.dxf.tag.upper() for e in definitions if e.dxftype() == 'ATTDEF'}
        identity = bool(tags & {'DWG-NO:', 'DRAWING NO.', 'TITLE', 'SHEET'}) or ('材料' in labels and '一般公差' in labels)
        if not identity:
            continue
        rectangles = detect_rectangles(definitions)
        if rectangles:
            block_candidates.append({'block_name': block.name, 'rectangle': rectangles[0],
                                     'attribute_tags': sorted(tags), 'evidence': 'identity labels + rectangular perimeter'})
    candidates = {b['block_name']: b for b in block_candidates}
    for layout in doc.layouts:
        for entity in layout.query('INSERT'):
            if entity.dxf.name not in candidates:
                continue
            local = candidates[entity.dxf.name]['rectangle']['bounds']
            inserts = entity.multi_insert() if entity.mcount > 1 else [entity]
            for cell, insert in enumerate(inserts):
                matrix = insert.matrix44()
                corners = [value_json(matrix.transform(Vec3(x, y, 0)))
                           for x, y in [(local[0], local[1]), (local[2], local[1]), (local[2], local[3]), (local[0], local[3])]]
                xs, ys = [p[0] for p in corners], [p[1] for p in corners]
                attrs = {a.dxf.tag: a.dxf.text.strip() for a in insert.attribs}
                frames.append({'id': f'F{len(frames)+1:03d}', 'layout': layout.name,
                               'block_name': insert.dxf.name, 'insert_handle': entity.dxf.handle,
                               'cell': cell, 'insert': value_json(insert.dxf.insert),
                               'scale': [insert.dxf.xscale, insert.dxf.yscale], 'rotation': insert.dxf.rotation,
                               'corners': corners, 'bounds': [min(xs), min(ys), max(xs), max(ys)],
                               'attributes': attrs, 'drawing_number': attrs.get('DWG-NO:', ''),
                               'title': attrs.get('TITLE', ''), 'revision': attrs.get('REV', ''),
                               'sheet': attrs.get('SHEET', ''), 'rectangle_method': candidates[insert.dxf.name]['rectangle']['method']})
        rectangles = detect_rectangles(layout)
        top_rectangles.extend([{'layout': layout.name, **r} for r in rectangles])
    numbers = Counter(f['drawing_number'] for f in frames if f['drawing_number'])
    sheets = Counter(f['sheet'] for f in frames)
    groups = defaultdict(list)
    for frame in frames:
        groups[frame['drawing_number']].append(frame)
    checks = []
    for number, group in groups.items():
        parsed = [re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', f['sheet']) for f in group]
        totals = {int(p.group(2)) for p in parsed if p}
        pages = [int(p.group(1)) for p in parsed if p]
        complete = len(totals) == 1 and len(pages) == len(group) and sorted(pages) == list(range(1, next(iter(totals)) + 1))
        checks.append({'drawing_number': number, 'frame_count': len(group), 'pages': pages,
                       'declared_totals': sorted(totals), 'page_set_complete': complete,
                       'revision_values': sorted({f['revision'] for f in group})})
    centers = [[(f['bounds'][0] + f['bounds'][2]) / 2, (f['bounds'][1] + f['bounds'][3]) / 2] for f in frames]
    clustering = []
    if centers:
        width = statistics.median(f['bounds'][2] - f['bounds'][0] for f in frames)
        for epsilon in [max(1e-5, width * 1e-4), width * 0.75, width * 1.5]:
            clustering.append(cluster_points(centers, epsilon, min_points=2))
    # A separate text-position DBSCAN experiment, explicitly not a part counter.
    points = [t['position'][:2] for t in texts if t['layout'] == 'Model' and t['position'] and t['plain_text'].strip()
              and t['scope'] in {'layout', 'block_attribute'}]
    text_clusters = []
    if frames:
        width = statistics.median(f['bounds'][2] - f['bounds'][0] for f in frames)
        text_clusters = [cluster_points(points, width * ratio, min_points=4) for ratio in (0.1, 0.25)]
    # Point-in-polygon, so rotated rectangles are not assigned by AABB alone.
    def inside(point, corners):
        signs = []
        for a, b in zip(corners, corners[1:] + corners[:1]):
            signs.append((b[0]-a[0])*(point[1]-a[1]) - (b[1]-a[1])*(point[0]-a[0]))
        return all(v >= -1e-5 for v in signs) or all(v <= 1e-5 for v in signs)
    assigned = ambiguous = 0
    for text in texts:
        if text['layout'] != 'Model' or not text['position']:
            continue
        point = text['position']
        matches = [f['id'] for f in frames if f['layout'] == 'Model'
                   and f['bounds'][0]-1e-5 <= point[0] <= f['bounds'][2]+1e-5
                   and f['bounds'][1]-1e-5 <= point[1] <= f['bounds'][3]+1e-5
                   and inside(point, f['corners'])]
        text['frame_ids'] = matches
        assigned += bool(matches)
        ambiguous += len(matches) > 1
    model_frames = sum(f['layout'] == 'Model' for f in frames)
    populated_paper_layouts = sum(len(l) > 0 for l in doc.layouts if l.name != 'Model')
    classification = 'A' if populated_paper_layouts >= 179 else 'B' if model_frames > 1 else 'unresolved'
    return {'classification': classification,
            'classification_meaning': 'B = repeated title frames in modelspace; not one layout per drawing',
            'frame_block_candidates': block_candidates, 'frame_count': len(frames),
            'unique_drawing_number_count': len(numbers), 'drawing_number_frequencies': dict(sorted(numbers.items())),
            'sheet_value_counts': dict(sorted(sheets.items())), 'page_groups': checks,
            'page_groups_complete': bool(checks) and all(g['page_set_complete'] for g in checks),
            'frames': frames, 'top_level_rectangle_candidates': top_rectangles,
            'frame_center_clustering': clustering, 'text_position_clustering': text_clusters,
            'text_positions_assigned_to_frames': assigned, 'text_positions_in_multiple_frames': ambiguous,
            'limitations': ['图框候选不等于实际零件数；不同图号也可能是同一零件的版本。',
                           '图框检测仅支持轴对齐定义矩形及顶层图框块；未识别候选不证明没有分区。',
                           '空间聚类结果依赖距离阈值，仅作布局交叉检查，不能替代图号/页码。',
                           '文字归属基于插入点；跨边界文字、视口、XCLIP与可见性需图面复核。']}


def analyze_document(doc):
    warnings, texts, dimensions, layouts = [], [], [], []
    count = Counter()
    for layout in doc.layouts:
        local = Counter(e.dxftype() for e in layout)
        count.update(local)
        layouts.append({'name': layout.name, 'entity_count': len(layout), 'entity_type_counts': dict(sorted(local.items()))})
        context = {'layout': layout.name, 'scope': 'layout', 'block_path': [], 'effective_layer': '0'}
        for entity, ctx in walk_entities(layout, doc, context, warnings):
            try:
                kind = entity.dxftype()
                if kind in TEXT_TYPES:
                    if kind == 'ATTDEF' and ctx['scope'] == 'block_instance':
                        # Nonconstant ATTDEF defaults are not actual attribute values.
                        if not entity.is_const:
                            continue
                        ctx = {**ctx, 'scope': 'constant_attribute_definition'}
                    texts.append(text_record(entity, ctx, doc))
                elif kind == 'DIMENSION':
                    dimensions.append(dimension_record(entity, ctx, doc))
            except Exception as error:
                warnings.append({'code': 'entity_extraction_error', 'entity_type': entity.dxftype(),
                                 'source_handle': original(entity).dxf.get('handle'), 'message': str(error), **ctx})
    # Definitions are kept separately, with local coordinates and no claim of visibility.
    definitions, blocks = [], []
    for block in doc.blocks:
        blocks.append({'name': block.name, 'base_point': value_json(block.base_point),
                       'entity_count': len(block), 'entity_type_counts': dict(sorted(Counter(e.dxftype() for e in block).items())),
                       'is_xref': block.block.is_xref})
        # Layout blocks duplicate the model/paperspace already traversed.
        if block.name.upper().startswith(('*MODEL_SPACE', '*PAPER_SPACE')):
            continue
        for entity in block:
            if entity.dxftype() in TEXT_TYPES:
                try:
                    context = {'layout': None, 'scope': 'block_definition', 'block_path': [block.name], 'effective_layer': None}
                    record = text_record(entity, context, doc)
                    record['coordinate_system'] = 'block local OCS converted to local WCS'
                    definitions.append(record)
                except Exception as error:
                    warnings.append({'code': 'definition_text_error', 'block_name': block.name, 'message': str(error)})
    structure = frame_analysis(doc, texts)
    for i, text in enumerate(texts):
        text['id'] = f'T{i+1:06d}'
    layers = [{'name': l.dxf.name, 'color': l.dxf.color, 'linetype': l.dxf.linetype,
               'is_off': l.is_off(), 'is_frozen': l.is_frozen()} for l in doc.layers]
    keywords = {k: [t['id'] for t in texts if k in t['keyword_matches']] for k in KEYWORDS}
    tokens = {k: [t['id'] for t in texts if k in t['tolerance_search_matches']] for k in TOKENS}
    dim_tokens = {k: [d['source_handle'] for d in dimensions if k in d['search_matches']] for k in TOKENS}
    scope_counts = Counter(t['scope'] for t in texts)
    attribute_values = defaultdict(Counter)
    for text in texts:
        if text['attribute_tag'] and text['scope'] == 'block_attribute':
            attribute_values[text['attribute_tag']][text['plain_text'].strip()] += 1
    xrefs = [b['name'] for b in blocks if b['is_xref']]
    fonts = [value_json(s.dxf.all_existing_dxf_attribs()) for s in doc.styles]
    return {'file_information': {'dxf_version': doc.dxfversion,
                                 'entity_total': sum(count.values()), 'entity_count_scope': 'top-level model + paperspace, excluding attached attributes and block contents',
                                 'entity_database_total': len(doc.entitydb), 'layer_count': len(layers),
                                 'layout_count': len(layouts), 'paper_layout_count': len(layouts)-1,
                                 'block_count': len(blocks), 'block_count_definition': 'block definitions, including anonymous dimension/table/layout blocks',
                                 'insert_count': count['INSERT'], 'units_code': doc.header.get('$INSUNITS', 0),
                                 'measurement_system': doc.header.get('$MEASUREMENT')},
            'entity_type_counts': {k: count[k] for k in ENTITY_TYPES},
            'all_entity_type_counts': dict(sorted(count.items())), 'layers': layers, 'layouts': layouts,
            'blocks': blocks, 'insert_name_counts': dict(sorted(Counter(e.dxf.name for l in doc.layouts for e in l.query('INSERT')).items())),
            'texts': texts, 'block_definition_texts': definitions,
            'text_summary': {'instance_text_count': len(texts), 'definition_text_count': len(definitions),
                             'instance_scope_counts': dict(sorted(scope_counts.items())),
                             'keyword_match_counts': {k: len(v) for k, v in keywords.items()},
                             'tolerance_token_counts': {k: len(v) for k, v in tokens.items()},
                             'special_font_text_count': sum(t['glyph_decoding_required'] for t in texts),
                             'replacement_character_count': sum('\ufffd' in t['raw_text'] for t in texts)},
            'keyword_matches': keywords, 'tolerance_text_matches': tokens,
            'attribute_value_counts': {tag: dict(sorted(values.items())) for tag, values in sorted(attribute_values.items())},
            'dimensions': dimensions, 'dimension_search_matches': dim_tokens,
            'dimension_summary': {'instance_dimension_count': len(dimensions),
                                  'type_counts': dict(sorted(Counter(d['dimension_type'] for d in dimensions).items())),
                                  'active_tolerance_count': sum(d['tolerance']['active'] for d in dimensions),
                                  'precision_counts': dict(sorted(Counter(str(d['precision']['linear']) for d in dimensions).items())),
                                  'cached_vs_geometry_mismatch_count': sum(d['cached_vs_geometry_mismatch'] for d in dimensions),
                                  'numeric_override_vs_geometry_mismatch_count': sum(d['numeric_override_vs_geometry_mismatch'] for d in dimensions),
                                  'search_token_counts': {k: len(v) for k, v in dim_tokens.items()}},
            'drawing_structure': structure, 'font_styles': fonts, 'external_reference_blocks': xrefs,
            'warnings': warnings, 'warning_counts': dict(Counter(w['code'] for w in warnings)),
            'limitations': ['没有执行OCR/LLM或重建尺寸显示；仅读取存储实体与已有显示块。',
                           '块实例文字与未引用定义文字分开；定义默认值不是实际材料或硬度。',
                           '未验证字体文件安装、外参加载、隐藏属性与视口可见性、XCLIP。',
                           'CAD可解析不等于成本可预测，仍需工艺/工时/费率及版本关联。']}
