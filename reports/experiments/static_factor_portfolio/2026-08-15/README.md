# Static Three/Four-Sleeve Factor Portfolio

This research-only experiment formalizes the strongest prior three-sleeve diagnostic and extends
the same protocol to unequal weights and fixed four-sleeve portfolios. It does not connect to
paper or live execution.

The factor universe is frozen on 2021-2023 before configuration selection. All 780 three-sleeve
and 9,880 four-sleeve combinations are screened on development data. Detailed weights and
leverage are selected on 2021-2025 with separate discovery and validation risk gates. The reused
2026 interval is loaded only after selection and is never used to choose a configuration.

Signals use closed component bars and fill at the next component-bar open. Component replays
include historical funding, 5 bps fees, and 2 bps slippage per fill. Stress confirmation uses
10 bps fees and 5 bps slippage. Portfolio equity uses fixed initial sleeve capital without free
daily rebalancing.

## Result

The experiment is rejected. The development protocol selected a four-sleeve portfolio at 4x:

- 40% BTC-shock-to-ETH lead-lag;
- 15% ETH 60-day shock continuation, long-only, 12x4h hold;
- 30% BTC 15-day shock continuation, long/short, 4x4h hold;
- 15% ETH-to-BTC 60-day underreaction continuation, long/short, 12x4h hold.

| Split | Return | Daily-close max DD | 25% months |
|---|---:|---:|---:|
| 2021-2023 discovery | +1388.74% | -34.22% | 7/36 |
| 2024-2025 validation | +214.45% | -34.56% | 5/24 |
| 2026 reused confirmation | +184.88% | -18.29% | 3/8 |
| 2026 stressed costs | +154.90% | -20.57% | 3/8 |

The development-selected three-sleeve baseline was stronger in total 2026 return at +209.55%,
with -22.34% drawdown, but also reached only 3/8 target months. The fourth sleeve reduced reused
confirmation drawdown by about 4 percentage points and improved March from +9.15% to +10.18%, but
reduced April and June. Neither configuration filled the missing fourth target month.

This is the strongest static risk-adjusted portfolio found in this search, but it fails the
required `4/8` monthly coverage gate. It remains research-only and is not approved for trading.

The authoritative artifacts are
[`static-factor-portfolio-20260815-110450-461817.md`](static-factor-portfolio-20260815-110450-461817.md)
and its adjacent JSON file.
