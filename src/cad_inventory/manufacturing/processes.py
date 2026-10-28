"""Preserve original operation occurrences, sequence, and evidence-based dependencies."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import math
import re
import statistics


def normalized_id(value):
    """Only normalize case/whitespace, never strip *, =, - or version suffixes."""
    return re.sub(r'\s+', '', str(value or '')).upper()


def parse_time(value):
    if value is None or not str(value).strip():
        return None, 'missing'
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        return None, 'non_numeric'
    if not result.is_finite():
        return None, 'non_finite'
    return result, 'negative' if result < 0 else 'zero' if result == 0 else 'positive'


def quantiles(numbers):
    if not numbers:
        return None
    points=sorted(numbers)
    def q(f):
        i=(len(points)-1)*f; lo=math.floor(i);hi=math.ceil(i)
        return points[lo]+(points[hi]-points[lo])*(i-lo)
    q1,q3=q(.25),q(.75)
    return {'count':len(points),'min':points[0],'q1':q1,'median':q(.5),'q3':q3,'p95':q(.95),
            'max':points[-1],'numeric_sum':float(sum(Decimal(str(x)) for x in points)),
            'upper_iqr_fence':q3+1.5*(q3-q1)}


def extract_operations(workbook, dictionary):
    lookup=defaultdict(list)
    for entry in dictionary['entries']:
        lookup[entry['code']].append(entry)
    records, sheets = [], []
    expected=['模具内部编号','件号','零件名称','工序名称','工时定额']
    for sheet in workbook['sheets']:
        header = next((row for row in sheet['rows'] if [c['raw_value'] for c in row['cells']][:5]==expected),None)
        if header is None:
            raise ValueError(f"Expected process header missing in {workbook['source_file']}:{sheet['sheet']}")
        headers=[c['raw_value'] for c in header['cells']]
        n=0
        for row in sheet['rows']:
            if row['row_number']<=header['row_number']:
                continue
            raw=[c['raw_value'] for c in row['cells']]
            if not any(str(v).strip() for v in raw if v is not None):
                continue
            if len(raw)<5:
                raise ValueError('Process record has fewer than five columns')
            parsed,status=parse_time(raw[4]); part=normalized_id(raw[1]);code=str(raw[3]).strip()
            mapped=lookup.get(code,[])
            records.append({'record_id':f"{str(workbook['source_file']).split('/')[-1]}::{sheet['sheet']}::R{row['row_number']}",
                            'source_file':workbook['source_file'],'sheet':sheet['sheet'],'row_number':row['row_number'],
                            'raw_fields':dict(zip(headers,raw)),'raw_cells':row['cells'],
                            'mold_id':normalized_id(raw[0]),'part_id':part,'part_id_raw':raw[1],
                            'part_name_raw':raw[2],'process_code':code,'time_raw':raw[4],
                            'time_numeric':float(parsed) if parsed is not None else None,
                            'time_decimal':str(parsed) if parsed is not None else None,'time_status':status,
                            'time_field_name':'工时定额','unit_raw':None,'full_table_unit_confirmed':False,
                            'time_nature':'quota; actual_processing_time_unconfirmed',
                            'is_electrode_branch':'=' in part,
                            'parent_part_id_candidate':part.split('=',1)[0] if '=' in part else None,
                            'process_dictionary_matches':mapped,
                            'process_mapping_status':'mapped' if len(mapped)==1 else 'unmapped' if not mapped else 'ambiguous'})
            n+=1
        sheets.append({'sheet':sheet['sheet'],'header_row':header['row_number'],'columns':headers,
                       'physical_rows':sheet['row_count'],'data_record_count':n,
                       'column_storage_types':{str(column_name):dict(Counter(row['cells'][i]['storage_type'] for row in sheet['rows'] if row['row_number']>header['row_number'])) for i,column_name in enumerate(headers)}})
    return records,sheets


def build_routes(records):
    """Keep every operation occurrence in original sheet row order."""
    grouped=defaultdict(list)
    for record in records:
        grouped[(record['mold_id'],record['part_id'])].append(record)
    routes=[]
    for (mold,part),items in grouped.items():
        sources={(r['source_file'],r['sheet']) for r in items}
        # Within one sheet row numbers establish order. Cross-sheet order is unknown.
        if len(sources)==1:
            items=sorted(items,key=lambda r:r['row_number'])
        operations=[]
        for i,record in enumerate(items,1):
            operations.append({**record,'sequence_in_part':i,'sequence_basis':'PPT slide 4 + original worksheet row order' if len(sources)==1 else 'cross-sheet order unresolved'})
        signatures=Counter((r['process_code'],r['time_decimal'],r['part_name_raw']) for r in items)
        routes.append({'mold_id':mold,'part_id':part,'is_electrode_branch':'=' in part,
                       'parent_part_id_candidate':part.split('=',1)[0] if '=' in part else None,
                       'operation_count':len(items),'operation_codes':[r['process_code'] for r in items],
                       'source_rows':[r['row_number'] for r in items],
                       'source_order_restorable':len(sources)==1,
                       'contiguous_original_rows':len(sources)==1 and all(b['row_number']==a['row_number']+1 for a,b in zip(items,items[1:])),
                       'repeated_identical_operation_extra_occurrences':sum(n-1 for n in signatures.values() if n>1),
                       'numeric_time_sum':float(sum(Decimal(r['time_decimal']) for r in items if r['time_decimal'] is not None)),
                       'operations':operations})
    return routes


def profile(records,routes,sheets):
    vals=[r['time_numeric'] for r in records if r['time_numeric'] is not None]
    stats=quantiles(vals)
    by_code=defaultdict(list)
    for record in records:
        if record['time_numeric'] is not None:
            by_code[record['process_code']].append(record['time_numeric'])
    code_stats={code:quantiles(numbers) for code,numbers in sorted(by_code.items())}
    outliers=[]
    for record in records:
        value=record['time_numeric']; reasons=[]
        if record['time_status'] in {'missing','negative','non_numeric','non_finite','zero'}:
            reasons.append(record['time_status'])
        if value is not None and stats and value>stats['upper_iqr_fence']:
            reasons.append('above_global_IQR_fence')
        local=code_stats.get(record['process_code'])
        if value is not None and local and local['count']>=4 and value>local['upper_iqr_fence']:
            reasons.append('above_process_IQR_fence')
        if reasons:
            outliers.append({'record_id':record['record_id'],'part_id':record['part_id'],
                             'process_code':record['process_code'],'row_number':record['row_number'],
                             'time_raw':record['time_raw'],'reasons':reasons})
    ids=Counter(r['part_id'] for r in records)
    return {'record_count':len(records),'unique_part_count':len(ids),'primary_part_count':sum('=' not in p for p in ids),
            'electrode_part_count':sum('=' in p for p in ids),'part_record_counts':dict(sorted(ids.items())),
            'process_code_counts':dict(sorted(Counter(r['process_code'] for r in records).items())),
            'code_mapping_counts':dict(Counter(r['process_mapping_status'] for r in records)),
            'unmapped_codes':sorted({r['process_code'] for r in records if r['process_mapping_status']!='mapped'}),
            'time_status_counts':dict(Counter(r['time_status'] for r in records)),
            'mold_id_counts':dict(Counter(r['mold_id'] for r in records)),
            'field_missing_counts':{field:sum(r['raw_fields'].get(field) is None or not str(r['raw_fields'].get(field,'')).strip() for r in records) for field in records[0]['raw_fields']} if records else {},
            'time_distribution':stats,'per_process_time_distribution':code_stats,'anomalies':outliers,
            'identical_row_extra_occurrences':sum(n-1 for n in Counter(tuple(r['raw_fields'].values()) for r in records).values() if n>1),
            'parts_with_multiple_operations':sum(r['operation_count']>1 for r in routes),
            'all_route_orders_restorable':all(r['source_order_restorable'] for r in routes),
            'noncontiguous_routes':[r['part_id'] for r in routes if not r['contiguous_original_rows']],
            'sheet_profiles':sheets,'time_unit':'not explicitly provided in workbook; h only demonstrated in PPT F20A example',
            'actual_processing_time_confirmed':False,
            'outlier_method':'linear-interpolated Q3+1.5*IQR globally and within process when n>=4; candidate only, no removal'}


def build_electrode_graph(routes,parent_id,ppt_confirmed=False):
    lookup={r['part_id']:r for r in routes};parent=lookup.get(parent_id)
    branches=[r for r in routes if r['parent_part_id_candidate']==parent_id]
    nodes=[];edges=[];ledger=[]
    if parent is None:
        return {'parent_id':parent_id,'status':'parent_missing','nodes':[],'edges':[],'ledger':[]}
    for route in [parent]+branches:
        for op in route['operations']:
            role='electrode_discharge' if route['is_electrode_branch'] and op['process_code']=='EDM2' else 'electrode_manufacture' if route['is_electrode_branch'] else 'body_operation'
            nodes.append({'id':op['record_id'],'part_id':route['part_id'],'sequence':op['sequence_in_part'],
                          'process_code':op['process_code'],'time_raw':op['time_raw'],'time_numeric':op['time_numeric'],
                          'source_file':op['source_file'],'sheet':op['sheet'],'row_number':op['row_number'],'role':role})
            ledger.append({'record_id':op['record_id'],'part_id':route['part_id'],'row_number':op['row_number'],
                           'process_code':op['process_code'],'time_numeric':op['time_numeric'],'role':role,
                           'cost_owner':'unconfirmed','time_nature':op['time_nature']})
        for a,b in zip(route['operations'],route['operations'][1:]):
            edges.append({'from':a['record_id'],'to':b['record_id'],'relation':'source_sequence',
                          'evidence':'PPT slide 4; worksheet row order','confirmed_for_case':ppt_confirmed})
    parent_edm=[op for op in parent['operations'] if op['process_code']=='EDM2']
    endpoints=[]
    for branch in branches:
        manufacturing=[op for op in branch['operations'] if op['process_code']!='EDM2']
        discharge=[op for op in branch['operations'] if op['process_code']=='EDM2']
        if manufacturing:
            endpoints.append(manufacturing[-1])
        for event in discharge:
            for aggregate in parent_edm:
                edges.append({'from':event['record_id'],'to':aggregate['record_id'],'relation':'discharge_detail_of',
                              'evidence':'PPT slide 5 explicit F20A explanation' if ppt_confirmed else 'identifier prefix + EDM2 co-occurrence; confirm with engineer',
                              'confirmed_for_case':ppt_confirmed})
    # The PPT arrows join electrode CNC outputs into the BODY EDM2 stage.
    for endpoint in endpoints:
        for aggregate in parent_edm:
            edges.append({'from':endpoint['record_id'],'to':aggregate['record_id'],'relation':'electrode_ready_for_EDM2',
                          'evidence':'PPT slide 5 diagram' if ppt_confirmed else 'candidate prefix association',
                          'confirmed_for_case':ppt_confirmed})
    distinct={r['record_id']:r for r in ledger}
    if len(distinct)!=len(ledger):
        raise ValueError('Duplicate source occurrence in dependency ledger')
    def subtotal(role):
        return float(sum(Decimal(str(r['time_numeric'])) for r in ledger if r['role']==role and r['time_numeric'] is not None))
    return {'parent_id':parent_id,'status':'PPT-verified relationship; cost attribution pending' if ppt_confirmed else 'candidate; engineer confirmation needed',
            'electrode_part_ids':[r['part_id'] for r in branches], 'nodes':nodes,'edges':edges,'ledger':ledger,
            'branch_completeness':[{'electrode_part_id':branch['part_id'],
                                    'has_manufacturing_record':any(op['process_code']!='EDM2' for op in branch['operations']),
                                    'has_discharge_record':any(op['process_code']=='EDM2' for op in branch['operations']),
                                    'source_rows':branch['source_rows']} for branch in branches],
            'body_numeric_sum':subtotal('body_operation'),'electrode_manufacture_numeric_sum':subtotal('electrode_manufacture'),
            'electrode_discharge_numeric_sum':subtotal('electrode_discharge'),
            'distinct_source_record_numeric_sum':float(sum(Decimal(str(r['time_numeric'])) for r in ledger if r['time_numeric'] is not None)),
            'source_record_count':len(ledger),'double_counted_source_records':len(ledger)-len(distinct),
            'unit_evidence':'PPT slide 5 explicitly uses h for F20A' if ppt_confirmed else 'workbook unit not explicit',
            'constraints':['Source sequence edges are precedence; discharge_detail_of edges are aggregation, not chronological precedence.',
                           'Do not add a recomputed body EDM2 subtotal to the original electrode EDM2 rows.',
                           'Electrode1/2 discharge scheduling, reuse, quantity, setup and cost ownership require engineer confirmation.',
                           'No actual-time label is established by the PPT.']}
