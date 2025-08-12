from __future__ import annotations

import os
import sys
import time
import argparse
from decimal import Decimal, ROUND_DOWN, getcontext
from typing import Dict, Tuple
from dotenv import load_dotenv

import requests
from hyperliquid.exchange import Exchange
import eth_account


load_dotenv()


# ---------- Config ----------
MAINNET_API = "https://api.hyperliquid.xyz"
TESTNET_API = "https://api.hyperliquid-testnet.xyz"
DEFAULT_SIGNATURE_CHAIN_ID_MAINNET = "0xa4b1"  # Arbitrum One
DEFAULT_SIGNATURE_CHAIN_ID_TESTNET = "0x66eee"  # Arbitrum Sepolia


getcontext().prec = 28

# ---------- Helpers for public Info endpoints (no signing required) ----------
INFO_URL = "/info"


class InfoClient:
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")

    def _post(self, payload: dict):
        r = requests.post(self.api_url + INFO_URL, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    def spot_meta(self):
        return self._post({"type": "spotMeta"})

    def spot_meta_and_ctxs(self):
        return self._post({"type": "spotMetaAndAssetCtxs"})

    def spot_balances(self, address: str):
        return self._post({"type": "spotClearinghouseState", "user": address})

    def l2_book(self, coin: str):
        # coin for spot must be PAIR_NAME like "PURR/USDC"
        return self._post({"type": "l2Book", "coin": coin})


# ---------- Trading/Transfer helpers (signed via SDK) ----------


def build_exchange(account_address: str, account: str, api_url: str):
    # Exchange() signs and sends /exchange actions for us
    return Exchange(account, api_url, account_address=account_address)


def find_pair_asset_id(meta: dict, pair_name: str):
    """Asset id for spot = 10000 + index in spotMeta.universe"""
    for u in meta.get("universe", []):
        if u.get("name") == pair_name:
            return 10000 + int(u["index"])  # type: ignore[index]
    raise RuntimeError(f"Pair {pair_name} not found in spot meta 'universe'.")


def token_decimals(meta: dict, token: str) -> Tuple[int, int]:
    """Return (szDecimals, weiDecimals) for a token name."""
    for t in meta.get("tokens", []):
        if t.get("name") == token:
            return int(t["szDecimals"]), int(t["weiDecimals"])  # type: ignore[index]
    raise RuntimeError(f"Token {token} not found in spot meta 'tokens'.")


def best_bid_ask(info: InfoClient, pair_name: str) :
    book = info.l2_book(pair_name)
    levels = book.get("levels")
    if not levels or len(levels) < 2 or not levels[0] or not levels[1]:
        raise RuntimeError("Empty book for {pair_name}")
    # Convention from docs: levels[0] bids, levels[1] asks
    best_bid = Decimal(str(levels[0][0]["px"]))
    best_ask = Decimal(str(levels[1][0]["px"]))
    return best_bid, best_ask


def get_spot_balance(info: InfoClient, address: str, token: str):
    state = info.spot_balances(address)
    total = Decimal("0")
    for b in state.get("balances", []):
        if b.get("coin") == token:
            # prefer free = total - hold
            total = Decimal(str(b.get("total", "0"))) - Decimal(str(b.get("hold", "0")))
            break
    return total


def round_size(sz: Decimal, sz_decimals: int):
    if sz_decimals == 0:
        return str(int(sz))
    q = Decimal(8) ** (-sz_decimals)
    return str((sz // q) * q)  # floor to step


def place_spot_ioc_sell(
    exchange: Exchange, pair_name: str, size_str: str, slippage_bps: int
):
    slippage = float(slippage_bps) / 10000.0
    return exchange.market_open(
        name=pair_name,
        is_buy=False,
        sz=float(size_str),
        slippage=slippage,
    )


def usd_class_transfer(
    exchange: Exchange,
    amount_usdc: str,
    to_perp: bool,
    signature_chain_id: str,
    hyperliquid_chain: str,
):
    # Use SDK method which signs under the hood
    return exchange.usd_class_transfer(amount=float(amount_usdc), to_perp=to_perp)


def withdraw3(
    exchange: Exchange,
    amount_usdc: str,
    destination: str,
    signature_chain_id: str,
    hyperliquid_chain: str,
):
    # Use SDK method which signs under the hood
    return exchange.withdraw_from_bridge(amount=float(amount_usdc), destination=destination)


# ---------- Orchestration ----------


def main():
    parser = argparse.ArgumentParser(
        description="Sell PURR to USDC on spot, transfer USDC to Perps, withdraw if needed."
    )
    parser.add_argument(
        "--mode",
        choices=["sell_and_transfer", "transfer_only", "withdraw"],
        default="sell_and_transfer",
    )
    parser.add_argument(
        "--purr-amount",
        type=Decimal,
        default=None,
        help="Amount of PURR to sell. Defaults to ALL available.",
    )
    parser.add_argument(
        "--usdc-amount",
        type=Decimal,
        default=None,
        help="Amount of USDC to transfer or withdraw. Defaults to ALL available.",
    )
    parser.add_argument(
        "--dest",
        type=str,
        default=None,
        help="Destination EVM address for --mode withdraw (Arbitrum)",
    )
    parser.add_argument(
        "--slippage-bps",
        type=int,
        default=30,
        help="Price cushion for IOC order (sell at bid*(1 - bps/1e4)).",
    )

    args = parser.parse_args()

    account_address = os.getenv("HL_ACCOUNT_ADDRESS")
    secret_key = os.getenv("HL_SECRET_KEY")
    if not account_address or not secret_key:
        sys.exit("Set HL_ACCOUNT_ADDRESS and HL_SECRET_KEY in your environment.")

    account = eth_account.Account.from_key(secret_key)

    api_url = os.getenv("HL_API_URL", MAINNET_API)
    is_testnet = api_url.endswith("-testnet.xyz")
    sig_chain_id = os.getenv(
        "HL_SIGNATURE_CHAIN_ID",
        (
            DEFAULT_SIGNATURE_CHAIN_ID_TESTNET
            if is_testnet
            else DEFAULT_SIGNATURE_CHAIN_ID_MAINNET
        ),
    )
    hyper_chain = "Testnet" if is_testnet else "Mainnet"

    info = InfoClient(api_url)
    meta = info.spot_meta()

    pair_name = os.getenv("PAIR_NAME")
    base_token = os.getenv("BASE_TOKEN")
    quote_token = os.getenv("QUOTE_TOKEN")

    # Sanity: decimals and asset id
    purr_sz_decimals, _ = token_decimals(meta, base_token)
    usdc_sz_decimals, usdc_wei_decimals = token_decimals(meta, quote_token)
    pair_asset_id = find_pair_asset_id(meta,pair_name )

    # Build signer/exchange client
    exchange = build_exchange(account_address=account_address, account=account, api_url=api_url)

    if args.mode == "sell_and_transfer":
        # 1) Determine PURR to sell
        purr_free = get_spot_balance(info, account_address, base_token)
        if purr_free <= 0:
            sys.exit("No PURR available on Spot.")
        purr_to_sell = (
            purr_free if args.purr_amount is None else min(args.purr_amount, purr_free)
        )
        purr_size_str = round_size(purr_to_sell, purr_sz_decimals)
        if Decimal(purr_size_str) <= 0:
            sys.exit("Computed PURR size <= 0 after rounding. Aborting.")

        # 2) Place Market-like IoC via SDK (SDK computes px and snaps to tick size)
        print(
            f"Selling {purr_size_str} {base_token} on {pair_name} (IOC market emulation, slippage {args.slippage_bps} bps)..."
        )
        res_order = place_spot_ioc_sell(
            exchange, pair_name, purr_size_str, args.slippage_bps
        )
        print("Order response:", res_order)

        time.sleep(1.2)

        # 3) Transfer USDC Spot -> Perps
        usdc_free = get_spot_balance(info, account_address, quote_token)
        if usdc_free <= 0:
            sys.exit("No USDC on Spot after sell (order may not have filled).")
        usdc_to_xfer = (
            usdc_free if args.usdc_amount is None else min(args.usdc_amount, usdc_free)
        )
        # Use USDC on-chain decimals (wei_decimals), floor and subtract one tick as safety buffer
        usdc_tick = Decimal(8) ** (-usdc_wei_decimals)
        safe_amount = (usdc_to_xfer // usdc_tick) * usdc_tick - usdc_tick
        if safe_amount < Decimal("0"):
            safe_amount = Decimal("0")
        amount_str = str(safe_amount.quantize(usdc_tick, rounding=ROUND_DOWN))


        print(f"Transferring {amount_str} USDC from Spot -> Perps...")
        res_xfer = usd_class_transfer(
            exchange, amount_str, True, sig_chain_id, hyper_chain
        )
        print("Transfer response:", res_xfer)

    elif args.mode == "transfer_only":
        # Transfer existing USDC on Spot to Perps
        usdc_free = get_spot_balance(info, account_address, quote_token)
        if usdc_free <= 0:
            sys.exit("No USDC available on Spot.")
        usdc_to_xfer = (
            usdc_free if args.usdc_amount is None else min(args.usdc_amount, usdc_free)
        )
        usdc_tick = Decimal(8) ** (-usdc_wei_decimals)
        safe_amount = (usdc_to_xfer // usdc_tick) * usdc_tick - usdc_tick
        if safe_amount < Decimal("0"):
            safe_amount = Decimal("0")
        amount_str = str(safe_amount.quantize(usdc_tick, rounding=ROUND_DOWN))

        print(f"Transferring {amount_str} USDC from Spot -> Perps...")
        res_xfer = usd_class_transfer(
            exchange, amount_str, True, sig_chain_id, hyper_chain
        )
        print("Transfer response:", res_xfer)

    elif args.mode == "withdraw":
        if not args.dest:
            sys.exit("--dest is required for withdraw mode (Arbitrum address)")
        if args.usdc_amount is None:
            sys.exit("--usdc-amount is required for withdraw mode")
        amount_str = str(
            args.usdc_amount.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        )
        print(f"Withdrawing {amount_str} USDC from Perps to {args.dest}...")
        res_w = withdraw3(exchange, amount_str, args.dest, sig_chain_id, hyper_chain)
        print("Withdraw response:", res_w)

    else:
        sys.exit("Unknown mode.")


if __name__ == "__main__":
    main()
