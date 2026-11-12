# Equities paper replay

The replay starts with independent £50 virtual accounts for US and UK stocks. It uses
simulated fractional shares, with a configurable maximum GBP stake per entry. Add an
`ETF` starting balance to enable ETF instruments and report their returns separately.
The Kraken runner and its existing account and price history are independent.

## Run a sample

With Python 3.12, run these from the repository root. The sample uses synthetic
prices and FX rates for test instruments.

```sh
python -m unittest -v test_equities.py
python -c 'import json; from test_equities import sample; print(json.dumps(sample(), indent=2))' > sample-equities.json
python equities.py sample-equities.json --as-of 2026-11-30T22:00:00Z > equities-ledger.json
```

The GitHub workflow is manual and read-only. Once available on the default
branch, select a branch containing an authorized input file and supply the file
path and cutoff. Download the ledger from the run artifacts. Keep licensed or
private datasets local unless their terms permit storing them in the repository
and its Actions artifacts.

## Import data

The JSON fields below are required unless marked optional. Keep input snapshots
immutable to reproduce a report. The runner makes no network calls.

| Field | Contents |
| --- | --- |
| `capital_gbp` | Starting balances keyed by `US`, `UK`, and optionally `ETF` |
| `stake_gbp` | Maximum cash allocated per buy, including costs |
| `sessions` | Arrays keyed by venue (`NYSE`, `NASDAQ`, `LSE`) containing `open` and `close` timestamps for regular sessions |
| `fx` | Rows with `available_at` and `gbp_per_usd` (GBP received per USD before simulated FX costs) |
| `costs` | One row per `VENUE:SYMBOL` with `spread_bps`, `slippage_bps`, `commission_bps`, `fx_bps`, `buy_charge_bps`, and `sell_charge_bps` |
| `instruments` | Rows with `venue`, `symbol`, `kind` (`stock` or `etf`), `unit` (`USD`, `GBP`, or `GBX`), `bars`, and `actions` |
| `bars` | Per-instrument rows with exchange-local `date`, raw `open` and `close` prices, and `open_available_at` and `close_available_at` |
| `actions` | Per-instrument rows with exchange-local effective `date`, `available_at`, `type` (`split` or `dividend`), and `amount` (new shares per old share, or gross cash per share in the quote unit) |

Use ISO 8601 timestamps with explicit UTC offsets. Supply a complete regular-session
calendar for the replay interval, including early closes and omitting exchange
holidays. Obtain unadjusted, timestamped opening and closing prints plus complete
corporate-action history. Only use a watchlist whose membership was known at the
time being replayed if drawing conclusions about historical strategy performance.
Enter the publication or first observation time for FX and price inputs. A date
alone cannot establish availability. Set cost assumptions per instrument, including
any applicable buy and sell charges. The sample rates are illustrative.

## Simulation rules

- Two consecutive available session closes are required. An uptick queues a buy
  when flat, and a downtick queues a sell when holding. The signal must be known
  strictly before the next session open.
- A historical opening fill models a decision already queued before that open.
  The opening print must be available by the report cutoff and within five
  minutes of the open. The replay skips fills with missing prices or FX, drops
  the pending decision, and clears the momentum comparison after a missing close.
- USD conversions use the last rate available at the fill, up to four calendar
  days old. GBX prices and dividends are divided by 100. Each fill records the
  spread-adjusted and slipped price, rate, fees, and signed GBP cash movement.
- Splits adjust holdings and reset the comparison. Dividends are modeled as
  gross cash credited on the ex-date. The replay rejects actions disclosed after
  their effective open. The model covers splits and cash dividends, with no
  dividend withholding, payment lag, or other corporate-action types.
- Unrealized P&L uses the latest observed mark and shows its timestamp. Trade
  fees include commission, FX markup and transaction charges. Spread and
  slippage are reflected in the execution price. The replay ledger is rebuilt
  on each run, so retries cannot append duplicate trades.

The data import is for historical replay. Automated forward paper trading remains
disabled pending a permitted, reliable feed and validation of its actual latency.
