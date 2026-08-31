export const CONFIG = {
  initialCash: 10_000_000,
  scanIntervalMs: 5000,
  risk: {
    maxOrderWon: 100_000,
    maxPositionPct: 0.10,
    maxPositions: 5,
    dailyLossPct: 0.01,
    maxLossStreak: 3,
  },
  strategy: {
    buyScore: 84,
    sellScore: 62,
    takeProfitPct: 0.035,
    stopLossPct: 0.020,
  }
};
