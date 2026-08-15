# Monthly Factor Risk Overlay

This research-only experiment adds fixed leverage, a month-to-date loss lock, and a profit lock
to the frozen four-sleeve static factor anchor. Locks are evaluated after each UTC daily close,
flatten on the next day, reset at the next month, and include 7 bps exposure-turnover cost.
Configuration selection uses the same 2021-2023 discovery and continuous 2024-2025 validation
protocol as the frozen anchor.

Twenty of 275 configurations passed development risk gates. The selected 1.0x overlay with a 15%
loss lock and 50% profit lock returned `+186.75%` with `-18.29%` drawdown in reused 2026, but still
reached only `3/8` target months. Stress confirmation remained positive at `+158.84%` and `3/8`.
An explicitly non-selective diagnostic evaluated all 20 eligible neighbors; none met the 4/8
confirmation gate.

The risk overlay is rejected. The authoritative artifacts are
[`factor-risk-overlay-20260815-120924-108288.md`](factor-risk-overlay-20260815-120924-108288.md)
and its adjacent JSON file.
