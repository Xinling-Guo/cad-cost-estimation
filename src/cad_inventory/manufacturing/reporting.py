"""Evidence-based manufacturing, CAD joins, and feasibility reports."""
from collections import Counter
from pathlib import Path

from ..deep_pipeline import table


def pct(value):
    return 'N/A' if value is None else f'{value:.2f}%'


def source_label(record):
    return f"{Path(record['source_file']).name} / {record['sheet']} / 行{record['row_number']}"


def route_table(route):
    return table(['顺序','来源行','工序代码','字典准确说明','工时定额原文'],[
        [o['sequence_in_part'],o['row_number'],o['process_code'],
         '；'.join(d['description_raw'] for d in o['process_dictionary_matches']) or '未映射',o['time_raw']]
        for o in route['operations']])


def manufacturing_report(result):
    lines=['# 历史加工工艺与工时数据分析','',f"分析日期：{result['analysis_date']}（北京时间）。仅本地数据分析，未调用外部大模型，未训练模型或开发 Agent。",'',
           '## 业务说明与证据','',
           '完整读取《机加图档工艺工时说明》6页的文字、表格、图片及连接关系。第3页明确：CMY1000A 为冲压模具、仅有2D；SMX0100 为注塑模具，说明称2D均有对应3D。此前将大图一并视作塑胶模具样本的理解应据此修正。两种模具后续分别验证和建模。',
           '第4页明确图号与件号对应、同件号工序自上至下排列；第5页给出 F20A 主体与两个铜电极的依赖及示例小时数。原始说明、各页完整内容与连接标识存于 `presentation.json`，图片保存在结构化结果子目录。','',
           '## 文件、工作表与字段','']
    for family,data in result['families'].items():
        p=data['profile']
        lines += [f'### {family}（{data["matching"]["mold_type"]}）','']
        for s in p['sheet_profiles']:
            lines += [f"来源：`{data['workbook']['source_file']}`；工作表 `{s['sheet']}`；物理行数 {s['physical_rows']}，表头在第{s['header_row']}行，有效记录 {s['data_record_count']}。第1行为空行。",'',
                      f"原始字段：{s['columns']}。",'',f"存储类型分布：{s['column_storage_types']}。所有工时原文保持不变，单独产生数值解析结果。",'']
        lines += [f"各原始字段空值数：{p['field_missing_counts']}；模具内部编号分布：{p['mold_id_counts']}。",'']
    lines += table(['模具','工序记录','所有件号','主体件号','电极件号','不同工序代码','一件多工序'],[
        [family,d['profile']['record_count'],d['profile']['unique_part_count'],d['profile']['primary_part_count'],d['profile']['electrode_part_count'],len(d['profile']['process_code_counts']),d['profile']['parts_with_multiple_operations']]
        for family,d in result['families'].items()])
    lines += ['', '## 工艺分类字典与覆盖','',
              f"来源：`{result['dictionary_workbook']['source_file']}`，工作表 `机加工(工艺分类)`；{result['dictionary_workbook']['sheets'][0]['row_count']}个物理行、5列，无常规列标题；第4至40行有 {result['dictionary']['entry_count']} 个代码说明。",'',
              'A/B列类别仅按原始合并单元格锚点解析，不将空白行按前一工种填充。第34至40行的类别单元格为空，YJ、DG、HH、PP、JY、DD、RCL 虽有准确代码描述，但类别未给出，不能归入“放电”。CK1/CK2 位于“慢丝/MS”原始分组中，也不自行重分类。','']
    lines += table(['模具','已映射记录/全部','记录覆盖率','已映射代码/全部','未映射代码'],[
        [family,f"{d['profile']['code_mapping_counts'].get('mapped',0)}/{d['profile']['record_count']}",pct(100*d['profile']['code_mapping_counts'].get('mapped',0)/d['profile']['record_count']),
         f"{len(d['profile']['process_code_counts'])-len(d['profile']['unmapped_codes'])}/{len(d['profile']['process_code_counts'])}",d['profile']['unmapped_codes']]
        for family,d in result['families'].items()])
    lines += ['', '字典中的准确名称如下；这些是客户分类，不代表已验证设备、能力或成本费率。','']
    lines += table(['原始行','类别','类别代码','工序代码','原始说明'],[[e['row_number'],e['category'],e['category_code'],e['code'],e['description_raw']] for e in result['dictionary']['entries']])
    lines += ['', '### 工序代码分布','']
    for family,d in result['families'].items():
        lines += [f"- {family}：{d['profile']['process_code_counts']}。"]
    lines += ['', '## 工时性质、分布与异常','',
              '**两个Excel的列名均为“工时定额”，不是实测标签。未提供报工时间、开停机记录、人员、设备、批量或采集方式，因此无法确认其为实际加工工时。** 原需求PDF提到可提供精准工时，不等于本次交付表已证实具备该属性。',
              '表内没有单位列；PPT第5页 F20A 案例明确使用 h。全表是否统一为小时、单件还是批次、是否含准备和检验、热处理是否为等待周期，仍需甲方确认。以下全表统计均为原始定额数值，不折算为实际小时或成本。','']
    lines += table(['模具','有效数值','空/非数值','零值','负值','最小','Q1','中位','Q3','P95','最大','原始数值合计'],[
        [family,p['time_distribution']['count'],p['time_status_counts'].get('missing',0)+p['time_status_counts'].get('non_numeric',0)+p['time_status_counts'].get('non_finite',0),p['time_status_counts'].get('zero',0),p['time_status_counts'].get('negative',0),
         *[round(p['time_distribution'][k],4) for k in ['min','q1','median','q3','p95','max','numeric_sum']]]
        for family,d in result['families'].items() for p in [d['profile']]])
    lines += ['', '异常候选使用全表与同工序内 Q3+1.5×IQR 上界（同工序至少4条）。不剔除或修正记录；长周期热处理、大型零件、准备时间都可能产生高值。完整异常清单及逐工序分布见 JSON。','']
    for family,d in result['families'].items():
        p=d['profile'];zeros=[a for a in p['anomalies'] if 'zero' in a['reasons']]
        high=[a for a in p['anomalies'] if any('IQR' in r for r in a['reasons'])]
        lines += [f"### {family} 异常及重复记录",'',
                  f"零值 {len(zeros)} 条；统计高值候选 {len(high)} 条；完全相同五字段的额外出现次数 {p['identical_row_extra_occurrences']}。重复行保留为独立工序发生，不能默认删除。",'']
        lines += table(['来源行','件号','代码','定额原文','标记'],[[a['row_number'],a['part_id'],a['process_code'],a['time_raw'],a['reasons']] for a in zeros])
        lines += ['', '高值候选示例（至多8条）：','']
        lines += table(['来源行','件号','代码','定额原文','标记'],[[a['row_number'],a['part_id'],a['process_code'],a['time_raw'],a['reasons']] for a in sorted(high,key=lambda a:float(a['time_raw']),reverse=True)[:8]])
    lines += ['', '## 原始工序顺序与典型完整路线','',
              '按PPT第4页说明，按件号保留同工作表原始行序。工序重复、回到同一工种均保留，不按代码排序、不按相同字段去重。恢复的是历史记录路线，未获得机器排程、跨件并行顺序或每次加工的特征位置。','']
    for family,part in [('CMY1000A','CMY1000A-D20C'),('SMX0100','SMX0100-E01A'),('SMX0100','SMX0100-F20A')]:
        route=next(r for r in result['families'][family]['routes'] if r['part_id']==part)
        lines += [f'### {part}', '',f"来源：`{route['operations'][0]['source_file']}` / `{route['operations'][0]['sheet']}`；原始定额数值合计 {route['numeric_time_sum']}。",'']
        lines += route_table(route)
    for family,d in result['families'].items():
        p=d['profile'];lines += ['',f"{family}：全部路线可按单一工作表原行序恢复：{p['all_route_orders_restorable']}；非连续行件号：{p['noncontiguous_routes']}。"]
    lines += ['', '## 电极与放电依赖验证','',
              'F20A 主体 Excel 行28–31：MS2(4)→TG2(2.5)→EDM1(4)→EDM2(0)。电极1行101–103：CNC3(1.5)→CNC3(1.5)→EDM2(3.5)；电极2行104–105：CNC3(0.7)→EDM2(2)。PPT第5页给出相同编号、两段1.5h加工及3.5h/2.0h放电时间，支持该案例的支线解释。', '',
              '```mermaid','flowchart LR',
              ' B1["主体 MS2：4"] --> B2["主体 TG2：2.5"] --> B3["主体 EDM1：4"] --> JOIN["主体 EDM2：原表0，汇合阶段"]',
              ' C11["电极1 CNC3：1.5（行101）"] --> C12["电极1 CNC3：1.5（行102）"] --> E1["电极1 EDM2：3.5（行103）"]',
              ' C21["电极2 CNC3：0.7（行104）"] --> E2["电极2 EDM2：2（行105）"]',
              ' C12 -. 电极就绪 .-> JOIN',' C21 -. 电极就绪 .-> JOIN',
              ' E1 -. 放电明细归入该阶段 .-> JOIN',' E2 -. 放电明细归入该阶段 .-> JOIN',' JOIN --> DONE[主体加工完成]','```','',
              '实线表示原始件号内顺序；虚线分别表示电极就绪条件和放电明细归属，不将汇总关系当作工序先后。两个放电明细的相互顺序、与主体EDM1的具体开工约束及电极复用均待工艺工程师确认。']
    graph=result['f20a_graph']
    lines += ['',f"按不同来源行计一次：主体数值小计 {graph['body_numeric_sum']}，电极CNC加工 {graph['electrode_manufacture_numeric_sum']}，电极放电 {graph['electrode_discharge_numeric_sum']}，合计 {graph['distinct_source_record_numeric_sum']}（PPT该例使用小时）；来源行 {graph['source_record_count']} 条，重复计入 {graph['double_counted_source_records']} 条。",'',
              '**19.7 是本案例原始定额按来源行去重后的工作量合计，不是实际完工时长或确认的主体成本。** 若另外将5.5放电小计回填主体EDM2后再加支线，会重复计时；本次不回填原始0、不决定成本归属。两条1.5 CNC3是PPT支持的两次发生，保持独立。',
              '其他“父件号=...”依赖只生成编号前缀候选，不将后缀数字解释为数量、材料或电极次数；也不将F20A的解释未经审核推广到所有零件。','',
              '### 其他电极支线完整性检查','']
    incomplete=[b for g in result['electrode_graphs'] for b in g['branch_completeness'] if not b['has_manufacturing_record'] or not b['has_discharge_record']]
    lines += table(['电极件号','有制造记录','有放电记录','原始行'],[[b['electrode_part_id'],b['has_manufacturing_record'],b['has_discharge_record'],b['source_rows']] for b in incomplete])
    lines += ['', '缺少某类记录可能涉及电极复用、资料缺失或其他归属方式；不能填零、复制其他件工时或自动补工序。完整候选关系与缺口见 electrode_dependencies.json。','',
              '## 待甲方确认','',
              '1. 两份表的工时定额由谁编制、是否经过审核？能否提供实际报工及设备记录，与定额分列？',
              '2. 全表单位、单件/批次口径、准备/换刀/装夹/检验/等待的包含范围是什么？',
              '3. 主体EDM2为0是否为汇总占位？支线EDM2如何归属主体、是否包含其他准备工作？',
              '4. 星号含义、电极后缀规则、重复工序的具体加工内容，以及仅CNC3而无EDM2的支线如何解释？',
              '5. 热处理72等长值是设备占用、人工工时还是周期？分类表末7项的类别如何定义？',
              '6. 提供图纸版本、工艺版本、数量、毛坯、设备能力、费率与有效期，确认冲压/注塑的独立核价规则。','',
              '结构化结果位于 `data/processed/manufacturing/`：原始工作表、工序记录、路线、统计、CAD证据、关联明细、电极图和输入完整性清单均保留来源。']
    return '\n'.join(lines)+'\n'


def matching_report(result):
    lines=['# CAD 与工艺工时关联验证','',f"日期：{result['analysis_date']}。CMY1000A（冲压）与SMX0100（注塑）分别统计，不计算混合覆盖率。",'',
           '## 关联规则与覆盖率口径','',
           '按PPT第4页以图纸中图号关联Excel件号。仅统一大小写与空白，不删除星号、不切除电极后缀、不从文件名猜图号；也不使用小图里重复出现的产品 PART NO. 作为本次零件键。图号相等只证明编号关联，不证明图纸/工艺版本一致。','']
    lines += table(['模具','CAD不同图号','Excel全部件号','主体','电极','严格匹配主体','CAD→Excel','主体→CAD','全件号直接→CAD'],[
        [family,m['cad_unique_drawing_count'],m['excel_unique_part_count'],m['excel_primary_part_count'],m['excel_electrode_part_count'],m['strict_matched_primary_count'],pct(m['cad_to_excel_strict_pct']),pct(m['excel_primary_to_cad_strict_pct']),pct(m['excel_all_part_to_cad_direct_pct'])]
        for family,d in result['families'].items() for m in [d['matching']]])
    lines += ['', 'CAD→Excel分母是去重图号；主体→CAD分母是Excel非电极件号；全件号直接→CAD包含电极分母。电极无独立图档不能自动算主体缺图，电极前缀关系另报。','']
    lines += ['### 图框零件明细编号的补充证据关联','',
              'CMY图框“图号”与框内明细块 PARTNO 不完全相同。例如图号 P20A 的明细属性直接写 P20A-B，图号 D22A 的明细直接写 D22A-G。按明细块插入点落在对应图框内部建立归属，再结合图框提供的模具编号限定前缀；保留原属性、坐标、实体标识和图框标识。没有把字母区间展开成多个零件，也没有截断后缀。',
              'SMX使用图框 DRAWING NO.；其表中的 PART NO. 为另一产品编号，未用作Excel件号。下面的证据关联仍不等于版本一致性验证。','']
    lines += table(['模具','CAD不同零件编号','有原文证据的主体匹配','CAD零件→Excel','Excel主体→CAD','Excel全件号→CAD'],[
        [family,m['cad_unique_part_number_count'],m['evidence_matched_primary_count'],pct(m['cad_part_to_excel_evidence_pct']),pct(m['excel_primary_to_cad_evidence_pct']),pct(m['excel_all_part_to_cad_evidence_pct'])]
        for family,d in result['families'].items() for m in [d['matching']]])
    lines += ['', '该口径与上表严格图号匹配分开报告，不将候选星号别名纳入已匹配数。','']
    lines += ['### 图号与明细证据合并后的身份冲突检查','']
    lines += table(['模具','图号或明细有编号依据','占Excel主体','其中无图号/明细冲突','图号/明细冲突数','仍无依据的Excel主体'],[
        [family,m['combined_evidence_primary_count'],pct(m['combined_evidence_primary_pct']),m['combined_evidence_no_identity_conflict_count'],len(m['identifier_conflicts']),m['unmatched_excel_primary_after_all_evidence']]
        for family,d in result['families'].items() for m in [d['matching']]])
    lines += ['', '“有编号依据”不等于自动放行。CMY中 D21A 图号与明细 D21AG 不一致，图号可匹配Excel D21A，但该身份冲突需单列确认，不能忽略明细或补写连字符。','']
    for family,d in result['families'].items():
        m=d['matching'];links=m['electrode_parent_links']
        lines += [f'### {family}', '',f"严格匹配涉及工序记录 {m['matched_process_record_count']} 条。",'',
                  f"CAD无严格Excel对应：{m['unmatched_cad_ids']}。",'',
                  f"Excel主体无严格CAD对应：{m['unmatched_excel_primary_ids']}。",'',
                  f"引入原始零件明细证据后，仍未匹配的CAD零件编号：{m['unmatched_cad_part_ids']}；Excel主体件号：{m['unmatched_excel_primary_after_part_evidence']}。有证据关联的主体工序记录：{m['evidence_matched_process_record_count']}。",'',
                  f"Excel电极无直接独立CAD图号：{m['unmatched_excel_electrode_ids']}。",'',
                  f"电极前缀父件在Excel存在：{sum(x['parent_exists_in_excel'] for x in links)}/{len(links)}；父件严格CAD命中：{sum(x['parent_exact_cad_match'] for x in links)}/{len(links)}。除PPT F20A案例外均为待确认候选，不计入直接匹配率。",'']
        lines += [f"使用图框零件明细后，电极前缀父件有CAD原文证据：{sum(x['parent_evidence_cad_match'] for x in links)}/{len(links)}；仍保留父子关系待确认状态。",'']
        if m['identifier_conflicts']:
            lines += table(['图号','明细PARTNO编号','来源图框','状态'],[[c['drawing_number'],c['detail_part_ids'],[s.get('insert_handle') for s in c['cad_sources']],c['status']] for c in m['identifier_conflicts']])
            lines += ['']
        lines += table(['Excel件号','CAD图号候选','证据','已确认'],[[c['excel_part_id'],c['cad_drawing_number'],c['rule'],c['confirmed']] for c in m['unconfirmed_alias_candidates']])
        lines += ['', '两处星号差异须由甲方确认后才能建立受控别名表；当前覆盖率不包含候选放行。','']
    lines += ['## 新旧CAD重叠与去重','',
              '逐文件哈希去重并保留别名；同一图号的不同格式和重复导出不增加零件数。大图按图号组织多页，保留图框、页码和版本证据。','']
    overlaps=result['cad']['old_new_exact_overlaps']
    counts=Counter(Path(x['old_files'][0]).suffix.lower() for x in overlaps)
    lines += [f"新增18组中，旧6组DWG/STP均重复：哈希相同DWG {counts.get('.dwg',0)} 组、STP {counts.get('.stp',0)} 组；DXF字节相同 {counts.get('.dxf',0)} 组。",'']
    special=[d for d in result['cad']['drawings'] if d['mold_id']=='SMX0100' and d['same_drawing_multiple_exports']]
    for d in special:
        lines += [f"`{d['drawing_number']}` 有{d['distinct_file_hash_count']}种文件哈希；结构内容指纹一致：{d['structured_content_agrees']}。按相同图号计一个主体，保留不同导出文件；不宣称字节或完整图面等价，版本一致性待确认。",'']
    lines += ['六张旧/新DXF均不是字节完全相同，但结构内容指纹一致。指纹检查排除了更新时间、标识符、显示缓存和对象字典；例如M24A原文差异包含更新时间、显示缓存及字典顺序。该比较不能替代图面/版本审核。','']
    lines += ['因此SMX0100当前为18个不同主体图号，而非旧6组加新18组得到24件；新增不同主体图号为12个。新增18个主体均有同名DWG/STP文件，但本次没有证明3D几何有效或二维/三维版本完全一致。', '',
              '## 一件多页与一件多工序','',
              'CMY1000A大图保持此前证据：195图框、179图号（167单页、8两页、4三页）；多页在图号层面合并，不能把每页都计算一次完整工艺或成本。SMX0100的18张独立图号按主体去重。','']
    for family,d in result['families'].items():
        lines += [f"- {family}：{d['profile']['parts_with_multiple_operations']}个件号有多条工序；原行序可恢复：{d['profile']['all_route_orders_restorable']}。"]
    lines += ['', '## 典型关联证据','']
    for family,part in [('CMY1000A','CMY1000A-D20C'),('SMX0100','SMX0100-F20A')]:
        m=next(m for m in result['families'][family]['matching']['strict_matches'] if m['part_id']==part)
        lines += [f'### {part}', '',f"Excel：{Path(m['route']['operations'][0]['source_file']).name} / 模具进度查询 / 行{m['process_source_rows']}。",'']
        lines += table(['CAD来源','图号原文','图框/实体','坐标','页码'],[
            [s['source_file'],s['identity_evidence']['raw_text'],s.get('insert_handle',s['identity_evidence'].get('handle')),s['identity_evidence'].get('position'),s.get('sheet')]
            for s in m['cad_sources']])
        lines += ['']+route_table(m['route'])+['']
    lines += ['## 完整关联仍缺少的证据','',
              '工序代码→客户分类说明当前可以关联；图号→件号多数严格匹配。但工艺记录没有图纸修订版次、工艺版本、加工特征位置、设备、批次、数量及费率，因而尚未建立“正确版本CAD→具体加工特征→实际工时→实际成本”的完整业务闭环。',
              '电极关系和一般公差范围需要专业审核。当前不把星号候选、未验证的版本关系或电极前缀自动转换为已确认的生产关联。','',
              f"CAD解析错误：{result['cad']['errors']}。所有逐件关联、未匹配列表、源行/图号证据及去重记录见 `cad_process_matches.json` 和 `cad_index.json`。"]
    return '\n'.join(lines)+'\n'


def feasibility_report(result):
    c=result['families']['CMY1000A'];s=result['families']['SMX0100']
    lines=['# Agent 与自动成本评估可行性更新','',f"日期：{result['analysis_date']}。本次只分析数据，不开发Agent、不训练模型。",'',
           '## 结论与进入下一阶段的建议','',
           '**可以进入限定范围、有人审核的图纸理解与历史工艺检索原型阶段。当前不能据此承诺工艺自动规划、实际工时准确预测、自动成本或端到端自动报价。** 下一阶段先验证信息提取、依据展示、历史路线检索及复杂依赖的人工确认流程。', '',
           '## 分项判断','']
    lines += table(['能力','当前已验证','可以进一步验证','尚不具备的条件'],[
        ['CAD图纸理解Agent','图纸实体/文字可读取；可定位图号与多页；材料/公差候选可提取','构建带原图依据与人工确认的受限原型','完整公差规则、字体/尺度核对、3D有效性和专家标注；尚未验证Agent效果'],
        ['历史工艺路线检索','代码字典与逐件原行序路线可组织，存在可追溯CAD编号关联','按已知件号检索；用经审核的材料/公差等筛选历史案例','相似零件检索质量与跨模具泛化尚未评估'],
        ['工艺推荐模型训练','有历史路线样本，支持整理与人工基线','规则/案例推荐的小范围可行性评测','独立模具和零件家族不足；缺加工特征-工序对应与方案选择依据，不足以证明可训练可靠模型'],
        ['工时预测模型训练','有定额数值，可做定额分布和历史查询','明确标签性质后评估定额估算基线','未确认实际工时、单位/批量口径；缺设备和加工参数、多次实测及独立测试数据'],
        ['自动加工成本计算','能组织原始定额和工艺名称','在工时口径、数量、费率及电极归属确认后验证确定性核算','无费率、单位成本、批量分摊及完整成本口径，目前不能算出可信金额'],
        ['端到端自动报价','本次无已验证端到端能力','先做受控辅助核价、异常转人工','缺前述条件及商务报价规则、验收指标和独立验证，尚不具备放行条件']])
    lines += ['', '## 数据量与标签限制','',
              f"冲压样本：1套CMY1000A，{c['profile']['primary_part_count']}个主体、{c['profile']['electrode_part_count']}个电极、{c['profile']['record_count']}条工序。注塑样本：1套SMX0100，{s['profile']['primary_part_count']}个主体、{s['profile']['electrode_part_count']}个电极、{s['profile']['record_count']}条工序。两类分别分析，不能以合并记录量代表统一模具模型的数据规模。",'',
              '一件多工序、多个相似镶件、多页、两次相同CNC步骤都不是独立图纸训练样本。旧6组与新18组重叠，去重后不增加至24个注塑主体。尚无跨多套独立模具、版本和生产批次的泛化证据。',
              '原始行序能恢复已发生的历史路线，但工序说明未定位到孔、槽、型面等具体加工特征，不能直接推出新零件的最佳工艺。定额不是自动获得的实际加工标签，历史工艺也未附审批/变更记录，不能直接称为完整“正确工艺”标签。',
              '评测需按模具、零件家族及版本分组；同件的重复CAD、多页与相邻版本不能泄漏到训练/测试两侧。对仅一套注塑模具的样本，评测只能说明该样本内表现。','',
              '## 更新后的技术方案','',
              '1. 建立分模具类型的数据目录，保留原件号、原行序、字典说明、CAD证据；星号别名和电极后缀先审核。',
              '2. 建设只读图纸理解与历史路线检索原型，展示材料、公差、图号和工序依据；提供错误纠正与人工确认，不自行发布生产工艺。',
              '3. 以F20A验证“主体路线＋电极支线＋放电明细”表示，使用来源行唯一标识防重复；由工艺工程师确认业务归属和调度含义。',
              '4. 收集经核验的实际报工和费用条件，先建立历史查询/规则基线，并对受限样本做定额与实际工时对照。',
              '5. 独立验证达到约定标准后，再考虑工艺推荐与工时模型；成本核算使用确认的工时和费率，报价流程另行定义审核条件。','',
              '## 进入原型阶段前的核对项','',
              '可以并行开始数据展示与检索原型；涉及未确认字段的功能保持待确认。正式工艺/工时估算依赖以下资料：','',
              '- 星号含义及 S33A/M20A 的受控别名；D21A图号与D21AG明细冲突；图纸版本与工艺版本对应。',
              '- F20A的两个1.5是否为不同装夹/阶段；电极制造与放电时间如何计入主体，是否复用、是否计数量。',
              '- 工时定额来源、审核人、单位、单件/批次口径，及原始实际报工记录。',
              '- 多套冲压/注塑模具，代表性零件家族与难度分布；材料、毛坯、设备、加工参数、数量与质量结果。',
              '- 加工特征与工序关联、路线选择理由、工序前后约束、热处理等待与人工工时的区分。',
              '- 各工序设备/人工费率、有效期、准备时间分摊、外协、材料和税费/利润范围。',
              '- 按任务定义的独立人工基准：关键字段漏检率、检索有效性、工艺可接受度、工时/成本误差及复核率。','',
              '## 主要实施风险及控制','']
    lines += table(['风险','控制'],[
        ['冲压/注塑混用造成错误泛化','分类型组织数据和评价，按工艺专业审核'],
        ['把定额作为实际工时训练','保留标签性质，获得报工后分别评估'],
        ['删星号或电极后缀导致错误关联','严格匹配率独立报告，别名规则人工确认'],
        ['重复步骤删除或支线重复加总','保留来源行；工序发生与汇总关系分开'],
        ['0值占位被当作无需放电','结合电极明细检查；不回填、不自动认定成本归属'],
        ['72等长周期作为机加工占用','按工序解释异常，核对热处理周期/人工工时'],
        ['相似样本、多页与重复导出造成评测虚高','按模具/零件家族/版本去重并分组验证'],
        ['图纸字段可读被当作完整成本闭环','每步设置证据与缺失状态，金额与生产建议须具备完整条件']])
    lines += ['', '## 本次交付与后续里程碑','',
              '本次交付三份报告、完整工作表快照、标准化工序与历史路线、分类字典、CAD关联证据、严格/候选匹配统计、电极依赖图与唯一来源行台账，以及输入完整性复核。',
              '建议下一里程碑为“可审核的图纸理解＋历史路线检索＋电极依赖展示”。验收以正确关联、来源可追溯、无重复计数和异常转人工为主；工时预测与自动核价另设数据准备和独立评估关口。']
    return '\n'.join(lines)+'\n'
