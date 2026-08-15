# BTC/ETH Event Consensus

This experiment tests whether simultaneous agreement across de-duplicated shock-event factors can
improve monthly return coverage. It also searches the opposite hypothesis: fading a crowded event
vote. The experiment is research-only and is not connected to execution.

The selection protocol is chronological. Event representatives are chosen on 2021-2023, consensus
and portfolio settings are selected on 2024-2025, and the fixed result is evaluated on the reused
2026 confirmation interval. The representative filter requires at least eight completed discovery
trades and no bankruptcy; it does not require a sleeve to be profitable because weak sleeves can
still provide useful disagreement information.

Signals use only a closed 4h bar and fill at the next 4h open. The base replay includes historical
funding, 5 bps per fill, and 2 bps slippage per fill. The stress replay uses 10 bps fees and 5 bps
slippage. Liquidation, market impact, exchange failure, and shared margin are not modeled.

## Result

The experiment is rejected.

- 864 event expressions were reduced to 24 structural groups using 2021-2023 only. One group was
  rejected for insufficient completed trades, leaving 19 BTC and 4 ETH representatives.
- 82 of 1,008 BTC consensus settings and 55 of 672 ETH settings passed the 2024-2025 component
  risk gates. Neither selected component used a fallback diagnostic.
- Both instruments selected `follow`, not `fade`: BTC required three active votes with 67%
  agreement at 3x target exposure and a 15% monthly loss limit; ETH required two active votes with
  50% agreement at 0.5x exposure and a 5% monthly loss limit.
- Portfolio selection evaluated 20 BTC/ETH allocations and leverage settings. It assigned ETH zero
  weight and retained BTC at 1x portfolio leverage, so consensus did not produce a diversified
  portfolio.

| Split | Return | Daily-close max DD | Positive months | 25% months |
|---|---:|---:|---:|---:|
| 2024-2025 selection | +7.61% | -29.69% | 15/24 | 1/24 |
| 2026 reused confirmation | +8.69% | -19.17% | 4/8 | 0/8 |
| 2026 stressed costs | +4.96% | -16.59% | 4/8 | 0/8 |

The fixed BTC component remained profitable under higher costs, but it did not produce a single
25% confirmation month. The ETH component changed from +18.46% in selection to -7.33% in
confirmation. Simultaneous voting therefore reduces neither the sparse-return problem nor the
instability enough to meet the research objective. Searching nearby vote thresholds against the
already revealed 2026 interval would only add confirmation leakage.

The authoritative artifacts are
[`event-consensus-20260815-100129-636117.md`](event-consensus-20260815-100129-636117.md) and its
adjacent JSON file. The JSON contains all selected representatives, development rankings,
component metrics, and 2026 daily and monthly returns.
