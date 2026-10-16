"""Aggregate actual native reports, independent evidence and training provenance.

Requires all feedback-v1 stages, including the separately registered refinement.
"""

import argparse
import collections
import hashlib
import json
import statistics
from pathlib import Path

from flycode.research.identity import file_sha256, implementation_fingerprint

source = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description='Summarize the complete primary and exploratory feedback-v1 experiment')
parser.add_argument('root', type=Path)
root = parser.parse_args().root.expanduser()
registration = json.loads((root / 'registration.json').read_text())
assert implementation_fingerprint() == registration['implementation_sha256']
protocol = source / 'benchmarks/feedback-v1/protocol.json'
assert file_sha256(protocol) == registration['protocol_sha256']
records, teachers, training, report_hashes = [], [], [], {}
for name in ['baseline', 'teacher', *['trained-' + str(s) for s in range(5)], *['refined-' + str(s) for s in range(5)], 'live']:
    path = root / (name + '.json')
    report = json.loads(path.read_text())
    assert report['completed'] and report['source_unchanged']
    report_hashes[name] = file_sha256(path)
    feedback = {row['run_id']: row for row in json.loads((root / 'feedback' / (name + '.json')).read_text())}
    for case in report['cases']:
        row = feedback[case['run_id']]
        metrics = case['metrics']
        step_path = root / name / report['experiment_id'] / 'trajectories' / (case['run_id'] + '.steps.jsonl')
        steps = [json.loads(line) for line in step_path.read_text().splitlines()] if step_path.exists() else []
        record = {key: case[key] for key in ['run_id', 'controller_id', 'seed', 'fixture', 'control', 'error']}
        record.update(phase=name, split=case['fixture_split'], metrics=metrics,
             independent_artifact_pass=row['accepted'], independent_status=row['status'],
             tests_unchanged=row['tests_unchanged'], exact_case_ids_match=row['case_ids_match'],
             expected_case_ids=row['expected_cases'], evidence_id=row.get('verdict_id'),
             strict_agent_completion=bool(metrics and metrics['verified_completion'] and row['accepted']),
             executed_tools=[step['chosen_action']['tool'] for step in steps],
             explicit_stop=bool(steps and steps[-1]['chosen_action']['tool']=='stop'),
             trajectory_sha256=file_sha256(step_path) if step_path.exists() else None,
             checkpoint_sha256=case.get('checkpoint_sha256'), graph_fingerprint=case.get('graph_fingerprint'))
        record['derived_tool_feedback'] = {
            'test_calls':sum(s['chosen_action']['tool']=='run_tests' for s in steps),
            'edit_calls':sum(s['chosen_action']['tool'] in {'edit_file','create_file'} for s in steps),
            'duplicate_tool_argument_calls':len(steps)-len({json.dumps({'tool':s['chosen_action']['tool'],'arguments':s['chosen_action'].get('arguments',{})},sort_keys=True) for s in steps})}
        record['controller_compute'] = {key: value for key, value in (case.get('controller') or {}).items()
                                        if key in {'name','tier','state_dim','telemetry_dim','params','memory_capacity','cost','learning','latency_ms','memory_usage_mb','dynamics'}}
        records.append(record)
        if name == 'live':
            record['model_event_case_index'] = report['cases'].index(case)
        if name == 'teacher' or (name == 'baseline' and case['controller_id'] == 'full' and case['control'] == 'bias' and case['fixture_split'] == 'teacher_toy'):
            teachers.append({'family': 'functional_teacher' if name == 'teacher' else 'full_bias_teacher',
                'run_id':case['run_id'], 'seed':case['seed'], 'fixture':case['fixture'],
                'independent_artifact_pass':row['accepted'],
                'steps':[{'tool':s['chosen_action']['tool'], 'outcome_success':s['outcome']['success'],
                          'recorded_return':s['future_return']} for s in steps]})
for family in ['checkpoints', 'refined-checkpoints']:
    for seed in range(5):
        path = root / family / str(seed)
        t = json.loads((path / 'training.json').read_text())
        training.append({'family':family,'seed':seed,**{k:t[k] for k in ['method','ridge','episodes','steps','dataset_sha256','graph_fingerprint','topology_hash','frozen','trained','selected_dynamics','task_success_improvement','evaluation']},
                         'training_report_sha256':file_sha256(path/'training.json')})

def summarize(rows):
    metrics = [r['metrics'] for r in rows if r['metrics']]
    successful = [r['metrics'] for r in rows if r['strict_agent_completion']]
    return {'cases':len(rows), 'strict_completed':sum(r['strict_agent_completion'] for r in rows),
            'independent_passing_artifacts':sum(r['independent_artifact_pass'] for r in rows),
            'success_rate':sum(r['strict_agent_completion'] for r in rows)/len(rows),
            'mean_tools_all_cases':statistics.mean(m['tool_calls'] for m in metrics) if metrics else None,
            'mean_tools_completed_cases':statistics.mean(m['tool_calls'] for m in successful) if successful else None,
            'mean_wall_time_s':statistics.mean(m['wall_time_s'] for m in metrics) if metrics else None,
            'regressions':sum(m['regressions'] for m in metrics),
            'unsafe_proposals':sum(m['unsafe_proposals'] for m in metrics),
            'model_calls':dict(sum((collections.Counter(m['model_calls']) for m in metrics),collections.Counter())),
            'model_error_cases':sum(bool(m['model_errors']) for m in metrics),
            'setup_errors':sum(r['error'] is not None for r in rows)}
summary={}
for phase in ['offline','live']:
    rows=[r for r in records if (r['phase']=='live' if phase=='live' else r['phase'] not in {'teacher','live'})]
    for controller in sorted({r['controller_id'] for r in rows}):
        group=[r for r in rows if r['controller_id']==controller]
        summary[phase+'/'+controller]={'all':summarize(group),'by_split':{},'by_control':{},'by_seed':{}}
        for field,key in [('split','by_split'),('control','by_control'),('seed','by_seed')]:
            for value in sorted({r[field] for r in group}):
                summary[phase+'/'+controller][key][str(value)] = summarize([r for r in group if r[field]==value])

events=[json.loads(line) for line in (root/'live-model-events.jsonl').read_text().splitlines()]
starts=[e for e in events if e['kind']=='model_start']; ends=[e for e in events if e['kind']=='model_end']
assert len(starts)==len(ends)
active = None
for event in events:
    if event['kind'] == 'model_start':
        assert active is None
        active = event['case_index']
    elif event['kind'] == 'model_end':
        assert active == event['case_index']
        active = None
assert active is None
for record in [r for r in records if r['phase'] == 'live']:
    assert sum(e['case_index'] == record['model_event_case_index'] for e in starts) == sum(record['metrics']['model_calls'].values())
assert len(starts)==sum(sum(r['metrics']['model_calls'].values()) for r in records if r['phase']=='live' and r['metrics'])
assert all(e['data']['model']=='qwen/qwen3.5-9b' and e['data']['role']=='reasoner' for e in starts)
assert len(starts)<=48
assert sum(r['metrics']['tool_calls'] for r in records if r['phase']=='live' and r['metrics'])<=72
assert all(r['tests_unchanged'] and r['exact_case_ids_match'] for r in records)
assert len(records)==296
assert len([r for r in records if r['phase'] not in {'teacher','live'}])==280
assert all(t['task_success_improvement'] is None for t in training)
assert all(t['graph_fingerprint']==registration['graph']['fingerprint'] for t in training)
paired={}
frozen={(r['seed'],r['fixture'],r['control']):r for r in records if r['phase']=='baseline' and r['controller_id']=='full'}
for controller in ['trained_full','refined_full']:
    paired[controller]={}
    for split in ['teacher_toy','heldout_synthetic']:
        cs=[r for r in records if r['controller_id']==controller and r['phase']!='live' and r['split']==split]
        pairs=[(frozen[(r['seed'],r['fixture'],r['control'])],r) for r in cs]
        paired[controller][split]={'pairs':len(pairs),
          'new_completions':sum(not a['strict_agent_completion'] and b['strict_agent_completion'] for a,b in pairs),
          'lost_completions':sum(a['strict_agent_completion'] and not b['strict_agent_completion'] for a,b in pairs),
          'both_completed':sum(a['strict_agent_completion'] and b['strict_agent_completion'] for a,b in pairs)}
output={'version':1,'completed':True,'protocol_sha256':file_sha256(protocol),
 'source_commit':registration['source_commit'],'implementation_sha256':registration['implementation_sha256'],
 'registered_at':registration['created_at'],'external_artifact_root':str(root).replace(str(Path.home()), '~', 1),
 'published_recipe_sha256':file_sha256(source/'benchmarks/feedback-v1/run.py'),
 'analysis_sha256':file_sha256(Path(__file__)),
 'recipe_note':'Initial init/baseline used an external adapter of the same APIs; subsequent stages used the published recipe. Primary protocol and runtime were frozen before measured execution. The refinement was registered separately after the first candidate failed.',
 'environment':{'python':registration['environment']['python'],'platform':registration['environment']['platform'],
                'full_environment_sha256':hashlib.sha256(json.dumps(registration['environment'],sort_keys=True).encode()).hexdigest()},
 'graph':registration['graph'],'verified_source_checksums':registration['source_checksums'],
 'counts':{'offline_evaluation_runs':280,'teacher_runs':10,'live_model_runs':6,'total_agent_runs':296,
           'independent_audits':306,'pristine_negative_controls':4,'training_checkpoints':10},
 'model':{'id':'qwen/qwen3.5-9b','decision_model':None,'temperature':0,'max_output_tokens':1536,
          'weight_revision':None,'provider_token_usage':None,
          'limitations':'The current telemetry does not persist provider token usage or prove the exact local weight revision. Model calls are HTTP generation calls. llm_calls equals that count when model_calls is present; heuristic llm_calls counts cortex iterations. Native repeated_actions is habituation-specific; derived duplicate calls are reported separately and may be valid after edits.'},
 'scope':'Existing synthetic software fixtures, not scientific acceptance, workflow completion or orchestration admission.',
 'selection':{'default_checkpoint_changed':False,'production_admission':False,'research_admission':False,
              'first_candidate':'rejected: training-family regression',
              'refined_candidate':'rejected for live use: toy-only improvement, 0/2 live tasks versus frozen 2/2, model-call exhaustion; no tournament held-out success'},
 'raw_report_sha256':report_hashes,'summary':summary,'paired_offline_comparison':paired,
 'training':training,'checkpoint_integrity':json.loads((root/'checkpoint-integrity.json').read_text()),
 'teacher_feedback':teachers,'live_model_events':events,'cases':records}
path=root/'summary.json'
path.write_text(json.dumps(output,indent=2,allow_nan=False))
print('Published',path, path.stat().st_size,'bytes')
for name,g in summary.items():
 s=g['all'];print(name,s['strict_completed'],'/',s['cases'],'mean tools',s['mean_tools_all_cases'],'models',s['model_calls'])
print('Paired',paired)
