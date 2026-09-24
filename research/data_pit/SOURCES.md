# Data sources for research/data_pit

| File | Source | License |
|---|---|---|
| `sp500_hist.csv` | Daily S&P 500 membership snapshots, from [fja05680/sp500](https://github.com/fja05680/sp500) (`S&P 500 Historical Components & Changes`) | MIT (notice below) |
| `sp500_start_end.csv` | Membership windows per ticker, from [fja05680/sp500](https://github.com/fja05680/sp500) (`sp500_ticker_start_end.csv`) | MIT (notice below) |
| `sectors_wiki.json` | Sector of each current constituent, derived from Wikipedia's "List of S&P 500 companies" (GICS sector), with semiconductors split out of tech and labels renamed | Wikipedia text is CC BY-SA 4.0 |
| `v10_grid.csv` | Output of `research/backtest_v10_pit.py --full` | This repository's license |

Price files (`*.parquet`) are not versioned: they are downloaded from Databento by
`research/pit_fetch.py` and are subject to Databento's terms.

## fja05680/sp500 license

```
MIT License

Copyright (c) 2019-2020 Farrell J. Aultman <fja0568@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
