# Unrestricted Marginal Factor Portfolio

This research-only experiment tests all 2,988 static BTC/ETH factors as a marginal sleeve beside
the frozen four-sleeve anchor. Unlike earlier portfolio searches, the marginal factor need not be
profitable or pass a standalone discovery gate. The combined portfolio must independently pass
positive-return, positive-month, and 35% drawdown gates in 2021-2023, 2024, and 2025.

The search evaluated 71,664 aligned configurations; 4,140 passed the combined development gates.
It selected a 30% ETH 60m expression sleeve with the anchor at 1.25x outer leverage. Reused 2026
confirmation returned `+136.42%` with `-21.60%` drawdown and `3/8` target months. Stress costs
reduced it to `+102.79%`, `-25.89%` drawdown, and `2/8` target months. The marginal sleeve alone
lost `67.59%` in 2026, demonstrating severe regime failure.

The unrestricted marginal search is rejected. The authoritative artifacts are
[`marginal-factor-portfolio-20260815-120433-669275.md`](marginal-factor-portfolio-20260815-120433-669275.md)
and its adjacent JSON file.
