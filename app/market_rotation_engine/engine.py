"""Pure market-rotation calculations with no infrastructure dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Bar:
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class SectorProfile:
    code: str
    label: str
    proxy: str
    benchmark: str
    symbols: tuple[str, ...]


class RotationEngine:
    """Rank sectors from relative returns, breadth and dollar-volume activity."""

    def analyze(
        self, profiles: tuple[SectorProfile, ...], history: dict[str, tuple[Bar, ...]]
    ) -> tuple[dict[str, object], ...]:
        results: list[dict[str, object]] = []
        for profile in profiles:
            proxy = history.get(profile.proxy, ())
            benchmark = history.get(profile.benchmark, ())
            if len(proxy) < 21 or len(benchmark) < 21:
                continue
            evidence = [self._symbol(symbol, history.get(symbol, ())) for symbol in profile.symbols]
            evidence = [item for item in evidence if item is not None]
            if not evidence:
                continue
            relative = self._return(proxy, 20) - self._return(benchmark, 20)
            positive = Decimal(sum(bool(item["return_5d"] > 0) for item in evidence))
            above = Decimal(sum(bool(item["above_sma20"]) for item in evidence))
            count = Decimal(len(evidence))
            breadth_positive = positive * 100 / count
            breadth_above = above * 100 / count
            rvol = sum((item["rvol"] for item in evidence), Decimal()) / count
            score = min(
                Decimal("100"),
                max(
                    Decimal(),
                    Decimal("50") + relative * 3 + (breadth_positive - 50) / 2 + (rvol - 1) * 10,
                ),
            ).quantize(Decimal("0.01"))
            state = (
                "INFLOW"
                if score >= 70
                else "ACCUMULATING"
                if score >= 58
                else "OUTFLOW"
                if score < 40
                else "NEUTRAL"
            )
            ranked = sorted(evidence, key=lambda item: item["score"], reverse=True)
            results.append(
                {
                    "code": profile.code,
                    "label": profile.label,
                    "proxy": profile.proxy,
                    "benchmark": profile.benchmark,
                    "score": score,
                    "state": state,
                    "relative_20d": relative.quantize(Decimal("0.01")),
                    "breadth_positive": breadth_positive.quantize(Decimal("0.01")),
                    "breadth_above_sma20": breadth_above.quantize(Decimal("0.01")),
                    "rvol": rvol.quantize(Decimal("0.01")),
                    "evidence": tuple(ranked),
                }
            )
        return tuple(sorted(results, key=lambda item: item["score"], reverse=True))

    def _symbol(self, symbol: str, bars: tuple[Bar, ...]) -> dict[str, object] | None:
        if len(bars) < 21 or any(bar.close <= 0 for bar in bars[-21:]):
            return None
        latest = bars[-1]
        avg_dollar = sum((bar.close * bar.volume for bar in bars[-21:-1]), Decimal()) / 20
        dollar = latest.close * latest.volume
        rvol = dollar / avg_dollar if avg_dollar > 0 else Decimal()
        ret5 = self._return(bars, 5)
        ret20 = self._return(bars, 20)
        sma20 = sum((bar.close for bar in bars[-20:]), Decimal()) / 20
        score = min(Decimal("100"), max(Decimal(), Decimal("50") + ret20 * 2 + (rvol - 1) * 10))
        return {
            "symbol": symbol,
            "price": latest.close,
            "return_1d": self._return(bars, 1),
            "return_5d": ret5,
            "return_20d": ret20,
            "dollar_volume": dollar,
            "average_dollar_volume_20": avg_dollar,
            "rvol": rvol,
            "above_sma20": latest.close > sma20,
            "above_sma50": latest.close
            > (sum((bar.close for bar in bars[-50:]), Decimal()) / min(50, len(bars))),
            "score": score.quantize(Decimal("0.01")),
        }

    @staticmethod
    def _return(bars: tuple[Bar, ...], periods: int) -> Decimal:
        base = bars[-periods - 1].close
        return (bars[-1].close / base - 1) * 100 if base else Decimal()
