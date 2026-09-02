# Fund Holding X-Ray

[简体中文](README.md) | English

Original author: Roland (xxkyuss). QUANTSKILLS publication maintainer: abgyjaguo. This community project does not claim official certification or endorsement.

Fund Holding X-Ray uses the `panda_data` SDK to turn one ETF or fund symbol into a terminal, HTML, or JSON research report. It covers fund details, estimated top holdings, CR5/CR10/HHI concentration, sector and heuristic theme exposure, style classification, data-driven observations, and risk notes.

## Quick start

```bash
python -m pip install -r requirements.txt
python scripts/fund_xray.py --symbol 510300.SH
```

Credentials are read interactively or from a local `.env` file. Never commit credentials, local data, or generated reports. The main data methods are `get_fund_detail` and `get_fund_etf_constituents`; optional enrichment uses `get_stock_daily` and `get_stock_detail`.

## Important limitations

- Constituent weights are estimates based on quantity times closing price, normalized over the available basket; they are not official disclosed fund weights.
- Only the latest available constituent snapshot is compared. Results across rebalance dates are not directly comparable.
- Theme and style labels are heuristic and must be checked against the prospectus and index methodology.
- Cross-border or incomplete constituent coverage can degrade the report to count-based statistics.
- Outputs are for research and education only and are not investment advice.

## Outputs and tests

The CLI can produce a rich terminal table, an ECharts HTML report, or normalized JSON. Run the offline test suite with:

```bash
python scripts/test_fund_xray.py
```

Real API smoke tests run only when PandaAI credentials are configured. See [README.md](README.md) for the complete methodology, command options, field conventions, and output schema.

## License

Distributed under [GPL-3.0-only](LICENSE). The original MIT copyright and permission notice for Roland (xxkyuss) is preserved in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
