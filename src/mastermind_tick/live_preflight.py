"""Read-only and Binance test-order preflight for the SOXLB live path."""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal

from mastermind_tick.binance_spot import BinanceSpotClient
from mastermind_tick.config import load_settings
from mastermind_tick.live_spot import load_live_credentials


async def run(config_path: str, *, test_order: bool) -> int:
    settings = load_settings(config_path)
    live = settings.live_spot
    instrument = next(item for item in settings.instruments if item.id == live.instrument_id)
    api_key, api_secret, credential_error = load_live_credentials(
        live.credentials_path,
        live.api_key_env,
        live.api_secret_env,
    )
    client = BinanceSpotClient(
        live.api_base_url,
        api_key,
        api_secret,
        recv_window_ms=live.recv_window_ms,
    )
    result: dict[str, object] = {
        "symbol": instrument.symbol,
        "credentials_present": client.has_credentials,
        "credential_error": credential_error,
        "real_orders_sent": False,
    }
    try:
        rules = await client.symbol_rules(instrument.symbol)
        book = await client.book_ticker(instrument.symbol)
        result["public"] = {
            "status": rules.status,
            "market_order_allowed": rules.market_order_allowed,
            "quote_order_quantity_allowed": rules.quote_order_quantity_allowed,
            "quantity_step": str(rules.quantity_step),
            "minimum_quantity": str(rules.minimum_quantity),
            "minimum_notional": str(rules.minimum_notional),
            "bid_price": str(book["bidPrice"]),
            "ask_price": str(book["askPrice"]),
        }
        if not client.has_credentials:
            result["signed"] = {"ok": False, "reason": "credentials_missing"}
            print(json.dumps(result, indent=2))
            return 2

        offset = await client.sync_time()
        account, open_orders, restrictions = await asyncio.gather(
            client.account(),
            client.open_orders(instrument.symbol),
            client.api_restrictions(),
        )
        balances = {item["asset"]: item for item in account.get("balances", [])}
        base = balances.get(rules.base_asset, {"free": "0", "locked": "0"})
        quote = balances.get(rules.quote_asset, {"free": "0", "locked": "0"})
        reading_enabled = bool(restrictions.get("enableReading", False))
        result["signed"] = {
            "ok": reading_enabled,
            "can_trade": bool(account.get("canTrade", False)),
            "api_reading_enabled": reading_enabled,
            "spot_trading_permitted": bool(
                restrictions.get("enableSpotAndMarginTrading", False)
            ),
            "withdrawals_enabled": bool(restrictions.get("enableWithdrawals", False)),
            "ip_restricted": bool(restrictions.get("ipRestrict", False)),
            "server_time_offset_ms": offset,
            "open_order_count": len(open_orders),
            "base_balance_present": Decimal(str(base["free"])) + Decimal(str(base["locked"])) > 0,
            "quote_balance_present": (
                Decimal(str(quote["free"])) + Decimal(str(quote["locked"])) > 0
            ),
        }
        if test_order:
            test_notional = max(rules.minimum_notional * Decimal("1.02"), Decimal("5.10"))
            await client.market_buy(
                instrument.symbol,
                test_notional,
                "mmtick-preflight-buy",
                test=True,
            )
            result["test_order"] = {
                "ok": True,
                "endpoint": "/api/v3/order/test",
                "side": "BUY",
                "quote_order_quantity": str(test_notional),
                "real_orders_sent": False,
            }
        print(json.dumps(result, indent=2))
        return 0 if reading_enabled else 1
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Binance Spot SOXLB access without sending a real order"
    )
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument(
        "--test-order",
        action="store_true",
        help="Call Binance /api/v3/order/test; this endpoint never creates an order",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.config, test_order=args.test_order)))


if __name__ == "__main__":
    main()
