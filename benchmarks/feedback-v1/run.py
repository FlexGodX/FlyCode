"""Execute the registered feedback-v1 protocol with existing FlyCode tools.

Run from the checkout using PYTHONPATH=src; raw outputs stay outside Git.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from flycode.environment.fixtures import materialize
from flycode.research.benchmark import BenchmarkConfig, run_benchmark
from flycode.research.identity import file_sha256, implementation_fingerprint
from flycode.verification import VerificationEngine

SOURCE = Path(__file__).resolve().parents[2]
PROTOCOL = SOURCE / 'benchmarks/feedback-v1/protocol.json'
p = json.loads(PROTOCOL.read_text())
parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['init', 'baseline', 'teacher', 'train', 'trained', 'refine', 'live', 'audit'])
parser.add_argument('root', type=Path)
a = parser.parse_args()
root = a.root
root.mkdir(parents=True, exist_ok=True)
graph = Path(p['graph']).expanduser()
if a.stage == 'init':
    if (root / 'registration.json').exists():
        raise FileExistsError('Use a new output directory for a new registration')
else:
    registration = json.loads((root / 'registration.json').read_text())
    if registration['implementation_sha256'] != implementation_fingerprint():
        raise ValueError('Runtime changed after registration')
    if registration['protocol_sha256'] != file_sha256(PROTOCOL):
        raise ValueError('Protocol changed after registration')

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False))

def config(controllers, **overrides):
    return BenchmarkConfig.model_validate(dict(task=p['task'], controllers=controllers,
        fixtures=p['train_fixtures'] + p['heldout_fixtures'], seeds=p['seeds'], controls=p['controls'],
        cortex=p['evaluation_cortex'], max_steps=p['max_steps'], max_tool_calls=p['max_tool_calls'], **overrides))

async def execute(name, c):
    save(root / 'configs' / (name + '.json'), c.model_dump(mode='json'))
    start = time.monotonic()
    print('START', name, flush=True)
    report = await run_benchmark(c, root / name)
    save(root / (name + '.json'), report)
    print('END', name, round(time.monotonic() - start, 2), json.dumps(report['summary']), flush=True)
    return report

def audit(report, name):
    engine = VerificationEngine(root / 'evidence' / name)
    rows = []
    for case in report['cases']:
        repo = Path(case['repository'])
        original = root / 'pristine' / case['fixture']
        expected = {f.relative_to(original / 'tests').as_posix(): file_sha256(f)
                    for f in (original / 'tests').rglob('*.py')}
        actual = {f.relative_to(repo / 'tests').as_posix(): file_sha256(f)
                  for f in (repo / 'tests').rglob('*.py')} if (repo / 'tests').exists() else {}
        unchanged = expected == actual
        row = {'run_id': case['run_id'], 'fixture': case['fixture'], 'seed': case['seed'],
               'controller_id': case['controller_id'], 'control': case['control'], 'tests_unchanged': unchanged}
        if repo.exists() and unchanged:
            verdict = engine.verify(repo, timeout=30)
            row.update(status=verdict.status, verdict_id=verdict.evidence_id, cases=len(verdict.cases),
                       scope=verdict.scope)
        else:
            row.update(status='blocked', cases=0, scope='registered-suite')
        negative = json.loads(next((root / 'negative-controls' / case['fixture']).glob('*/verdict.json')).read_text())
        expected_ids = {item['id'] for item in negative['cases']}
        observed_ids = {item.id for item in verdict.cases} if repo.exists() and unchanged else set()
        row['case_ids_match'] = expected_ids == observed_ids
        row['expected_cases'] = sorted(expected_ids)
        row['accepted'] = row['status'] == 'passed' and unchanged and row['case_ids_match'] and row['cases'] > 0
        rows.append(row)
    save(root / 'feedback' / (name + '.json'), rows)
    print('AUDIT', name, sum(r['accepted'] for r in rows), '/', len(rows), flush=True)
    return rows

def fit(curated, checkpoints):
    for seed in p['seeds']:
        command = [sys.executable, '-m', 'flycode.cli', 'train', '--trajectories', str(curated), '--connectome-path', str(graph),
                   '--output', str(checkpoints / str(seed)), '--seed', str(seed), '--ridge', str(p['ridge'])]
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=300)
        (checkpoints / ('train-' + str(seed) + '.stdout')).write_text(result.stdout)
        (checkpoints / ('train-' + str(seed) + '.stderr')).write_text(result.stderr)
        assert result.returncode == 0, result.stderr
        print('TRAINED', seed, json.loads((checkpoints / str(seed) / 'training.json').read_text())['trained'], flush=True)

if a.stage == 'init':
    from flycode.brain.full_connectome.graph import SparseConnectome
    from flycode.brain.full_connectome.importer import SOURCE_CHECKSUMS
    from flycode.verification.snapshot import environment_identity
    checks = {}
    for name, expected in SOURCE_CHECKSUMS.items():
        h = hashlib.md5()
        with (graph.parent / name).open('rb') as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                h.update(chunk)
        assert h.hexdigest() == expected, name
        checks[name] = {'expected_md5': expected, 'actual_md5': h.hexdigest()}
    g = SparseConnectome.load(graph)
    save(root / 'registration.json', {'created_at': datetime.now(timezone.utc).isoformat(),
        'protocol_sha256': file_sha256(PROTOCOL), 'driver_sha256': file_sha256(Path(__file__)), 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip(),
        'implementation_sha256': implementation_fingerprint(), 'environment': environment_identity(),
        'source_checksums': checks, 'graph': g.metadata})
    for fixture in p['train_fixtures'] + p['heldout_fixtures']:
        materialize(fixture, root / 'pristine' / fixture)
        verdict = VerificationEngine(root / 'negative-controls' / fixture).verify(root / 'pristine' / fixture, timeout=30)
        assert verdict.status == 'failed' and len(verdict.cases) > 0, (fixture, verdict.status)
        print('NEGATIVE CONTROL', fixture, verdict.status, len(verdict.cases), flush=True)
elif a.stage == 'baseline':
    specs = [{'id': b, 'brain': b, **({'connectome_path': str(graph)} if b == 'full' else {})} for b in p['baselines']]
    asyncio.run(execute('baseline', config(specs)))
elif a.stage == 'teacher':
    c = config([{'id': 'teacher', 'brain': p['teacher_brain']}])
    c = c.model_copy(update={'fixtures': p['train_fixtures'], 'controls': ['bias'], 'cortex': p['teacher_cortex']})
    r = asyncio.run(execute('teacher', c))
    audit(r, 'teacher')
elif a.stage == 'train':
    r = json.loads((root / 'teacher.json').read_text())
    feedback = {row['run_id']: row for row in json.loads((root / 'feedback/teacher.json').read_text())}
    curated = root / 'curated-training'
    curated.mkdir(exist_ok=False)
    for case in r['cases']:
        assert case['fixture'] in p['train_fixtures'] and case['fixture'] not in p['heldout_fixtures']
        assert feedback[case['run_id']]['accepted'] and case['metrics']['verified_completion'] and case['error'] is None
        source = root / 'teacher' / r['experiment_id'] / 'trajectories' / (case['run_id'] + '.steps.jsonl')
        shutil.copyfile(source, curated / source.name)
    save(curated / 'selection.json', {'source_report_sha256': file_sha256(root / 'teacher.json'),
         'feedback_sha256': file_sha256(root / 'feedback/teacher.json'), 'run_ids': [c['run_id'] for c in r['cases']],
         'filter': 'Complete successful teacher episodes; original tests unchanged and independently passing; training fixtures only.'})
    fit(curated, root / 'checkpoints')
elif a.stage == 'trained':
    for seed in p['seeds']:
        c = config([{'id': 'trained_full', 'brain': 'full', 'connectome_path': str(graph), 'checkpoint': str(root / 'checkpoints' / str(seed))}])
        c = c.model_copy(update={'seeds': [seed]})
        asyncio.run(execute('trained-' + str(seed), c))
elif a.stage == 'refine':
    refinement = json.loads((PROTOCOL.parent / 'refinement.json').read_text())
    r = json.loads((root / 'baseline.json').read_text())
    r['cases'] = [c for c in r['cases'] if c['controller_id'] == refinement['teacher_controller']
                  and c['fixture'] in refinement['fixtures'] and c['control'] == refinement['control']]
    assert len(r['cases']) == refinement['required_episodes']
    assert not set(refinement['fixtures']) & set(p['heldout_fixtures'])
    save(root / 'refinement-registration.json', {'created_at': datetime.now(timezone.utc).isoformat(),
         'refinement_sha256': file_sha256(PROTOCOL.parent / 'refinement.json'), 'protocol': refinement})
    rows = audit(r, 'refinement-teacher')
    assert all(row['accepted'] for row in rows)
    curated = root / 'refined-training'
    curated.mkdir(exist_ok=False)
    for case in r['cases']:
        assert case['error'] is None and case['metrics']['verified_completion']
        source = root / 'baseline' / r['experiment_id'] / 'trajectories' / (case['run_id'] + '.steps.jsonl')
        shutil.copyfile(source, curated / source.name)
    save(curated / 'selection.json', {'run_ids': [c['run_id'] for c in r['cases']],
         'source_report_sha256': file_sha256(root / 'baseline.json'),
         'feedback_sha256': file_sha256(root / 'feedback/refinement-teacher.json')})
    fit(curated, root / 'refined-checkpoints')
    for seed in p['seeds']:
        c = config([{'id': 'refined_full', 'brain': 'full', 'connectome_path': str(graph),
                     'checkpoint': str(root / 'refined-checkpoints' / str(seed))}])
        c = c.model_copy(update={'seeds': [seed]})
        asyncio.run(execute('refined-' + str(seed), c))
elif a.stage == 'live':
    deadline = time.monotonic() + p['live']['wall_limit_seconds'] - p['live']['request_timeout']
    event_path = root / 'live-model-events.jsonl'
    case_counter = []
    class ObservedConfig(BenchmarkConfig):
        def create_cortex(self):
            if time.monotonic() >= deadline:
                raise RuntimeError('Benchmark wall deadline reached')
            cortex = super().create_cortex()
            case_index = len(case_counter)
            case_counter.append(case_index)
            def sink(kind, data):
                with event_path.open('a') as stream:
                    stream.write(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), 'case_index': case_index,
                                             'kind': kind, 'data': data}) + '\n')
            cortex.on_event = sink
            cortex.should_stop = lambda: time.monotonic() >= deadline
            generate = cortex.generate_candidates
            def bounded_generate(context):
                if time.monotonic() >= deadline:
                    sink('diagnostic', {'error': 'Benchmark wall deadline reached'})
                    return []
                return generate(context)
            cortex.generate_candidates = bounded_generate
            return cortex
    c = config([{'id': 'frozen_full', 'brain': 'full', 'connectome_path': str(graph)},
                {'id': 'trained_full', 'brain': 'full', 'connectome_path': str(graph), 'checkpoint': str(root / 'checkpoints/0')}])
    if (root / 'refinement-registration.json').exists():
        registration = json.loads((root / 'refinement-registration.json').read_text())
        assert registration['refinement_sha256'] == file_sha256(PROTOCOL.parent / 'refinement.json')
        values = c.model_dump(mode='json')
        values['controllers'].append({'id': 'refined_full', 'brain': 'full', 'connectome_path': str(graph),
                                      'checkpoint': str(root / 'refined-checkpoints/0')})
        c = BenchmarkConfig.model_validate(values)
    values = c.model_dump(mode='json')
    values.update({k: v for k, v in p['live'].items() if k != 'wall_limit_seconds'})
    values['fixtures'] = p['heldout_fixtures']
    asyncio.run(execute('live', ObservedConfig.model_validate(values)))
elif a.stage == 'audit':
    for name in ['baseline', *['trained-' + str(seed) for seed in p['seeds']],
                 *['refined-' + str(seed) for seed in p['seeds']], 'live']:
        if (root / (name + '.json')).exists():
            audit(json.loads((root / (name + '.json')).read_text()), name)
