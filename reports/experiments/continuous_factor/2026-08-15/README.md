# Continuous GPU Factor

This research-only experiment trains three-seed GPU XGBoost regression ensembles on causal BTC
and ETH 4h cross-asset features. Models fit on 2021-2022, use 2023 only for early stopping and
score calibration, select signal and risk controls independently on 2024 and 2025, and load 2026
only after selection. Signals use closed bars and fill at the next 4h open.

The selected 1.5x ETH factor returned `+66.50%` in 2024 and `+124.26%` in 2025, but lost `50.88%`
with `-53.76%` drawdown in reused 2026 confirmation. Stress costs increased the loss to `54.40%`.
The short-horizon prediction relationship changed sign in 2026, so the candidate is rejected and
must not be connected to execution.

The authoritative artifacts are
[`continuous-factor-20260815-112759-832242.md`](continuous-factor-20260815-112759-832242.md)
and its adjacent JSON file. Checkpoints remain under ignored `data/continuous_factor_models/`.
