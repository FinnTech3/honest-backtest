# Sources

- **Kind:** reimplementation
- **Started:** 2026-08-03

## Data

Daily bars from the Yahoo Finance chart API
(`query1.finance.yahoo.com/v8/finance/chart/`), which needs no key. Ten years
for AAPL, MSFT, KO and SPY are committed under `tests/fixtures` so the suite
runs offline and the published numbers reproduce exactly.

The committed files are a small sample retained for reproducibility, not a
redistribution of the dataset. Anyone wanting more should pull their own.

An important property of this feed, verified rather than assumed: its OHLC is
already adjusted for splits, and `adjclose` differs from `close` only by
dividends. `Series.looks_split_adjusted()` checks this, because a feed without
that property needs fills priced differently and the error is silent.

## Studied

No code was copied from anything. The biases modelled here, look-ahead,
survivorship, transaction costs, and square-root market impact, are standard
results in the market microstructure and backtesting literature rather than
anyone's implementation.

The square-root impact law is the one substantive borrowing: impact scaling
with the square root of participation rather than linearly is a long-standing
empirical finding, and the constant is a plausible round number rather than a
calibrated estimate.

## License obligations

None. Original work.
