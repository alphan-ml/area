'''Evals package (task W-B4; scoring rewritten in task A1 part 1). CASES
is the fixed real-question set (now carrying gold/expect_abstain/status/
parts scoring metadata -- see cases.py); run_evals() runs them through
area.agent.run_agent for real, score_from_traces() re-scores already-
written trace files with no agent loop/database call at all -- see
runner.py and scoring.py for what "passed" means today.'''

from area.evals.cases import CASES, EvalCase
from area.evals.runner import EvalCaseResult, EvalsSummary, run_evals, score_from_traces
from area.evals.scoring import CaseScore, score_case

__all__ = [
    'CASES',
    'EvalCase',
    'EvalCaseResult',
    'EvalsSummary',
    'run_evals',
    'score_from_traces',
    'CaseScore',
    'score_case',
]
