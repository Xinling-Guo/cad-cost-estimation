import ezdxf
import pytest

from cad_inventory.deep_analysis import analyze_document, token_matches
from cad_inventory.deep_pipeline import analyze_files, main
from cad_inventory.deep_spatial import cluster_points, detect_rectangles


def test_insert_attributes_nested_text_and_frame_pages(tmp_path):
    doc = ezdxf.new('R2018')
    doc.layers.new('FRAME')
    frame = doc.blocks.new('FRAME')
    frame.add_lwpolyline([(0, 0), (100, 0), (100, 70), (0, 70)], close=True)
    frame.add_attdef('DWG-NO:', (60, 5), text='DEFAULT-NOT-A-PART')
    frame.add_attdef('SHEET', (80, 5), text='1/1')
    frame.add_text('一般公差 ±0.01', dxfattribs={'insert': (20, 5), 'layer': '0'})
    nested = doc.blocks.new('MATERIAL')
    nested.add_text('SKD61', dxfattribs={'insert': (1, 1)})
    frame.add_blockref('MATERIAL', (10, 10))
    for i in range(2):
        insert = doc.modelspace().add_blockref('FRAME', (i * 250, 0), dxfattribs={'xscale': 2, 'yscale': 2, 'layer': 'FRAME'})
        insert.add_auto_attribs({'DWG-NO:': 'PART-A', 'SHEET': f'{i+1}/2'})
    path = tmp_path / 'sample.dxf'
    doc.saveas(path)
    before = path.read_bytes()
    result = analyze_files(tmp_path)['files'][0]
    assert result['status'] == 'parsed'
    assert path.read_bytes() == before and result['source_unchanged']
    analysis = result['analysis']
    texts = analysis['texts']
    material = [t for t in texts if t['plain_text'] == 'SKD61']
    assert [t['position'] for t in material] == [[22.0, 22.0, 0.0], [272.0, 22.0, 0.0]]
    assert material[0]['effective_layer'] == 'FRAME'
    assert not any(t['plain_text'] == 'DEFAULT-NOT-A-PART' for t in texts)
    assert any(t['plain_text'] == 'DEFAULT-NOT-A-PART' for t in analysis['block_definition_texts'])
    structure = analysis['drawing_structure']
    assert structure['classification'] == 'B'
    assert structure['frame_count'] == 2
    assert structure['unique_drawing_number_count'] == 1
    assert structure['page_groups_complete']
    assert structure['frames'][0]['bounds'] == [0.0, 0.0, 200.0, 140.0]


def test_dimension_tolerance_override_active_and_geometry_mismatch():
    doc = ezdxf.new('R2018')
    dim = doc.modelspace().add_linear_dim(base=(0, 4), p1=(0, 0), p2=(10, 0),
        text='20.00', override={'dimtol': 1, 'dimtp': 0.01, 'dimtm': 0.002, 'dimdec': 2, 'dimtdec': 3})
    dim.render()
    result = analyze_document(doc)['dimensions'][0]
    assert result['tolerance']['active']
    assert result['tolerance']['lower_deviation'] == -0.002
    assert result['tolerance']['upper_magnitude'] == 0.01
    assert result['precision']['linear'] == 2
    assert result['geometric_scalar'] == 10
    assert result['explicit_override_numeric_value'] == 20
    assert result['numeric_override_vs_geometry_mismatch']
    assert result['annotation_visible_texts']


def test_gdt_raw_is_preserved_and_inactive_tolerance_not_applied():
    doc = ezdxf.new('R2018')
    raw = '.XX \\Fgdt;{\\Fgdt;`}\\Ftxt; 0.01'
    doc.modelspace().add_mtext(raw)
    dim = doc.modelspace().add_linear_dim(base=(0, 4), p1=(0, 0), p2=(10, 0), override={'dimtol': 0, 'dimtp': 0.1})
    dim.render()
    result = analyze_document(doc)
    text = next(t for t in result['texts'] if t['raw_text'] == raw)
    assert text['glyph_decoding_required']
    assert '0.01' in text['tolerance_search_matches']
    assert '±' not in text['tolerance_search_matches']
    assert not result['dimensions'][0]['tolerance']['active']


def test_clustering_and_incomplete_rectangles():
    result = cluster_points([(0, 0), (0.1, 0), (5, 0)], 0.2, min_points=2)
    assert result['cluster_count'] == 1 and result['noise_count'] == 1
    doc = ezdxf.new()
    space = doc.modelspace()
    space.add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)])
    assert not detect_rectangles(space)
    space.add_lwpolyline([(20, 0), (30, 0), (30, 5), (20, 5)], close=True)
    assert detect_rectangles(space)[0]['bounds'] == [20, 0, 30, 5]


def test_fit_codes_following_sizes_and_numeric_token_boundaries():
    assert token_matches('10H7 8h7 0.0025') == ['H7', 'h7']
    assert 'H7' not in token_matches('AH7 H70')
    assert token_matches('±0.01 0.002') == ['±', '0.01', '0.002']


def test_bad_dxf_isolated_and_raw_outputs_rejected(tmp_path):
    (tmp_path / 'bad.dxf').write_text('invalid')
    doc = ezdxf.new()
    doc.modelspace().add_text('材料')
    doc.saveas(tmp_path / 'good.dxf')
    result = analyze_files(tmp_path)
    assert result['summary']['status_counts'] == {'error': 1, 'parsed': 1}
    assert result['summary']['all_sources_unchanged']
    with pytest.raises(SystemExit) as error:
        main(['--raw-dir', str(tmp_path), '--output', str(tmp_path / 'bad-output.json')])
    assert error.value.code == 2
    assert not (tmp_path / 'bad-output.json').exists()


def test_customer_table_material_hardness_and_world_positions():
    from pathlib import Path
    from cad_inventory.parsers import read_dxf
    path = Path(__file__).resolve().parents[1] / 'data/raw/smx0100-m26a-a-DXF/smx0100-m24a-a.dxf'
    if not path.exists():
        pytest.skip('Customer sample is not available in this checkout')
    doc, _ = read_dxf(path)
    result = analyze_document(doc)
    assert not result['warnings']
    table_texts = [t for t in result['texts'] if t['scope'] == 'table_graphic']
    assert any(t['plain_text'] == 'SKD61' for t in table_texts)
    assert any(t['plain_text'] == 'HRC50-52' for t in table_texts)
    assert any('SMX0100 - M24A' in t['plain_text'] for t in table_texts)
    material = next(t for t in table_texts if t['plain_text'] == 'SKD61')
    table = next(t for t in doc.modelspace().query('ACAD_TABLE') if t.dxf.geometry in material['block_path'][0])
    assert material['position'][0] == pytest.approx(material['definition_position'][0] + table.dxf.insert.x)
    assert material['position'][1] == pytest.approx(material['definition_position'][1] + table.dxf.insert.y)
