"""Shared market-state classifier v0.17.7.

Single source of truth for NORMAL / CAUTION / RED research labels.
This module is pure: no broker calls, no DB writes, and no trading decisions.
"""

RULE_VERSION = "market-state-v1"


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def classify_market_state(proxy_ret_pct=None, breadth=None, median_ret_pct=None):
    """Return the existing Market Lab regime rule plus reasons/confidence.

    Thresholds intentionally match the v0.17.0 Market Research Lab so this
    refactor does not alter validated strategy/research behavior.
    """
    proxy = _num(proxy_ret_pct)
    br = _num(breadth)
    med = _num(median_ret_pct)
    reasons = []

    if proxy is not None:
        if proxy < -1.0 or (proxy < -0.70 and br is not None and br < 0.40):
            label = "RED"
            if proxy < -1.0:
                reasons.append("proxy<-1.0%")
            if proxy < -0.70 and br is not None and br < 0.40:
                reasons.append("proxy<-0.70% & breadth<40%")
        elif proxy < -0.30 or (br is not None and br < 0.45) or (med is not None and med < -0.25):
            label = "CAUTION"
            if proxy < -0.30:
                reasons.append("proxy<-0.30%")
            if br is not None and br < 0.45:
                reasons.append("breadth<45%")
            if med is not None and med < -0.25:
                reasons.append("medianRet<-0.25%")
        else:
            label = "NORMAL"
            reasons.append("proxy/breadth/median stable")
    elif br is not None and med is not None:
        if br < 0.35 and med < -0.50:
            label = "RED"
            reasons.append("breadth<35% & medianRet<-0.50%")
        elif br < 0.45 or med < -0.25:
            label = "CAUTION"
            if br < 0.45:
                reasons.append("breadth<45%")
            if med < -0.25:
                reasons.append("medianRet<-0.25%")
        else:
            label = "NORMAL"
            reasons.append("breadth/median stable")
    else:
        label = "UNKNOWN"
        reasons.append("insufficient market-state inputs")

    available = sum(x is not None for x in (proxy, br, med))
    confidence = round(available / 3, 2)
    if label == "UNKNOWN":
        confidence = min(confidence, 0.34)

    return {
        "label": label,
        "confidence": confidence,
        "reasons": reasons,
        "inputs": {"proxyRetPct": proxy, "breadth": br, "medianRetPct": med},
        "ruleVersion": RULE_VERSION,
    }


def classify_label(proxy_ret_pct=None, breadth=None, median_ret_pct=None):
    return classify_market_state(proxy_ret_pct, breadth, median_ret_pct)["label"]
