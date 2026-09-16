'''Causal package (task W-B3). estimate_diff_in_diff() is the estimator;
run() is the raw-data -> causal/latest.json entry point `area causal`
uses. See diff_in_diff.py's module docstring for the design and its
disclosed scope (a standard 2-group/2-period DiD, not a discovered
real-world policy claim).'''

from area.causal.diff_in_diff import DEFAULT_TREATED_MANUFACTURERS, estimate_diff_in_diff, run

__all__ = ['estimate_diff_in_diff', 'run', 'DEFAULT_TREATED_MANUFACTURERS']
