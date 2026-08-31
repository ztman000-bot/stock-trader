"""Capital policy shared by future Paper/Simulation execution adapters.
No broker/order calls live here.
"""
REINVEST_PCT=.40
PROFIT_VAULT_PCT=.50
RISK_RESERVE_PCT=.10

def split_realized_profit(amount):
 p=max(0.0,float(amount));return {'profit':round(p,2),'reinvest':round(p*REINVEST_PCT,2),'profitVault':round(p*PROFIT_VAULT_PCT,2),'riskReserve':round(p*RISK_RESERVE_PCT,2),'policy':'40/50/10'}
def executable_capital(base_capital,realized_profit):
 s=split_realized_profit(realized_profit);return {'baseCapital':round(float(base_capital),2),'reinvestedProfit':s['reinvest'],'executableCapital':round(float(base_capital)+s['reinvest'],2),'protectedProfitVault':s['profitVault'],'riskReserve':s['riskReserve'],'orderAccess':False}
