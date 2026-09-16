'''Evals package (task W-B4). CASES is the fixed real-question set;
run_evals() runs them through area.agent.run_agent for real and scores
each on citation-verified acceptance -- see runner.py.'''

from area.evals.cases import CASES, EvalCase
from area.evals.runner import EvalCaseResult, EvalsSummary, run_evals

__all__ = ['CASES', 'EvalCase', 'EvalCaseResult', 'EvalsSummary', 'run_evals']
