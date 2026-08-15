# Expanded Static Factor Portfolio

This research-only experiment removes two possible search bottlenecks from the static factor
portfolio: the 40-factor ranking cap and the four-sleeve limit. It retains all 152 factors that
passed the discovery gates and uses a deterministic development-only beam search through two to
five sleeves. The reused 2026 interval does not participate in selection.

The search evaluated 16,785 detailed configurations; 106 passed the development risk gates. It
selected exactly the existing four-sleeve, 4x baseline. Reused 2026 confirmation returned
`+184.88%` with `-18.29%` daily-close drawdown, remained positive at `+154.90%` under `10+5 bps`
stress costs, and reached the `+25%` monthly target in `3/8` months.

The experiment is rejected because it misses the required `4/8` monthly coverage. The unchanged
selection also shows that the earlier candidate cap and missing fifth sleeve were not the cause.
The authoritative artifacts are
[`expanded-factor-portfolio-20260815-112028-442988.md`](expanded-factor-portfolio-20260815-112028-442988.md)
and its adjacent JSON file.
