"""V3-0: decisions. Where to send which lot, on which vehicle.

Database-free core used by the API, the Admin comparison and the study (ml/agripulse_ml/decision_study.py):
  model.py      Problem (lots, vehicles, mandis with calibrated forecasts), costs, legs
  rule.py       the V1 rule-based recommender applied lot by lot (the OLD way, kept for comparison)
  optimizer.py  OR-Tools CP-SAT: all lots together under capacity, spoilage and mandi-absorption constraints
  evaluate.py   scores ANY plan the same way; with realised prices it grades decisions ex post (rule 21)
No forecast here is better than naive (V2): any gain comes from the constraints and decision logic, not prices.
"""
from .evaluate import evaluate
from .model import Assignment, Lot, MandiOption, Plan, Problem, Vehicle
from .optimizer import optimize
from .rule import rule_plan

__all__ = ["Assignment", "Lot", "MandiOption", "Plan", "Problem", "Vehicle", "evaluate", "optimize", "rule_plan"]
