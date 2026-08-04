from mastermind_tick.live_store import LiveStore


def test_live_store_is_idempotent_for_orders_and_fills(tmp_path) -> None:
    store = LiveStore(tmp_path / "live.db")
    created = store.create_order(
        client_order_id="mmtick-buy-one",
        account_id="soxlb_live",
        symbol="SOXLBUSDT",
        side="BUY",
        reason="cross",
        signal_price="100",
        signal_at_ms=1000,
        requested_quantity=None,
        requested_quote_quantity="10",
    )
    duplicate = store.create_order(
        client_order_id="mmtick-buy-one",
        account_id="soxlb_live",
        symbol="SOXLBUSDT",
        side="BUY",
        reason="cross",
        signal_price="100",
        signal_at_ms=1000,
        requested_quantity=None,
        requested_quote_quantity="10",
    )

    assert created
    assert not duplicate
    store.update_order(
        "mmtick-buy-one",
        status="FILLED",
        updated_at_ms=1100,
        submitted_at_ms=1050,
        payload={"orderId": 7, "executedQty": "0.1", "cummulativeQuoteQty": "10"},
    )
    trade = {
        "id": 9,
        "orderId": 7,
        "time": 1100,
        "price": "100",
        "qty": "0.1",
        "quoteQty": "10",
        "commission": "0.0001",
        "commissionAsset": "SOXLB",
    }
    assert store.upsert_fill(
        account_id="soxlb_live",
        symbol="SOXLBUSDT",
        side="BUY",
        client_order_id="mmtick-buy-one",
        payload=trade,
    )
    assert not store.upsert_fill(
        account_id="soxlb_live",
        symbol="SOXLBUSDT",
        side="BUY",
        client_order_id="mmtick-buy-one",
        payload=trade,
    )
    assert store.order("mmtick-buy-one")["status"] == "FILLED"
    assert len(store.fills("soxlb_live")) == 1


def test_live_store_persists_strategy_and_balance_state(tmp_path) -> None:
    store = LiveStore(tmp_path / "live.db")
    store.save_strategy_state("soxlb_live", {"trailing_stop": "99"}, 1000)
    store.save_balance_snapshot(
        account_id="soxlb_live",
        timestamp_ms=1000,
        base_free="1",
        base_locked="0",
        quote_free="50",
        quote_locked="0",
        reference_price="100",
        equity_quote="150",
    )
    store.set_metadata("managed_position", "true", 1000)

    reopened = LiveStore(tmp_path / "live.db")
    assert reopened.strategy_state("soxlb_live") == {"trailing_stop": "99"}
    assert reopened.latest_balance("soxlb_live")["equity_quote"] == "150"
    assert reopened.metadata("managed_position") == "true"
    assert (tmp_path / "live.db").stat().st_mode & 0o777 == 0o600


def test_live_store_records_external_cash_flows_idempotently(tmp_path) -> None:
    store = LiveStore(tmp_path / "live.db")
    values = {
        "flow_id": "deposit-one",
        "account_id": "soxlb_live",
        "timestamp_ms": 2000,
        "amount_quote": "1600",
        "flow_type": "DEPOSIT",
        "reason": "operator_confirmed_deposit",
        "source": "operator_adjustment",
        "created_at_ms": 3000,
    }

    assert store.record_cash_flow(**values)
    assert not store.record_cash_flow(**values)
    assert store.cash_flows("soxlb_live")[0]["amount_quote"] == "1600"
