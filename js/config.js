export const CONFIG = {
  version: '0.5',
  initialCash: 10_000_000,
  scanIntervalMs: 5000,
  protectedSymbols: ['068270'],
  nh: { backendBaseUrl: '', preferLiveQuotes: false, readOnly: true },
  dayTrading: {
    entryVolumeRatio: 1.5, rsiMin: 50, rsiMax: 75, adxMin: 18,
    breakoutLookback: 8, stopLossPct: 0.010, takeProfitPct: 0.015,
    trailingTriggerPct: 0.010, trailingGapPct: 0.006, shadowBars: 12,
  },
  risk: {
    maxOrderWon: 300_000, maxPositionPct: 0.12, maxPositions: 3,
    dailyLossPct: 0.01, maxLossStreak: 2, riskPerTradePct: 0.0035,
  },
  profitSplit: { reinvestPct: 0.40, vaultPct: 0.50, reservePct: 0.10 }
};
