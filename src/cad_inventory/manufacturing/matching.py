"""Drawing-ID joins, strict punctuation preservation, and CAD duplicate evidence."""
from collections import defaultdict, Counter
import hashlib
import json
from pathlib import Path
import re

from ezdxf.lldxf.tagwriter import TagCollector

from ..deep_analysis import walk_entities, text_record
from ..parsers import read_dxf
from ..pipeline import sha256
from ..deep_spatial import detect_rectangles
from .processes import normalized_id


def semantic_fingerprint(doc):
    """Compare CAD entities/blocks, excluding handles and proxy display cache.

    This is a structured-content comparison, not a proof of bytewise equality or
    visual equivalence. Header timestamps and OBJECTS dictionaries are excluded.
    """
    content=[]
    for block in sorted(doc.blocks,key=lambda b:b.name):
        tags=[]
        for entity in block:
            tags.append([(tag.code,str(tag.value)) for tag in TagCollector.dxftags(entity)
                         if tag.code not in {5,105,330,331,340,350,360,1005,310}])
        content.append([block.name,tags])
    styles=[]
    for collection in (doc.layers,doc.styles,doc.dimstyles):
        styles.append([sorted((k,str(v)) for k,v in entry.dxf.all_existing_dxf_attribs().items() if k not in {'handle','owner'}) for entry in collection])
    return hashlib.sha256(json.dumps([content,styles],ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def cad_index(root):
    raw=root/'data/raw'
    manifest=[];hashed=defaultdict(list);drawing_sources=defaultdict(list);canonical={};errors=[]
    paths=sorted(p for p in raw.rglob('*') if p.is_file() and p.suffix.lower() in {'.dwg','.dxf','.stp','.step'})
    for path in paths:
        if path.is_symlink():
            errors.append({'source_file':path.relative_to(root).as_posix(),'error':'symlink skipped'})
            continue
        entry={'source_file':path.relative_to(root).as_posix(),'extension':path.suffix.lower(),
               'size_bytes':path.stat().st_size,'sha256':sha256(path)}
        manifest.append(entry);hashed[entry['sha256']].append(entry['source_file'])
    # Exact duplicate DXF files are read once, aliases retained.
    for entry in manifest:
        if entry['extension']!='.dxf':
            continue
        key=entry['sha256']
        if key in canonical:
            for item in canonical[key]:
                drawing_sources[item['drawing_number']].append({**item,'source_file':entry['source_file'],'duplicate_of':item['source_file'],'duplicate_type':'byte_identical'})
            continue
        path=root/entry['source_file']
        try:
            doc,parser=read_dxf(path)
            items=[]
            if path.name.startswith('CMY1000A'):
                for layout in doc.layouts:
                    detail_inserts=[e for e in layout.query('INSERT') if any(a.dxf.tag=='PARTNO' for a in e.attribs)]
                    for insert in layout.query('INSERT'):
                        attrs={a.dxf.tag:a for a in insert.attribs}
                        if 'DWG-NO:' not in attrs:
                            continue
                        raw_id=attrs['DWG-NO:'].dxf.text
                        drawing=normalized_id(raw_id)
                        item={'drawing_number':drawing,'mold_id':'CMY1000A','source_file':entry['source_file'],
                              'source_sha256':key,'layout':layout.name,'insert_handle':insert.dxf.handle,
                              'identity_evidence':{'raw_text':raw_id,'handle':attrs['DWG-NO:'].dxf.handle,'attribute_tag':'DWG-NO:',
                                                   'position':list(attrs['DWG-NO:'].dxf.insert)},
                              'sheet':attrs['SHEET'].dxf.text if 'SHEET' in attrs else None,
                              'revision':attrs['REV'].dxf.text if 'REV' in attrs else None,
                              'dedup_basis':'drawing number + explicit revision; preserve pages'}
                        rectangles=detect_rectangles(doc.blocks.get(insert.dxf.name,[]))
                        details=[]
                        if rectangles:
                            bounds=rectangles[0]['bounds'];inverse=insert.matrix44();inverse.inverse()
                            for detail in detail_inserts:
                                location=inverse.transform(detail.dxf.insert)
                                if bounds[0]-1e-5<=location.x<=bounds[2]+1e-5 and bounds[1]-1e-5<=location.y<=bounds[3]+1e-5:
                                    for attribute in detail.attribs:
                                        if attribute.dxf.tag!='PARTNO':
                                            continue
                                        raw_number=attribute.dxf.text.strip()
                                        short=normalized_id(raw_number)
                                        full=short if short.startswith('CMY1000A-') else 'CMY1000A-'+short
                                        details.append({'part_id':full,'raw_part_number':raw_number,'attribute_tag':'PARTNO',
                                                        'attribute_handle':attribute.dxf.handle,'detail_insert_handle':detail.dxf.handle,
                                                        'position':list(attribute.dxf.insert),'frame_insert_handle':insert.dxf.handle,
                                                        'qualification_rule':'explicit PARTNO value + mold prefix from enclosing frame drawing ID',
                                                        'suffix_expansion_performed':False})
                        item['part_number_evidence']=details
                        items.append(item);drawing_sources[drawing].append(item)
            else:
                texts=[];warnings=[]
                for layout in doc.layouts:
                    context={'layout':layout.name,'scope':'layout','block_path':[],'effective_layer':'0'}
                    for entity,ctx in walk_entities(layout,doc,context,warnings):
                        if entity.dxftype() in {'TEXT','MTEXT','ATTRIB'}:
                            texts.append(text_record(entity,ctx,doc))
                evidence=[]
                for text in texts:
                    if 'DRAWING' not in text['plain_text'].upper():
                        continue
                    for match in re.finditer(r'SMX0100\s*-\s*[A-Z]\d+[A-Z](?:\*)?',text['plain_text'],re.I):
                        evidence.append((normalized_id(match.group()),text))
                ids={drawing for drawing,_ in evidence}
                if len(ids)!=1:
                    raise ValueError(f'Expected one title-block drawing ID, found {sorted(ids)}')
                drawing=next(iter(ids));text=next(t for k,t in evidence if k==drawing)
                item={'drawing_number':drawing,'mold_id':'SMX0100','source_file':entry['source_file'],'source_sha256':key,
                      'identity_evidence':{'raw_text':text['raw_text'],'handle':text['source_handle'],
                                           'layout':text['layout'],'position':text['position'],'block_path':text['block_path']},
                      'revision':None,'sheet':None,'filename_is_join_key':False,
                      'structured_fingerprint':semantic_fingerprint(doc),
                      'engineering_evidence':[{'raw_text':t['raw_text'],'plain_text':t['plain_text'],'source_handle':t['source_handle'],
                                               'position':t['position'],'scope':t['scope'],'block_path':t['block_path']}
                                              for t in texts if t['keyword_matches'] or 'DRAWING NO' in t['plain_text'].upper() or 'PART NO' in t['plain_text'].upper()],
                      'warnings':warnings,'parser_audit':parser['audit']}
                items.append(item);drawing_sources[drawing].append(item)
            canonical[key]=items
        except Exception as error:
            errors.append({'source_file':entry['source_file'],'error':str(error)})
    drawings=[]
    for drawing,sources in sorted(drawing_sources.items()):
        distinct_hashes={s['source_sha256'] for s in sources}
        fingerprints={s.get('structured_fingerprint') for s in sources if s.get('structured_fingerprint')}
        drawings.append({'drawing_number':drawing,'mold_id':sources[0]['mold_id'],'sources':sources,
                         'cad_part_ids':sorted({e['part_id'] for s in sources for e in s.get('part_number_evidence',[])}) if sources[0]['mold_id']=='CMY1000A' else [drawing],
                         'source_file_count':len({s['source_file'] for s in sources}),
                         'page_count':len({(s.get('sheet'),s.get('revision')) for s in sources}) if sources[0]['mold_id']=='CMY1000A' else None,
                         'distinct_file_hash_count':len(distinct_hashes),
                         'same_drawing_multiple_exports':len(distinct_hashes)>1,
                         'structured_content_agrees':len(fingerprints)==1 if fingerprints else None,
                         'version_alignment_confirmed':False})
    overlap=[]
    new_dir='data/raw/SMX0100图档18张';new_dxf='data/raw/SMX0100图档18张_dxf'
    old_dirs=('data/raw/smx0100-m26a-a/','data/raw/smx0100-m26a-a-DXF/')
    for group in hashed.values():
        old=[p for p in group if p.startswith(old_dirs)]
        new=[p for p in group if p.startswith(new_dir+'/') or p.startswith(new_dxf+'/')]
        if old and new:
            overlap.append({'old_files':old,'new_files':new,'sha256':sha256(root/old[0]),'evidence':'byte_identical'})
    return {'cad_files':manifest,'exact_duplicate_groups':[{'sha256':k,'files':v} for k,v in hashed.items() if len(v)>1],
            'drawings':drawings,'old_new_exact_overlaps':overlap,'errors':errors,
            'counting_policy':'unique title-block drawing IDs; file hashes deduplicate identical exports; versions remain unverified',
            'fingerprint_scope':'entities/blocks/layers/text styles/dimension styles; excludes handles, header timestamps, proxy code310 and OBJECTS; no visual-equivalence claim'}


def match_family(family,cad,routes):
    drawings=[d for d in cad['drawings'] if d['mold_id']==family]
    cad_lookup={d['drawing_number']:d for d in drawings}
    routes=[r for r in routes if r['mold_id']==family]
    route_lookup={r['part_id']:r for r in routes}
    primary={r['part_id'] for r in routes if not r['is_electrode_branch']}
    electrodes={r['part_id'] for r in routes if r['is_electrode_branch']}
    cad_ids=set(cad_lookup);all_ids=set(route_lookup);exact=cad_ids&all_ids
    candidates=[]
    for part in sorted(primary-cad_ids):
        for drawing in sorted(cad_ids-primary):
            if part.replace('*','')==drawing.replace('*','') and part!=drawing:
                candidates.append({'excel_part_id':part,'cad_drawing_number':drawing,'rule':'only differs by *; requires explicit customer confirmation',
                                   'confirmed':False,'source_records':[op['record_id'] for op in route_lookup[part]['operations']]})
    matched=[]
    for part in sorted(exact):
        matched.append({'part_id':part,'match_type':'strict normalized drawing-ID equality; case/whitespace only',
                        'cad_sources':cad_lookup[part]['sources'],'process_record_ids':[o['record_id'] for o in route_lookup[part]['operations']],
                        'process_source_rows':route_lookup[part]['source_rows'],'route':route_lookup[part],
                        'version_alignment_confirmed':False})
    evidence_lookup=defaultdict(list)
    for drawing in drawings:
        for part in drawing.get('cad_part_ids',[drawing['drawing_number']]):
            evidence_lookup[part].append(drawing)
    evidence_ids=set(evidence_lookup)
    evidence_exact=evidence_ids&primary
    evidence_matches=[]
    for part in sorted(evidence_exact):
        evidence_matches.append({'part_id':part,'cad_drawing_numbers':[d['drawing_number'] for d in evidence_lookup[part]],
                                 'match_type':'explicit frame PARTNO + mold context' if family=='CMY1000A' else 'title-block drawing ID',
                                 'cad_sources':[source for d in evidence_lookup[part] for source in d['sources']],
                                 'source_record_ids':[o['record_id'] for o in route_lookup[part]['operations']],
                                 'source_rows':route_lookup[part]['source_rows'],'version_alignment_confirmed':False})
    conflicts=[]
    for drawing in drawings:
        detail=set(drawing.get('cad_part_ids',[]))
        if drawing['drawing_number'] in primary and detail and drawing['drawing_number'] not in detail:
            conflicts.append({'drawing_number':drawing['drawing_number'],'detail_part_ids':sorted(detail),
                              'detail_ids_found_in_excel':sorted(detail&primary),'cad_sources':drawing['sources'],
                              'status':'title/detail identity disagreement; requires engineer confirmation'})
    combined_ids=(cad_ids|evidence_ids)&primary
    conflict_ids={c['drawing_number'] for c in conflicts}|{p for c in conflicts for p in c['detail_part_ids'] if p in primary}
    branch_matches=[]
    for part in sorted(electrodes):
        parent=part.split('=',1)[0]
        branch_matches.append({'electrode_part_id':part,'parent_id_from_prefix':parent,
                               'parent_exists_in_excel':parent in primary,'parent_exact_cad_match':parent in cad_ids,
                               'parent_evidence_cad_match':parent in evidence_ids,
                               'relation_status':'PPT verified' if parent=='SMX0100-F20A' else 'prefix-based candidate; engineer confirmation required',
                               'source_rows':route_lookup[part]['source_rows']})
    def ratio(n,d):return round(100*n/d,4) if d else None
    return {'mold_id':family,'mold_type':'冲压模具' if family=='CMY1000A' else '注塑模具',
            'cad_unique_drawing_count':len(cad_ids),'excel_unique_part_count':len(all_ids),'excel_primary_part_count':len(primary),
            'excel_electrode_part_count':len(electrodes),'strict_matched_primary_count':len(exact&primary),
            'cad_to_excel_strict_pct':ratio(len(exact),len(cad_ids)),
            'excel_primary_to_cad_strict_pct':ratio(len(exact&primary),len(primary)),
            'excel_all_part_to_cad_direct_pct':ratio(len(exact),len(all_ids)),
            'matched_process_record_count':sum(route_lookup[p]['operation_count'] for p in exact),
            'cad_unique_part_number_count':len(evidence_ids),
            'evidence_matched_primary_count':len(evidence_exact),
            'cad_part_to_excel_evidence_pct':ratio(len(evidence_exact),len(evidence_ids)),
            'excel_primary_to_cad_evidence_pct':ratio(len(evidence_exact),len(primary)),
            'excel_all_part_to_cad_evidence_pct':ratio(len(evidence_exact),len(all_ids)),
            'evidence_matched_process_record_count':sum(route_lookup[p]['operation_count'] for p in evidence_exact),
            'unmatched_cad_part_ids':sorted(evidence_ids-primary),
            'unmatched_excel_primary_after_part_evidence':sorted(primary-evidence_ids),
            'part_number_evidence_matches':evidence_matches,
            'combined_evidence_primary_count':len(combined_ids),
            'combined_evidence_primary_pct':ratio(len(combined_ids),len(primary)),
            'combined_evidence_no_identity_conflict_count':len(combined_ids-conflict_ids),
            'unmatched_excel_primary_after_all_evidence':sorted(primary-combined_ids),
            'identifier_conflicts':conflicts,
            'unmatched_cad_ids':sorted(cad_ids-all_ids),'unmatched_excel_primary_ids':sorted(primary-cad_ids),
            'unmatched_excel_electrode_ids':sorted(electrodes-cad_ids),'unconfirmed_alias_candidates':candidates,
            'strict_matches':matched,'electrode_parent_links':branch_matches,
            'identifier_join_only':True,'route_order_basis':'PPT slide4 + original rows; no global scheduling inference'}
