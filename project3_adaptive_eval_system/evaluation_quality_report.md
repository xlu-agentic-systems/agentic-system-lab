# Project 3 Evaluation Quality Report

This report captures the current deterministic benchmark run used to validate
the Project 3 adaptive-evaluation design.

Command:

```bash
python -m project3_adaptive_eval_system.app.cli benchmark --use-rule-based
```

Result:

- Labeled cases: 6
- Passed cases: 6
- Pass/fail accuracy: 1.0
- Issue-category recall: 1.0
- Patch-target accuracy: 1.0
- Evaluation depth score: 8.5
- Production readiness score: 7.5

Coverage:

- valid successful refund
- delivery-date vs purchase-date policy failure
- unsafe refund proposal blocked by backend validation
- missing clarification question
- hallucinated policy detail
- session context loss

The benchmark uses the deterministic rule-based client for repeatable local and
CI validation. Runtime evaluation still defaults to the real OpenAI structured
output client when `OPENAI_API_KEY` is configured.
