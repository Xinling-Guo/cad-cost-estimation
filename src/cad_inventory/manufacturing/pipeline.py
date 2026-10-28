"""Reproducible local manufacturing/CAD association pipeline."""
from datetime import datetime
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from ..pipeline import ROOT, atomic_write, sha256
from .readers import read_workbook, dictionary_from_workbook, read_presentation
from .processes import extract_operations, build_routes, profile, build_electrode_graph
from .matching import cad_index, match_family
from .reporting import manufacturing_report, matching_report, feasibility_report

LOGGER=logging.getLogger(__name__)


def save_json(path,value):
    atomic_write(path,json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def run(root=ROOT):
    root=Path(root).resolve()
    output=root/'data/processed/manufacturing';reports=root/'reports'
    # Resolve destination roots before writing, and reject redirection into raw/doc.
    for target in [output,reports]:
        resolved=target.resolve()
        if resolved.is_relative_to((root/'data/raw').resolve()) or resolved.is_relative_to((root/'doc').resolve()):
            raise ValueError('Output directory must not point to original data')
    output.mkdir(parents=True,exist_ok=True);reports.mkdir(parents=True,exist_ok=True)
    inputs=sorted(p for folder in [root/'data/raw',root/'doc'] for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
    manifest=[{'source_file':p.relative_to(root).as_posix(),'size_bytes':p.stat().st_size,'sha256_before':sha256(p)} for p in inputs]
    LOGGER.info('Registered %d original files for integrity verification',len(manifest))
    result=None
    try:
        ppt=read_presentation(root/'doc/机加图档工艺工时说明.pptx',output/'presentation/images')
        dictionary_book=read_workbook(root/'data/raw/manufacturing/模具加工工艺分类.xlsx')
        dictionary=dictionary_from_workbook(dictionary_book)
        families={};all_routes=[]
        for family in ['CMY1000A','SMX0100']:
            workbook=read_workbook(root/f'data/raw/manufacturing/{family}-详细工艺工时.xls')
            records,sheets=extract_operations(workbook,dictionary);routes=build_routes(records)
            families[family]={'workbook':workbook,'records':records,'routes':routes,'profile':profile(records,routes,sheets)}
            all_routes+=routes
            LOGGER.info('%s: %s records, %s primary IDs, %s electrodes',family,len(records),families[family]['profile']['primary_part_count'],families[family]['profile']['electrode_part_count'])
        LOGGER.info('Reading CAD drawing identities and checking duplicate exports')
        cad=cad_index(root)
        for family,data in families.items():
            data['matching']=match_family(family,cad,data['routes'])
            LOGGER.info('%s: strict CAD matches %s/%s',family,data['matching']['strict_matched_primary_count'],data['matching']['cad_unique_drawing_count'])
        f20=build_electrode_graph(families['SMX0100']['routes'],'SMX0100-F20A',ppt_confirmed=True)
        graphs=[]
        for family,data in families.items():
            parents=sorted({r['parent_part_id_candidate'] for r in data['routes'] if r['parent_part_id_candidate']})
            graphs.extend(build_electrode_graph(data['routes'],parent,ppt_confirmed=parent=='SMX0100-F20A') for parent in parents)
        result={'schema_version':'1.0','analysis_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                'uses_external_llm':False,'model_training_performed':False,'dictionary_workbook':dictionary_book,
                'dictionary':dictionary,'presentation':ppt,'cad':cad,'families':families,'f20a_graph':f20,'electrode_graphs':graphs}
        save_json(output/'presentation.json',ppt)
        save_json(output/'classification_workbook.json',dictionary_book)
        save_json(output/'process_dictionary.json',dictionary)
        for family,data in families.items():
            save_json(output/f'{family}_workbook.json',data['workbook'])
            save_json(output/f'{family}_operations.json',data['records'])
            save_json(output/f'{family}_routes.json',data['routes'])
            save_json(output/f'{family}_quality_profile.json',data['profile'])
        save_json(output/'cad_index.json',cad)
        save_json(output/'cad_process_matches.json',{family:d['matching'] for family,d in families.items()})
        save_json(output/'electrode_dependencies.json',graphs)
        save_json(output/'SMX0100-F20A_dependency.json',f20)
        save_json(output/'analysis_summary.json',{'schema_version':'1.0','analysis_date':result['analysis_date'],
             'families':{family:{'profile':data['profile'],'matching':{k:v for k,v in data['matching'].items() if k not in {'strict_matches','electrode_parent_links','part_number_evidence_matches'}}} for family,data in families.items()},
             'time_nature':'工时定额; actual processing time not confirmed','f20a_numeric_sum':f20['distinct_source_record_numeric_sum']})
        atomic_write(reports/'manufacturing_data_analysis.md',manufacturing_report(result))
        atomic_write(reports/'cad_process_matching_report.md',matching_report(result))
        atomic_write(reports/'agent_feasibility_update.md',feasibility_report(result))
        LOGGER.info('Saved three reports and manufacturing structured outputs')
    finally:
        for entry in manifest:
            try:
                entry['sha256_after']=sha256(root/entry['source_file'])
                entry['unchanged']=entry['sha256_after']==entry['sha256_before']
            except Exception as error:
                entry['unchanged']=False;entry['verification_error']=str(error)
        integrity={'all_sources_unchanged':all(e['unchanged'] for e in manifest),'source_count':len(manifest),'sources':manifest}
        save_json(output/'source_integrity.json',integrity)
        if not integrity['all_sources_unchanged']:
            raise RuntimeError('Original-file integrity verification failed')
    return result


def main():
    output=ROOT/'data/processed/manufacturing'
    output.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(output/'analysis.log',encoding='utf-8')],force=True)
    try:
        result=run()
        return 1 if result['cad']['errors'] else 0
    except Exception:
        LOGGER.exception('Manufacturing pipeline failed')
        return 1
