# Hyperliquid Recovery: Sell PURR on Spot → Move USDC to Perps → Withdraw to Arbitrum

This script helps users who got their Hyperliquid account restricted (e.g., after using a VPN) recover funds by:
1) Selling a spot position (e.g., PURR/USDC),
2) Transferring resulting USDC from Spot to Perps balance, and
3) Optionally withdrawing to an Arbitrum address.

It was built for the "stuck funds" scenario and aims to provide a safe, automated path to exit.

- Not affiliated with Hyperliquid.
- Use at your own risk. Make sure you comply with local laws and the Hyperliquid Terms of Service.

## What it does

- Reads your account address and private key from `.env`.
- Uses Hyperliquid public `/info` endpoints to fetch metadata and balances.
- Places an Immediate-or-Cancel “market-like” sell on spot via the official Python SDK.
- Transfers available USDC from Spot → Perps.
- Optionally initiates a bridge withdrawal from Perps to an Arbitrum address.

## Requirements

- Python 3.10+ recommended
- A wallet that is connected to your Hyperliquid account
- Your private key for that wallet (hex string)
- Network access to Hyperliquid API

## Install

```bash
git clone https://github.com/yourname/hyperliquid-recoverty.git
cd hyperliquid-recoverty
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```


## Configure environment

Copy the example env and fill in values:

```1:6:.env.example
HL_ACCOUNT_ADDRESS=
HL_SECRET_KEY=

PAIR_NAME = "PURR/USDC"
BASE_TOKEN = "PURR"
QUOTE_TOKEN = "USDC"
```

- HL_ACCOUNT_ADDRESS: your EVM address used on Hyperliquid (checksummed, e.g., 0xABC…)
- HL_SECRET_KEY: the hex private key for that address (e.g., 0xabc123…)
- PAIR_NAME: spot pair to sell (default `"PURR/USDC"`)
- BASE_TOKEN: base asset symbol (default `"PURR"`)
- QUOTE_TOKEN: quote asset symbol (default `"USDC"`)

Optional variables (not in the example, but supported):
- HL_API_URL:
  - Mainnet (default): `https://api.hyperliquid.xyz`
  - Testnet: `https://api.hyperliquid-testnet.xyz`
- HL_SIGNATURE_CHAIN_ID:
  - Mainnet default: `0xa4b1` (Arbitrum One)
  - Testnet default: `0x66eee` (Arbitrum Sepolia)

Create `.env` in the project root and paste your values there. Keep `.env` private and never commit it.

## Usage

General syntax:
```bash
python3 hl_purr_to_perps.py --mode {sell_and_transfer|transfer_only|withdraw} [options]
```

Common options:
- `--purr-amount <Decimal>`: amount of `BASE_TOKEN` to sell on spot. If omitted, sells ALL available.
- `--usdc-amount <Decimal>`: amount of USDC to transfer/withdraw. If omitted, uses ALL available (with a tiny safety buffer).
- `--slippage-bps <int>`: cushion for the IOC “market-like” order; default 30 bps.

### 1) Sell PURR on Spot and transfer USDC to Perps (recommended)

Sell all available PURR and move resulting USDC to Perps:
```bash
python hl_purr_to_perps.py --mode sell_and_transfer
```

Sell a specific PURR amount (e.g., 123.45 PURR):
```bash
python hl_purr_to_perps.py --mode sell_and_transfer --purr-amount 123.45
```

Increase slippage if fills fail on illiquid books (example: 75 bps):
```bash
python hl_purr_to_perps.py --mode sell_and_transfer --slippage-bps 75
```

### 2) Transfer-only (Spot → Perps)

Move existing Spot USDC to Perps:
```bash
python hl_purr_to_perps.py --mode transfer_only
```

Specify an amount (e.g., 100.25 USDC):
```bash
python hl_purr_to_perps.py --mode transfer_only --usdc-amount 100.25
```

### 3) Withdraw from Perps to an Arbitrum address

Withdraw 50.12345678 USDC from Perps to your Arbitrum wallet:
```bash
python hl_purr_to_perps.py --mode withdraw --usdc-amount 50.12345678 --dest 0xYourArbitrumAddress
```

Decimals are handled safely (floored to 8 decimals for USDC).

## Testnet

To try on testnet, add to your `.env`:
HL_API_URL=https://api.hyperliquid-testnet.xyz

The script will auto-adjust signature chain id defaults for testnet.

## Security

- Never share your private key or seed phrase. The script only needs the private key corresponding to `HL_ACCOUNT_ADDRESS`.
- Keep `.env` outside version control.
- Consider creating and funding a fresh wallet dedicated to recovery if you’re worried about exposure.

## Troubleshooting

- “Pair PURR/USDC not found”:
  - Verify `PAIR_NAME` matches a live spot market.
- “Token PURR/USDC not found”:
  - Check `BASE_TOKEN` and `QUOTE_TOKEN` spelling.
- “No PURR available on Spot.”:
  - Ensure your PURR balance exists on Spot, not Perps.
- “No USDC on Spot after sell (order may not have filled).”:
  - The IOC order didn’t fill; increase `--slippage-bps` or check order book liquidity.
- HTTP 401/403:
  - Re-check `HL_SECRET_KEY` and `HL_ACCOUNT_ADDRESS`. Ensure the wallet is the one tied to your Hyperliquid account.
- Precision errors:
  - The script floors amounts to token decimals and subtracts one tick for safety. If you see “Computed size <= 0”, reduce amount slightly.

## Example recovery flow (what most people need)

1) Configure `.env` with your HL address and private key.
2) Run:
   ```bash
   python hl_purr_to_perps.py --mode sell_and_transfer
   ```
   This sells your PURR into USDC on Spot and moves USDC to Perps.
3) Withdraw from Perps to your Arbitrum address:
   ```bash
   python hl_purr_to_perps.py --mode withdraw --usdc-amount <amount> --dest 0xYourArbitrumAddress
   ```
4) If you prefer a third-party bridge (e.g., deBridge), you can withdraw to your own address and then bridge normally using their UI.

## Notes

- Default network is Mainnet. Override with `HL_API_URL`.
- Default slippage is 30 bps. Illiquid books may need more.
- The script waits ~1.2s after placing the spot order before transferring, to allow fills to settle.

## License

MIT. See `LICENSE`.