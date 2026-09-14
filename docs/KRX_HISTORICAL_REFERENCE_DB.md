# KRX Historical Reference DB — Research-Only Bootstrap

## Purpose

`server/krx_historical_reference.py` adds a **separate** historical Korean-market reference database for long-horizon research.

It is intentionally isolated from `server/market_data.db` so imported daily/reference data cannot be mistaken for NH PLUG intraday evidence.

- Control strategy: **v0.8.0 LOCKED**
- REAL ORDER: **OFF**
- Paper entry/exit/risk semantics: **unchanged**
- Celltrion protection: **unchanged**
- no NH credential access
- no broker/order API call
- no live-session network call

The default database is:

```text
server/historical_market.db
```

It is already covered by the repository `server/*.db` ignore rule and must never be committed.

## Why this exists

The NH 5m/1m database remains the source for Control/Paper/Exit-Replay fidelity. The historical reference DB solves different research problems:

1. long-run Korean-market regime context;
2. KOSPI/KOSDAQ daily liquidity and market-cap history;
3. point-in-time universe reconstruction;
4. delisted-symbol retention to reduce survivorship bias;
5. explicit separation of adjusted vs unadjusted prices;
6. provenance tracking for every imported dataset.

Historical daily data **does not replace** Paper/Shadow forward samples or official NH 5m/1m provenance.

## Official KRX source boundary

KRX Data Marketplace OPEN API states that its listed stock/index services provide data from 2010 onward (KONEX from 2013 where applicable). KRX also requires login/auth-key application and service-use approval before Open API use.

Official references:

- https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
- https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp
- https://data.krx.co.kr/contents/MDC/STAT/issue/MDCSTAT238.jsp

This repository therefore does **not** scrape KRX pages and does **not** embed a KRX auth key. Import only data the operator is entitled to use under the relevant KRX terms.

## Schema

The separate SQLite DB contains:

```text
daily_bars          # stock daily OHLCV/value/market cap
index_daily         # KOSPI/KOSDAQ/index daily series
symbol_history      # point-in-time symbol/listing metadata
 delisted_symbols   # delisted names/dates/reasons
corporate_actions   # split/rights/dividend/etc reference events
import_runs         # each import execution
 data_provenance    # source/date-range/row-count audit
```

`adjusted=0` and `adjusted=1` daily prices are stored separately and must not be silently mixed.

## Initialize / inspect

From the repository root:

```bash
python server/krx_historical_reference.py init
python server/krx_historical_reference.py status
```

Before data is imported, `readyForResearch` is expected to be `false`.

## Import a KRX-style daily stock export

CSV and JSON are supported. CSV decoding attempts UTF-8 BOM, CP949, then EUC-KR.

```bash
python server/krx_historical_reference.py import \
  --file /path/to/kospi_daily.csv \
  --type daily_bars \
  --market KOSPI \
  --source KRX_EXPORT
```

Accepted stock field aliases include common KRX-style names such as:

```text
BAS_DD / 일자
ISU_SRT_CD / 종목코드
ISU_ABBRV / 종목명
TDD_OPNPRC / 시가
TDD_HGPRC / 고가
TDD_LWPRC / 저가
TDD_CLSPRC / 종가
ACC_TRDVOL / 거래량
ACC_TRDVAL / 거래대금
MKTCAP / 시가총액
LIST_SHRS / 상장주식수
```

## Import index data

```bash
python server/krx_historical_reference.py import \
  --file /path/to/kospi_index.csv \
  --type index_daily \
  --market KOSPI \
  --source KRX_EXPORT
```

## Import delisted-symbol history

The KRX Data Marketplace provides a delisted-symbol status screen. A lawfully obtained CSV/export can be loaded as:

```bash
python server/krx_historical_reference.py import \
  --file /path/to/delisted.csv \
  --type delisted_symbols \
  --source KRX_EXPORT
```

Korean column aliases supported include:

```text
종목코드, 종목명, 시장구분, 상장일, 폐지일, 폐지사유
```

When delisted or symbol-history data exists, `status()` reports `survivorshipSupport=true`.

## Provenance rules

Every import records:

- dataset type;
- source;
- source reference/file name;
- adjusted/unadjusted flag;
- import time;
- input/written/rejected row counts;
- first/last available date and row count.

Do not label third-party data as `KRX_EXPORT` unless the file actually came from an approved KRX export/API response. Use an explicit source name such as `VENDOR_X` or `PUBLIC_DATASET_Y`.

## Research pipeline

The intended pipeline is:

```text
KRX/reference daily history
        ↓
market regime + point-in-time universe + survivorship research
        ↓
NH official 5m validation
        ↓
NH 1m Exit Replay
        ↓
Paper / Shadow forward evidence
        ↓
OOS → Walk-Forward → Lockbox → stress tests
```

No historical-reference result may automatically mutate Control v0.8.0.

## What is deliberately NOT implemented yet

- automatic KRX Open API downloading;
- auth-key storage;
- background downloads during the live session;
- direct daily-data filtering of Control entries;
- automatic strategy mutation;
- real-order execution.

Those require separate review after lawful data access is configured and enough forward evidence exists.
