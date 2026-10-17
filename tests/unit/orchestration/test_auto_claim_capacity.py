"""Measure a claim over a full legal task graph and retained attempt history."""
import json
import os
import time
from pathlib import Path

import test_store
from test_store import command, draft, queued, request

setup = test_store.setup


def test_auto_claim_full_graph_keeps_original_history_and_budget(setup, tmp_path):
    from flycode.domain.orchestration import AutoClaimRequest, BudgetAmount

    store, owner, host, _, instance, _, _, permission = setup
    workflow, root = queued(setup)
    branches=[]; leaves=[]
    for index in range(8):
        task=store.create_task(owner,workflow.id,draft(permission,parent_id=root.id),key=f'branch-{index}')
        branches.append(command(store,owner,task,'enqueue'))
    for index,branch in enumerate(branches[:7]):
        for child in range(8 if index<6 else 7):
            task=store.create_task(owner,workflow.id,draft(permission,parent_id=branch.id),key=f'leaf-{index}-{child}')
            leaves.append(command(store,owner,task,'enqueue'))
    leaves.append(branches[-1])
    assert len(store.tasks(owner,workflow.id))==64 and len(leaves)==56
    for task in leaves[:-1]:
        for number in range(3):
            idle=store.instance(host,instance.id)
            current=store.task(owner,task.id)
            claimed=store.claim(host,request(idle,current,key=f'history-{task.id}-{number}')).attempt
            assert claimed is not None
            store.confirm_started(host,claimed.id,claimed.fence)
            store.confirm_stopped(host,claimed.id,claimed.fence,outcome='retry_wait',expenditure_confirmed=True)
            if number<2:command(store,owner,store.task(owner,task.id),'retry')
    assert len(store.attempts(owner,workflow.id))==165
    deadline=store.workflow(owner,workflow.id).deadline_at
    idle=store.instance(host,instance.id)
    statements=[]
    store.db.set_trace_callback(lambda sql: statements.append(sql.split()[0]))
    start=time.perf_counter()
    result=store.claim_next(host,AutoClaimRequest(instance_id=idle.id,expected_instance_revision=idle.revision,idempotency_key='capacity-claim'))
    elapsed=time.perf_counter()-start
    store.db.set_trace_callback(None)
    assert result.attempt is not None and result.attempt.task_id==leaves[-1].id
    assert result.attempt.initial_budget==BudgetAmount(model_calls=10,tool_calls=20)
    assert len(store.attempts(owner,workflow.id))==166
    assert store.budget(owner,workflow.id).spent==BudgetAmount()
    assert store.workflow(owner,workflow.id).deadline_at==deadline
    target=Path(os.environ.get('FLYCODE_CAPACITY_REPORT', str(tmp_path / 'claim-capacity.json')))
    target.write_text(json.dumps({'kind':'isolated prototype queue capacity measurement; no native inference/dispatcher acceptance','tasks':64,'synthetic_zero_effect_attempts':165,'claims_measured':1,'claim_seconds':elapsed,'select_statements':statements.count('SELECT'),'spent_models':0,'spent_tools':0,'deadline_preserved':True},indent=2))
    assert statements.count('SELECT') < 1024, 'Claim repeats full database reads per candidate'
