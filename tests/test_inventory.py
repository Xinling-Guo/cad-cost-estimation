from pathlib import Path
import hashlib

import ezdxf
import pytest

from cad_inventory.pipeline import main, scan
from cad_inventory.parsers import MissingConverterError


def test_dxf_features_and_original_preserved(tmp_path):
    doc = ezdxf.new('R2010')
    doc.layers.new('PART')
    space = doc.modelspace()
    space.add_line((0, 0), (10, 0), dxfattribs={'layer': 'PART'})
    space.add_circle((0, 0), 2)
    space.add_text('一般公差 ±0.01', dxfattribs={'layer': 'PART'})
    space.add_mtext('Drawing notes')
    block = doc.blocks.new('TITLE')
    block.add_text('Tolerance ±0.002')
    block.add_circle((0, 0), 1)
    space.add_blockref('TITLE', (0, 0))
    space.add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0)).render()
    doc.layout().add_text('Paper note')
    path = tmp_path / 'sample.dxf'
    doc.saveas(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = scan(tmp_path)['files'][0]
    assert result['status'] == 'parsed'
    features = result['features']
    assert features['dimension_count'] == 1
    assert features['block_count'] == 1
    assert features['geometry_count'] == 2
    assert features['entity_type_counts']['TEXT'] == 2
    assert 'PART' in {layer['name'] for layer in features['layers']}
    assert any(t['scope'] == 'block:TITLE' for t in features['tolerance_text_candidates'])
    assert any(t['text'] == 'Paper note' for t in features['texts'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_bad_file_does_not_stop_scan_and_step_is_only_inspected(tmp_path):
    (tmp_path / 'bad.dxf').write_text('invalid')
    (tmp_path / 'notes.txt').write_text('note')
    (tmp_path / 'part.STP').write_text("ISO-10303-21; HEADER; FILE_SCHEMA(('AUTOMOTIVE_DESIGN')); ENDSEC; DATA; #1=MANIFOLD_SOLID_BREP('a',#2); #2=ADVANCED_FACE('',(),#3,.T.); ENDSEC; END-ISO-10303-21;")
    result = scan(tmp_path)
    assert result['summary']['total_files'] == 3
    assert result['summary']['status_counts'] == {'error': 1, 'inspected': 1, 'unsupported': 1}
    part = next(f for f in result['files'] if f['type'] == 'step')
    assert part['features']['solid_declaration_count'] == 1
    assert part['features']['geometry_validated'] is False


def test_missing_converter_is_blocked(tmp_path, monkeypatch):
    (tmp_path / 'part.dwg').write_bytes(b'AC1027some data')
    monkeypatch.setattr('cad_inventory.parsers.odafc.is_installed', lambda: False)
    result = scan(tmp_path)['files'][0]
    assert result['status'] == 'blocked'
    assert result['features'] is None
    assert result['errors'][0]['code'] == MissingConverterError.__name__


def test_outputs_cannot_target_raw(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(['--raw-dir', str(tmp_path), '--output', str(tmp_path / 'report.json')])
    assert error.value.code == 2
    assert not (tmp_path / 'report.json').exists()


def test_symlink_not_read(tmp_path):
    (tmp_path / 'external.dxf').symlink_to('/etc/passwd')
    result = scan(tmp_path)['files'][0]
    assert result['status'] == 'error'
    assert 'size_bytes' not in result
