"""Research-only job orchestration, replay reports, and daily data maintenance."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from mastermind_tick.backtest import ReplayParameters, run_parameter_grid
from mastermind_tick.config import InstrumentSettings, Settings
from mastermind_tick.factor_mining import FactorMiningConfig, load_market, mine_instrument

DAY_MS = 86_400_000
MAX_CANDIDATES = 24


@dataclass(frozen=True)
class ResearchPreset:
    instrument: InstrumentSettings
    archive_dir: str
    history_start_date: date
    direction: str
    atr_periods: tuple[int, ...]
    atr_multipliers: tuple[float, ...]
    trend_efficiency_period: int = 8
    minimum_trend_efficiency: float = 0.25
    reversal_confirmation_atr: float = 0.0
    leverage: int = 1
    position_fraction: float = 1.0
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    status: str = "baseline_unoptimized"

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument.id,
            "symbol": self.instrument.symbol,
            "display_symbol": self.instrument.display_symbol,
            "name": self.instrument.name,
            "history_start_date": self.history_start_date.isoformat(),
            "direction": self.direction,
            "atr_periods": list(self.atr_periods),
            "atr_multipliers": list(self.atr_multipliers),
            "trend_efficiency_period": self.trend_efficiency_period,
            "minimum_trend_efficiency": self.minimum_trend_efficiency,
            "reversal_confirmation_atr": self.reversal_confirmation_atr,
            "leverage": self.leverage,
            "position_fraction": self.position_fraction,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "status": self.status,
        }


def research_presets(settings: Settings) -> dict[str, ResearchPreset]:
    soxl = next(item for item in settings.instruments if item.id == "soxl_perp")

    def crypto(instrument_id: str, symbol: str, name: str, reference: str) -> InstrumentSettings:
        return InstrumentSettings(
            id=instrument_id,
            symbol=symbol,
            display_symbol=f"{reference}/USDT PERP",
            name=name,
            asset_type="crypto_perpetual",
            venue="Binance USD-M Futures",
            currency="USDT",
            feed="binance_futures",
            quantity_step=0.001,
            reference_symbol=reference,
            paper_model="futures",
            leverage=1,
            margin_mode="isolated",
            position_fraction=1.0,
            fee_bps=5.0,
            slippage_bps=2.0,
            minimum_notional=5.0,
            allow_short=True,
        )

    return {
        "soxl_perp": ResearchPreset(
            instrument=soxl,
            archive_dir="data/history_soxl",
            history_start_date=date(2026, 5, 15),
            direction="long_only",
            atr_periods=(28, 32, 35),
            atr_multipliers=(2.5, 3.0, 3.5),
            leverage=2,
            position_fraction=0.625,
            status="researched_candidate_grid",
        ),
        "btc_perp": ResearchPreset(
            instrument=crypto("btc_perp", "BTCUSDT", "Bitcoin USD-M Perpetual", "BTC"),
            archive_dir="data/history_btc",
            history_start_date=date(2024, 1, 1),
            direction="long_short",
            atr_periods=(14, 21, 28),
            atr_multipliers=(2.0, 2.5, 3.0),
            status="baseline_rejected_validation",
        ),
        "eth_perp": ResearchPreset(
            instrument=crypto("eth_perp", "ETHUSDT", "Ethereum USD-M Perpetual", "ETH"),
            archive_dir="data/history_eth",
            history_start_date=date(2026, 5, 1),
            direction="long_short",
            atr_periods=(14, 21, 28),
            atr_multipliers=(2.0, 2.5, 3.0),
        ),
    }


class ResearchLab:
    """Run at most one warehouse update or replay at a time."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.presets = research_presets(settings)
        self.report_dir = settings.project_root / "reports" / "backtests"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.factor_report_dir = settings.project_root / "reports" / "experiments" / "factor_mining"
        self.factor_report_dir.mkdir(parents=True, exist_ok=True)
        self.deep_factor_report_dir = (
            settings.project_root / "reports" / "experiments" / "deep_factor_v2"
        )
        self.deep_factor_report_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_deep_factor_report_dir = (
            settings.project_root / "reports" / "experiments" / "deep_factor"
        )
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def preset_list(self) -> list[dict[str, Any]]:
        return [preset.as_dict() for preset in self.presets.values()]

    def data_status(self, instrument_id: str = "soxl_perp") -> dict[str, Any]:
        preset = self._preset(instrument_id)
        database_uri = f"file:{self.settings.database_path}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT MIN(timestamp_ms) AS first_ms, MAX(timestamp_ms) AS last_ms,
                       COUNT(*) AS tick_count,
                       COALESCE(SUM(CASE
                           WHEN first_trade_id IS NOT NULL AND last_trade_id IS NOT NULL
                           THEN last_trade_id - first_trade_id + 1
                           ELSE 1 END), 0) AS raw_trade_count
                FROM agg_trades WHERE instrument_id = ?
                """,
                (instrument_id,),
            ).fetchone()
            bar = connection.execute(
                """
                SELECT MAX(end_ms) AS last_bar_ms, COUNT(*) AS bar_count
                FROM ohlcv_bars
                WHERE instrument_id = ? AND interval_minutes = 15 AND is_closed = 1
                """,
                (instrument_id,),
            ).fetchone()
            earliest_replay = connection.execute(
                """
                SELECT start_ms FROM ohlcv_bars
                WHERE instrument_id = ? AND interval_minutes = 15 AND is_closed = 1
                ORDER BY start_ms LIMIT 1 OFFSET ?
                """,
                (instrument_id, self.settings.warmup_bars),
            ).fetchone()
            funding_count = connection.execute(
                "SELECT COUNT(*) FROM funding_rates WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()[0]
        last_bar_ms = int(bar["last_bar_ms"]) if bar and bar["last_bar_ms"] is not None else None
        complete_through = None
        if last_bar_ms is not None:
            bar_time = datetime.fromtimestamp(last_bar_ms / 1000, UTC)
            complete_through = (
                bar_time.date()
                if bar_time.hour == 23 and bar_time.minute == 59
                else bar_time.date() - timedelta(days=1)
            )
        yesterday = datetime.now(UTC).date() - timedelta(days=1)
        earliest_replay_ms = int(earliest_replay[0]) if earliest_replay else None
        return {
            "instrument_id": instrument_id,
            "symbol": preset.instrument.symbol,
            "first_tick_ms": int(row["first_ms"]) if row and row["first_ms"] is not None else None,
            "last_tick_ms": int(row["last_ms"]) if row and row["last_ms"] is not None else None,
            "tick_count": int(row["tick_count"]) if row else 0,
            "raw_trade_count": int(row["raw_trade_count"]) if row else 0,
            "bar_count": int(bar["bar_count"]) if bar else 0,
            "funding_count": int(funding_count),
            "complete_through_date": complete_through.isoformat() if complete_through else None,
            "earliest_replay_ms": earliest_replay_ms,
            "earliest_replay_date": (
                datetime.fromtimestamp(earliest_replay_ms / 1000, UTC).date().isoformat()
                if earliest_replay_ms is not None
                else None
            ),
            "default_update_date": yesterday.isoformat(),
            "database_path": str(self.settings.database_path),
        }

    def submit_data_update(self, instrument_id: str, target_date: date) -> dict[str, Any]:
        preset = self._preset(instrument_id)
        yesterday = datetime.now(UTC).date() - timedelta(days=1)
        if target_date > yesterday:
            raise ValueError(f"target date cannot be later than {yesterday.isoformat()} UTC")
        if target_date < preset.history_start_date:
            raise ValueError(
                f"target date cannot be earlier than {preset.history_start_date.isoformat()}"
            )
        job = self._new_job(
            "data_update",
            {"instrument_id": instrument_id, "target_date": target_date.isoformat()},
        )
        self._executor.submit(self._run_data_update, job["id"], instrument_id, target_date)
        return job

    def submit_backtest(self, request: dict[str, Any]) -> dict[str, Any]:
        self._preset(request["instrument_id"])
        candidate_count = len(request["atr_periods"]) * len(request["atr_multipliers"])
        if candidate_count < 1 or candidate_count > MAX_CANDIDATES:
            raise ValueError(f"ATR grid must contain between 1 and {MAX_CANDIDATES} candidates")
        if request["start_date"] > request["end_date"]:
            raise ValueError("start_date must not be later than end_date")
        activation = request.get("profit_activation_atr")
        trailing = request.get("profit_trailing_atr")
        if (activation is None) != (trailing is None):
            raise ValueError("profit activation and trailing ATR must be enabled together")
        payload = {
            **request,
            "start_date": request["start_date"].isoformat(),
            "end_date": request["end_date"].isoformat(),
            "candidate_count": candidate_count,
        }
        job = self._new_job("backtest", payload)
        self._executor.submit(self._run_backtest, job["id"], request)
        return job

    def submit_factor_mining(self, instrument_id: str) -> dict[str, Any]:
        if instrument_id not in {"btc_perp", "eth_perp", "soxl_perp"}:
            raise ValueError(f"unsupported factor-mining instrument: {instrument_id}")
        job = self._new_job("factor_mining", {"instrument_id": instrument_id})
        self._executor.submit(self._run_factor_mining, job["id"], instrument_id)
        return job

    def submit_deep_factor_mining(self, instruments: tuple[str, ...]) -> dict[str, Any]:
        if set(instruments) != {"btc_perp", "eth_perp"} or len(instruments) != 2:
            raise ValueError("deep factor v2 requires BTC and ETH together")
        python = Path("/home/spaceaic/env/.venv/bin/python")
        if not python.is_file():
            raise ValueError(f"GPU research Python environment not found: {python}")
        job = self._new_job("deep_factor", {"instruments": list(instruments)})
        self._executor.submit(self._run_deep_factor, job["id"], instruments, python)
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        valid_characters = "abcdefghijklmnopqrstuvwxyz0123456789-_"
        if not report_id or any(value not in valid_characters for value in report_id):
            return None
        path = self.report_dir / f"{report_id}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_factor_report(self, report_id: str) -> dict[str, Any] | None:
        valid_characters = "abcdefghijklmnopqrstuvwxyz0123456789-_"
        if not report_id or any(value not in valid_characters for value in report_id):
            return None
        matches = list(self.factor_report_dir.glob(f"*/{report_id}.json"))
        if len(matches) != 1:
            return None
        try:
            return json.loads(matches[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def list_factor_reports(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        reports = []
        for path in sorted(self.factor_report_dir.glob("*/*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if "id" not in payload or "generated_at" not in payload:
                    continue
                if instrument_id and payload.get("instrument_id") != instrument_id:
                    continue
                reports.append(
                    {
                        "id": payload["id"],
                        "generated_at": payload["generated_at"],
                        "instrument_id": payload["instrument_id"],
                        "candidate_count": payload["candidate_count"],
                        "status": payload["decision"]["status"],
                    }
                )
            except (KeyError, json.JSONDecodeError, OSError):
                continue
        return reports[:20]

    def get_deep_factor_report(self, report_id: str) -> dict[str, Any] | None:
        if not report_id or any(
            value not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for value in report_id
        ):
            return None
        matches = [
            *self.deep_factor_report_dir.glob(f"*/{report_id}.json"),
            *self.legacy_deep_factor_report_dir.glob(f"*/{report_id}.json"),
        ]
        if len(matches) != 1:
            return None
        try:
            return json.loads(matches[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def list_deep_factor_reports(self) -> list[dict[str, Any]]:
        reports = []
        paths = {
            *self.deep_factor_report_dir.glob("*/*.json"),
            *self.legacy_deep_factor_report_dir.glob("*/*.json"),
        }
        for path in sorted(paths, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                reports.append(
                    {
                        "id": payload["id"],
                        "generated_at": payload["generated_at"],
                        "instruments": list(payload["data"]),
                        "status": payload["decision"]["status"],
                    }
                )
            except (KeyError, json.JSONDecodeError, OSError):
                continue
        return reports[:20]

    def list_reports(self, instrument_id: str | None = None) -> list[dict[str, Any]]:
        reports = []
        for path in sorted(self.report_dir.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if instrument_id and payload.get("instrument_id") != instrument_id:
                    continue
                reports.append(
                    {
                        "id": payload["id"],
                        "generated_at": payload["generated_at"],
                        "symbol": payload["symbol"],
                        "start_date": payload["request"]["start_date"],
                        "end_date": payload["request"]["end_date"],
                        "candidate_count": len(payload["candidates"]),
                    }
                )
            except (KeyError, json.JSONDecodeError, OSError):
                continue
        return reports[:50]

    def _new_job(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "stage": "等待研究任务队列",
            "progress": 0.0,
            "created_at": datetime.now(UTC).isoformat(),
            "started_at": None,
            "completed_at": None,
            "report_id": None,
            "error": None,
            "message": None,
            "result": None,
            "request": request,
        }
        with self._lock:
            self._jobs[job_id] = job
        return dict(job)

    def _update_job(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)

    def _run_data_update(self, job_id: str, instrument_id: str, target_date: date) -> None:
        self._update_job(
            job_id,
            status="running",
            stage="下载并导入完整 UTC 日数据",
            progress=0.05,
            started_at=datetime.now(UTC).isoformat(),
        )
        try:
            preset = self._preset(instrument_id)
            status = self.data_status(instrument_id)
            end_ms = (
                int(
                    datetime.combine(
                        target_date + timedelta(days=1), datetime.min.time(), UTC
                    ).timestamp()
                    * 1000
                )
                - 1
            )
            if (
                status["complete_through_date"] is not None
                and date.fromisoformat(status["complete_through_date"]) >= target_date
            ):
                self._update_job(
                    job_id,
                    status="completed",
                    stage="数据已是最新完整日",
                    progress=1.0,
                    completed_at=datetime.now(UTC).isoformat(),
                    message=f"无需更新，数据已覆盖 {target_date.isoformat()} UTC",
                )
                return
            command = [
                sys.executable,
                str(self.settings.project_root / "scripts" / "import_soxl_history.py"),
                "--database",
                str(self.settings.database_path),
                "--instrument-id",
                instrument_id,
                "--archive-dir",
                str(self.settings.project_root / preset.archive_dir),
                "--symbol",
                preset.instrument.symbol,
                "--start-ms",
                str(
                    int(status["last_tick_ms"]) + 1
                    if status["last_tick_ms"] is not None
                    else _date_start_ms(preset.history_start_date)
                ),
                "--end-ms",
                str(end_ms),
            ]
            completed = subprocess.run(
                command,
                cwd=self.settings.project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=7200,
            )
            self._update_job(
                job_id,
                status="completed",
                stage="数据更新完成",
                progress=1.0,
                completed_at=datetime.now(UTC).isoformat(),
                message=completed.stdout.strip() or f"已更新到 {target_date.isoformat()} UTC",
            )
        except Exception as exc:
            self._fail_job(job_id, exc)

    def _run_backtest(self, job_id: str, request: dict[str, Any]) -> None:
        self._update_job(
            job_id,
            status="running",
            stage="准备回测参数",
            progress=0.02,
            started_at=datetime.now(UTC).isoformat(),
        )
        try:
            preset = self._preset(request["instrument_id"])
            instrument = preset.instrument
            instrument = replace(
                instrument,
                leverage=request["leverage"],
                position_fraction=request["position_fraction"],
                fee_bps=request["fee_bps"],
                slippage_bps=request["slippage_bps"],
                allow_short=request["direction"] != "long_only",
            )
            strategy = replace(
                self.settings.strategy,
                bar_minutes=15,
                trend_efficiency_period=request["trend_efficiency_period"],
                minimum_trend_efficiency=request["minimum_trend_efficiency"],
                reversal_confirmation_atr=request["reversal_confirmation_atr"],
                position_fraction=request["position_fraction"],
            )
            settings = replace(
                self.settings,
                initial_cash=request["initial_cash"],
                strategy=strategy,
            )
            parameters = [
                ReplayParameters(
                    period,
                    multiplier,
                    variant=request["direction"],
                    profit_activation_atr=request.get("profit_activation_atr"),
                    profit_trailing_atr=request.get("profit_trailing_atr"),
                    continuation_reentry_atr=request.get("continuation_reentry_atr"),
                )
                for period in request["atr_periods"]
                for multiplier in request["atr_multipliers"]
            ]
            start_ms = _date_start_ms(request["start_date"])
            end_ms = _date_start_ms(request["end_date"] + timedelta(days=1)) - 1

            def progress(value: float) -> None:
                self._update_job(
                    job_id,
                    stage="逐 Tick 批量回放",
                    progress=min(0.92, 0.05 + max(0.0, value) * 0.87),
                )

            def warmup(count: int) -> None:
                self._update_job(
                    job_id,
                    stage=f"预热 ATR 指标（{count} 根 15m K 线）",
                    progress=0.04,
                )

            metadata, results = run_parameter_grid(
                settings,
                instrument,
                parameters,
                start_ms=start_ms,
                end_ms=end_ms,
                direction=request["direction"],
                progress_callback=progress,
                warmup_callback=warmup,
            )
            self._update_job(job_id, stage="生成每日与每月报告", progress=0.95)
            report = _build_research_report(request, metadata, results)
            self._write_report(report)
            self._update_job(
                job_id,
                status="completed",
                stage="回测报告已生成",
                progress=1.0,
                completed_at=datetime.now(UTC).isoformat(),
                report_id=report["id"],
                message=(
                    f"完成 {len(results)} 个 ATR 候选，"
                    f"预热 {metadata['warmup_bars']} 根 {metadata['warmup_interval_minutes']}m K 线"
                ),
            )
        except Exception as exc:
            self._fail_job(job_id, exc)

    def _run_factor_mining(self, job_id: str, instrument_id: str) -> None:
        self._update_job(
            job_id,
            status="running",
            stage="读取只读 BTC 历史数据",
            progress=0.01,
            started_at=datetime.now(UTC).isoformat(),
        )
        try:
            bars, funding = load_market(self.settings.database_path, instrument_id)
            direction_options = (
                ("long_only",)
                if instrument_id == "soxl_perp"
                else (
                    "long_only",
                    "long_short",
                )
            )

            def progress(stage: str, value: float) -> None:
                self._update_job(job_id, stage=stage, progress=value)

            payload = mine_instrument(
                bars,
                funding,
                FactorMiningConfig(
                    instrument_id=instrument_id,
                    direction_options=direction_options,
                ),
                progress_callback=progress,
            )
            generated_at = datetime.now(UTC)
            report_id = f"factor-{instrument_id}-{generated_at.strftime('%Y%m%d-%H%M%S-%f')}"
            payload["id"] = report_id
            payload["generated_at"] = generated_at.isoformat()
            output_dir = self.factor_report_dir / generated_at.date().isoformat()
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{report_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            (output_dir / f"{report_id}.md").write_text(
                _factor_report_markdown(payload), encoding="utf-8"
            )
            selected = payload["selected"]
            self._update_job(
                job_id,
                status="completed",
                stage="因子挖掘报告已生成",
                progress=1.0,
                completed_at=datetime.now(UTC).isoformat(),
                report_id=report_id,
                message=(
                    f"完成 {payload['candidate_count']} 个因果公式候选；"
                    f"开发期合格 {payload['development_eligible_count']} 个"
                ),
                result={
                    "instrument_id": instrument_id,
                    "candidate_count": payload["candidate_count"],
                    "development_eligible_count": payload["development_eligible_count"],
                    "selected_id": selected["id"] if selected else None,
                    "selected_formula": selected["formula"]["display"] if selected else None,
                    "status": payload["decision"]["status"],
                },
            )
        except Exception as exc:
            self._fail_job(job_id, exc)

    def _run_deep_factor(self, job_id: str, instruments: tuple[str, ...], python: Path) -> None:
        self._update_job(
            job_id,
            status="running",
            stage="启动 GPU 深度因子 worker",
            progress=0.01,
            started_at=datetime.now(UTC).isoformat(),
        )
        command = [
            str(python),
            str(self.settings.project_root / "scripts" / "train_deep_factor_v2.py"),
            "--database",
            str(self.settings.database_path),
            "--output-root",
            str(self.settings.project_root),
            "--report-root",
            str(self.deep_factor_report_dir),
        ]
        environment = os.environ.copy()
        source_path = str(self.settings.project_root / "src")
        environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
        completed_report: dict[str, Any] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=self.settings.project_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "progress":
                    self._update_job(
                        job_id,
                        stage=event.get("stage", "GPU 深度因子计算"),
                        progress=float(event.get("progress", 0.0)),
                    )
                elif event.get("event") == "error":
                    raise RuntimeError(str(event.get("error", "deep factor worker failed")))
                elif event.get("event") == "completed":
                    completed_report = event.get("report")
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"deep factor worker exited with code {return_code}")
            if completed_report is None:
                raise RuntimeError("deep factor worker did not return a report")
            self._update_job(
                job_id,
                status="completed",
                stage="GPU 深度因子报告已生成",
                progress=1.0,
                completed_at=datetime.now(UTC).isoformat(),
                report_id=completed_report["id"],
                message=(
                    f"完成 {', '.join(instruments)}；"
                    f"模型决策 {completed_report['decision']['status']}"
                ),
                result={
                    "instruments": list(instruments),
                    "status": completed_report["decision"]["status"],
                    "portfolio_selection": completed_report["portfolio_selection"][
                        "selection_status"
                    ],
                },
            )
        except Exception as exc:
            self._fail_job(job_id, exc)

    def _preset(self, instrument_id: str) -> ResearchPreset:
        preset = self.presets.get(instrument_id)
        if preset is None:
            raise ValueError(f"unknown research instrument: {instrument_id}")
        return preset

    def _write_report(self, report: dict[str, Any]) -> None:
        json_path = self.report_dir / f"{report['id']}.json"
        markdown_path = self.report_dir / f"{report['id']}.md"
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        markdown_path.write_text(_report_markdown(report), encoding="utf-8")

    def _fail_job(self, job_id: str, exc: Exception) -> None:
        self._update_job(
            job_id,
            status="failed",
            stage="任务失败",
            completed_at=datetime.now(UTC).isoformat(),
            error=str(exc),
        )


def _date_start_ms(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _factor_report_markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]
    lines = [
        f"# {payload['instrument_id']} Causal Factor Mining",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "Research only. Formula discovery cannot create orders or change paper/live settings.",
        "",
        "## Result",
        "",
        f"- Candidate formulas: {payload['candidate_count']:,}.",
        f"- Development eligible: {payload['development_eligible_count']:,}.",
        f"- Decision: `{payload['decision']['status']}`.",
        "",
    ]
    if selected is not None:
        lines.extend(
            [
                "## Selected Development Formula",
                "",
                f"- ID: `{selected['id']}`",
                f"- Formula: `{selected['formula']['display']}`",
                "",
                "| Split | Return | Max drawdown | Trades |",
                "|---|---:|---:|---:|",
            ]
        )
        for name in ("train", "validation", "confirmation"):
            result = selected[name]
            lines.append(
                f"| {name} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
                f"{result['completed_trades']} |"
            )
        lines.append("")
    lines.extend([payload["decision"]["reason"], ""])
    return "\n".join(lines)


def _period_rows(
    daily_equity: list[dict[str, Any]], initial_equity: float
) -> tuple[list[dict], list[dict]]:
    daily = []
    previous = initial_equity
    for row in daily_equity:
        equity = float(row["equity"])
        daily.append(
            {
                "label": row["date"],
                "timestamp_ms": row["timestamp_ms"],
                "start_equity": previous,
                "end_equity": equity,
                "net_profit": equity - previous,
                "return": equity / previous - 1 if previous else 0.0,
            }
        )
        previous = equity
    monthly_ends: dict[str, dict[str, Any]] = {}
    for row in daily:
        monthly_ends[row["label"][:7]] = row
    monthly = []
    previous = initial_equity
    for label, row in monthly_ends.items():
        equity = float(row["end_equity"])
        monthly.append(
            {
                "label": label,
                "start_equity": previous,
                "end_equity": equity,
                "net_profit": equity - previous,
                "return": equity / previous - 1 if previous else 0.0,
            }
        )
        previous = equity
    return daily, monthly


def _build_research_report(
    request: dict[str, Any], metadata: dict[str, Any], results: list
) -> dict[str, Any]:
    report_id = f"atr-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    candidates = []
    for index, result in enumerate(results, start=1):
        daily, monthly = _period_rows(result.daily_equity, result.initial_equity)
        raw = asdict(result)
        raw.pop("daily_equity", None)
        candidates.append(
            {
                "id": f"candidate-{index}",
                "parameters": {
                    "atr_period": result.atr_period,
                    "atr_multiplier": result.atr_multiplier,
                    "profit_activation_atr": result.profit_activation_atr,
                    "profit_trailing_atr": result.profit_trailing_atr,
                    "continuation_reentry_atr": result.continuation_reentry_atr,
                },
                "metrics": raw,
                "daily": daily,
                "monthly": monthly,
            }
        )
    candidates.sort(
        key=lambda item: (item["metrics"]["net_return"], item["metrics"]["max_drawdown"]),
        reverse=True,
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    request_payload = {
        **request,
        "start_date": request["start_date"].isoformat(),
        "end_date": request["end_date"].isoformat(),
    }
    return {
        "id": report_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "instrument_id": request["instrument_id"],
        "symbol": metadata["symbol"],
        "request": request_payload,
        "metadata": metadata,
        "candidates": candidates,
        "best_candidate_id": candidates[0]["id"],
    }


def _report_markdown(report: dict[str, Any]) -> str:
    request = report["request"]
    lines = [
        f"# ATR Backtest {report['symbol']}",
        "",
        f"Generated: {report['generated_at']}",
        f"Range: {request['start_date']} to {request['end_date']} UTC",
        f"Direction: {request['direction']}",
        (
            f"Warm-up: {report['metadata']['warmup_bars']} closed "
            f"{report['metadata']['warmup_interval_minutes']}m bars"
        ),
        "",
        "| Rank | ATR | Multiplier | Return | Max drawdown | Trades | Win rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate in report["candidates"]:
        metrics = candidate["metrics"]
        win_rate = "--" if metrics["win_rate"] is None else f"{metrics['win_rate']:.2%}"
        lines.append(
            f"| {candidate['rank']} | {metrics['atr_period']} | {metrics['atr_multiplier']:.2f} | "
            f"{metrics['net_return']:.2%} | {metrics['max_drawdown']:.2%} | "
            f"{metrics['completed_trades']} | {win_rate} |"
        )
    best = report["candidates"][0]
    lines.extend(
        [
            "",
            "## Best Candidate Monthly Returns",
            "",
            "| Month | Return | End equity |",
            "|---|---:|---:|",
        ]
    )
    for row in best["monthly"]:
        lines.append(f"| {row['label']} | {row['return']:.2%} | {row['end_equity']:,.2f} |")
    lines.extend(
        [
            "",
            "## Best Candidate Daily Returns",
            "",
            "| Date | Return | End equity |",
            "|---|---:|---:|",
        ]
    )
    for row in best["daily"]:
        lines.append(f"| {row['label']} | {row['return']:.2%} | {row['end_equity']:,.2f} |")
    return "\n".join(lines) + "\n"
