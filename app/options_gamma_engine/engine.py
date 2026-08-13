"""Deterministic options-gamma aggregation without provider or transport dependencies."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    GammaAssessment,
    GammaDirectionalBias,
    GammaExpirationAssessment,
    NamedValue,
    PatternDirection,
)

from .models import OptionContractSnapshot, OptionsGammaContext

ZERO = Decimal()
ONE = Decimal("1")
HUNDRED = Decimal("100")
CONTRACT_SIZE = Decimal("100")
ONE_PERCENT = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


class OptionsGammaEngine:
    """Estimate public-OI gamma levels and state their sign assumption explicitly."""

    engine_id = "options-gamma"
    engine_version = "1.0.0"
    methodology_version = "1.0.0"

    def evaluate(self, context: OptionsGammaContext) -> GammaAssessment:
        contracts = tuple(context.contracts)
        usable = tuple(
            item
            for item in contracts
            if item.open_interest is not None
            and item.open_interest > ZERO
            and item.gamma is not None
            and item.gamma > ZERO
        )
        coverage = ZERO if not contracts else Decimal(len(usable)) / Decimal(len(contracts))
        warnings = _warnings(
            contracts,
            usable,
            provider_warnings=context.provider_warnings,
        )
        nearest_dte = min(
            (
                max(0, (item.expiration_date - context.generated_at.date()).days)
                for item in usable
            ),
            default=45,
        )
        expires_at = context.generated_at + _ttl(nearest_dte)
        if not usable:
            return GammaAssessment(
                symbol=context.symbol.strip().upper(),
                generated_at=context.generated_at,
                expires_at=expires_at,
                engine_version=self.engine_version,
                methodology_version=self.methodology_version,
                spot_price=context.spot_price,
                spot_as_of=context.spot_as_of,
                expiration_from=context.expiration_from,
                expiration_to=context.expiration_to,
                open_interest_as_of=None,
                status="UNAVAILABLE",
                quality_score=ZERO,
                contract_count=len(contracts),
                usable_contract_count=0,
                coverage_ratio=_rounded(coverage),
                gamma_regime="UNKNOWN",
                directional_bias="UNRELIABLE",
                net_gamma_exposure=ZERO,
                absolute_gamma_exposure=ZERO,
                net_gamma_ratio=None,
                dealer_sign_assumption="CALL_POSITIVE_PUT_NEGATIVE",
                warnings=warnings,
                context_hash=_context_hash(context),
            )

        groups: dict[date, list[OptionContractSnapshot]] = defaultdict(list)
        for item in usable:
            groups[item.expiration_date].append(item)
        raw_expirations = [
            _expiration_assessment(context, tuple(groups[key])) for key in sorted(groups)
        ]
        total_influence = sum(
            (item.absolute_gamma_exposure for item in raw_expirations), ZERO
        )
        expirations = tuple(
            item.model_copy(
                update={
                    "influence_weight": _rounded(
                        ZERO
                        if total_influence <= ZERO
                        else item.absolute_gamma_exposure / total_influence
                    )
                }
            )
            for item in raw_expirations
        )
        aggregate = _aggregate(context, usable)
        net = aggregate.net_gamma_exposure
        absolute = aggregate.absolute_gamma_exposure
        ratio = None if absolute <= ZERO else _rounded(net / absolute)
        regime = (
            "UNKNOWN"
            if ratio is None
            else "NEGATIVE"
            if ratio <= Decimal("-0.15")
            else "POSITIVE"
            if ratio >= Decimal("0.15")
            else "MIXED"
        )
        quality = _quality(context, usable, coverage)
        status = "AVAILABLE" if quality >= Decimal("70") else "DEGRADED"
        max_pain = aggregate.max_pain
        absolute_wall = aggregate.absolute_gamma_wall
        pin_risk = regime != "NEGATIVE" and any(
            _near(context.spot_price, level, Decimal("1"))
            for level in (max_pain, absolute_wall)
            if level is not None
        )
        acceleration_risk = regime == "NEGATIVE" and any(
            _near(context.spot_price, level, Decimal("2"))
            for level in (aggregate.gamma_flip, aggregate.call_wall, aggregate.put_wall)
            if level is not None
        )
        bias = _directional_bias(context.spot_price, max_pain, quality)
        oi_dates = tuple(
            item.open_interest_date for item in usable if item.open_interest_date is not None
        )
        return GammaAssessment(
            symbol=context.symbol.strip().upper(),
            generated_at=context.generated_at,
            expires_at=expires_at,
            engine_version=self.engine_version,
            methodology_version=self.methodology_version,
            spot_price=_rounded(context.spot_price),
            spot_as_of=context.spot_as_of,
            expiration_from=context.expiration_from,
            expiration_to=context.expiration_to,
            open_interest_as_of=max(oi_dates, default=None),
            status=status,
            quality_score=quality,
            contract_count=len(contracts),
            usable_contract_count=len(usable),
            coverage_ratio=_rounded(coverage),
            gamma_regime=regime,
            directional_bias=bias,
            net_gamma_exposure=net,
            absolute_gamma_exposure=absolute,
            net_gamma_ratio=ratio,
            call_wall=aggregate.call_wall,
            put_wall=aggregate.put_wall,
            absolute_gamma_wall=absolute_wall,
            max_pain=max_pain,
            gamma_flip=aggregate.gamma_flip,
            expected_move_low=expirations[0].expected_move_low,
            expected_move_high=expirations[0].expected_move_high,
            pin_risk=pin_risk,
            acceleration_risk=acceleration_risk,
            dealer_sign_assumption="CALL_POSITIVE_PUT_NEGATIVE",
            expirations=expirations,
            warnings=warnings,
            context_hash=_context_hash(context),
        )


def gamma_analysis_from_assessment(assessment: GammaAssessment) -> AnalysisResult:
    """Project the rich assessment into the standard cross-engine analysis contract."""

    direction = {
        "UP": PatternDirection.BULLISH,
        "DOWN": PatternDirection.BEARISH,
        "NEUTRAL": PatternDirection.NEUTRAL,
        "UNRELIABLE": PatternDirection.NEUTRAL,
    }[assessment.directional_bias]
    verdict = {
        "AVAILABLE": AnalysisVerdict.WATCH,
        "DEGRADED": AnalysisVerdict.CAUTION,
        "UNAVAILABLE": AnalysisVerdict.INSUFFICIENT_DATA,
    }[assessment.status]
    metrics = (
        NamedValue(name="reference_price", value=assessment.spot_price),
        NamedValue(name="expires_at", value=assessment.expires_at),
        NamedValue(name="gamma_status", value=assessment.status),
        NamedValue(name="gamma_quality_score", value=assessment.quality_score),
        NamedValue(name="gamma_regime", value=assessment.gamma_regime),
        NamedValue(name="gamma_directional_bias", value=assessment.directional_bias),
        NamedValue(name="gamma_coverage_ratio", value=assessment.coverage_ratio),
        NamedValue(name="gamma_net_ratio", value=assessment.net_gamma_ratio),
        NamedValue(name="gamma_call_wall", value=assessment.call_wall),
        NamedValue(name="gamma_put_wall", value=assessment.put_wall),
        NamedValue(name="gamma_absolute_wall", value=assessment.absolute_gamma_wall),
        NamedValue(name="gamma_max_pain", value=assessment.max_pain),
        NamedValue(name="gamma_flip", value=assessment.gamma_flip),
        NamedValue(name="gamma_expected_move_low", value=assessment.expected_move_low),
        NamedValue(name="gamma_expected_move_high", value=assessment.expected_move_high),
        NamedValue(name="gamma_pin_risk", value=assessment.pin_risk),
        NamedValue(name="gamma_acceleration_risk", value=assessment.acceleration_risk),
        NamedValue(name="gamma_assessment_id", value=str(assessment.assessment_id)),
    )
    reasons = [
        f"options_gamma_status:{assessment.status.lower()}",
        f"options_gamma_regime:{assessment.gamma_regime.lower()}",
    ]
    if assessment.pin_risk:
        reasons.append("options_gamma_pin_risk")
    if assessment.acceleration_risk:
        reasons.append("options_gamma_acceleration_risk")
    reasons.extend(f"options_gamma_warning:{item}" for item in assessment.warnings)
    return AnalysisResult(
        engine_id=OptionsGammaEngine.engine_id,
        engine_version=assessment.engine_version,
        symbol=assessment.symbol,
        horizon=AnalysisHorizon.OPTIONS_GAMMA,
        as_of=assessment.generated_at,
        verdict=verdict,
        direction=direction,
        score=assessment.quality_score,
        confidence=_rounded(assessment.quality_score / HUNDRED),
        reasons=tuple(reasons),
        metrics=metrics,
        context_hash=assessment.context_hash,
    )


def _expiration_assessment(
    context: OptionsGammaContext,
    contracts: tuple[OptionContractSnapshot, ...],
) -> GammaExpirationAssessment:
    aggregate = _aggregate(context, contracts)
    expiration = contracts[0].expiration_date
    return GammaExpirationAssessment(
        expiration_date=expiration,
        days_to_expiration=max(0, (expiration - context.generated_at.date()).days),
        contract_count=len(contracts),
        usable_contract_count=len(contracts),
        open_interest=_rounded(sum((item.open_interest or ZERO for item in contracts), ZERO)),
        net_gamma_exposure=aggregate.net_gamma_exposure,
        absolute_gamma_exposure=aggregate.absolute_gamma_exposure,
        call_wall=aggregate.call_wall,
        put_wall=aggregate.put_wall,
        absolute_gamma_wall=aggregate.absolute_gamma_wall,
        max_pain=aggregate.max_pain,
        gamma_flip=aggregate.gamma_flip,
        expected_move_low=aggregate.expected_move_low,
        expected_move_high=aggregate.expected_move_high,
        influence_weight=ZERO,
    )


class _Aggregate:
    def __init__(
        self,
        *,
        net_gamma_exposure: Decimal,
        absolute_gamma_exposure: Decimal,
        call_wall: Decimal | None,
        put_wall: Decimal | None,
        absolute_gamma_wall: Decimal | None,
        max_pain: Decimal | None,
        gamma_flip: Decimal | None,
        expected_move_low: Decimal | None,
        expected_move_high: Decimal | None,
    ) -> None:
        self.net_gamma_exposure = net_gamma_exposure
        self.absolute_gamma_exposure = absolute_gamma_exposure
        self.call_wall = call_wall
        self.put_wall = put_wall
        self.absolute_gamma_wall = absolute_gamma_wall
        self.max_pain = max_pain
        self.gamma_flip = gamma_flip
        self.expected_move_low = expected_move_low
        self.expected_move_high = expected_move_high


def _aggregate(
    context: OptionsGammaContext,
    contracts: tuple[OptionContractSnapshot, ...],
) -> _Aggregate:
    by_strike: dict[Decimal, dict[str, Decimal]] = defaultdict(
        lambda: {"call": ZERO, "put": ZERO, "absolute": ZERO}
    )
    net = ZERO
    absolute = ZERO
    for item in contracts:
        exposure = _gamma_exposure(item, context.spot_price)
        signed = exposure if item.option_type == "call" else -exposure
        net += signed
        absolute += exposure
        bucket = by_strike[item.strike_price]
        bucket[item.option_type] += exposure
        bucket["absolute"] += exposure
    call_wall = _wall(by_strike, "call")
    put_wall = _wall(by_strike, "put")
    absolute_wall = _wall(by_strike, "absolute")
    expected_move = _expected_move(context.spot_price, contracts)
    return _Aggregate(
        net_gamma_exposure=_rounded(net),
        absolute_gamma_exposure=_rounded(absolute),
        call_wall=_optional_rounded(call_wall),
        put_wall=_optional_rounded(put_wall),
        absolute_gamma_wall=_optional_rounded(absolute_wall),
        max_pain=_optional_rounded(_max_pain(contracts)),
        gamma_flip=_optional_rounded(_gamma_flip(context, contracts)),
        expected_move_low=(
            _rounded(max(Decimal("0.0001"), context.spot_price - expected_move))
            if expected_move is not None
            else None
        ),
        expected_move_high=(
            _rounded(context.spot_price + expected_move)
            if expected_move is not None
            else None
        ),
    )


def _gamma_exposure(item: OptionContractSnapshot, spot: Decimal) -> Decimal:
    return (
        (item.gamma or ZERO)
        * (item.open_interest or ZERO)
        * CONTRACT_SIZE
        * spot
        * spot
        * ONE_PERCENT
    )


def _wall(by_strike: dict[Decimal, dict[str, Decimal]], key: str) -> Decimal | None:
    candidates = ((strike, values[key]) for strike, values in by_strike.items())
    positive = tuple(item for item in candidates if item[1] > ZERO)
    return max(positive, key=lambda item: (item[1], -item[0]))[0] if positive else None


def _max_pain(contracts: tuple[OptionContractSnapshot, ...]) -> Decimal | None:
    strikes = sorted({item.strike_price for item in contracts})
    if not strikes:
        return None
    payouts: list[tuple[Decimal, Decimal]] = []
    for candidate in strikes:
        payout = ZERO
        for item in contracts:
            intrinsic = (
                max(candidate - item.strike_price, ZERO)
                if item.option_type == "call"
                else max(item.strike_price - candidate, ZERO)
            )
            payout += intrinsic * (item.open_interest or ZERO) * CONTRACT_SIZE
        payouts.append((candidate, payout))
    return min(payouts, key=lambda item: (item[1], item[0]))[0]


def _expected_move(
    spot: Decimal, contracts: tuple[OptionContractSnapshot, ...]
) -> Decimal | None:
    strike = min({item.strike_price for item in contracts}, key=lambda value: abs(value - spot))
    values: dict[str, Decimal] = {}
    for option_type in ("call", "put"):
        matching = tuple(
            item
            for item in contracts
            if item.strike_price == strike and item.option_type == option_type
        )
        prices = tuple(_market_price(item) for item in matching)
        usable = tuple(item for item in prices if item is not None and item > ZERO)
        if usable:
            values[option_type] = usable[0]
    if set(values) == {"call", "put"}:
        return values["call"] + values["put"]
    ivs = tuple(
        item.implied_volatility
        for item in contracts
        if item.implied_volatility is not None and item.implied_volatility > ZERO
    )
    if not ivs:
        return None
    days = (
        max(
            1,
            (contracts[0].expiration_date - contracts[0].snapshot_at.date()).days,
        )
        if contracts[0].snapshot_at
        else 1
    )
    average_iv = sum(ivs, ZERO) / Decimal(len(ivs))
    return Decimal(str(float(spot * average_iv) * math.sqrt(days / 365)))


def _market_price(item: OptionContractSnapshot) -> Decimal | None:
    if (
        item.bid_price is not None
        and item.ask_price is not None
        and item.ask_price >= item.bid_price
    ):
        return (item.bid_price + item.ask_price) / Decimal("2")
    return item.latest_trade_price


def _gamma_flip(
    context: OptionsGammaContext,
    contracts: tuple[OptionContractSnapshot, ...],
) -> Decimal | None:
    previous_spot: float | None = None
    previous_net: float | None = None
    base = float(context.spot_price)
    for index in range(81):
        hypothetical = base * (0.8 + index * 0.005)
        net = 0.0
        for item in contracts:
            iv = float(item.implied_volatility or ZERO)
            if iv <= 0:
                continue
            days = max(0.5, (item.expiration_date - context.generated_at.date()).days + 0.5)
            gamma = _black_scholes_gamma(
                spot=hypothetical,
                strike=float(item.strike_price),
                maturity=days / 365,
                volatility=iv,
            )
            sign = 1.0 if item.option_type == "call" else -1.0
            net += gamma * float(item.open_interest or ZERO) * 100 * hypothetical**2 * 0.01 * sign
        if previous_net is not None and net != 0 and previous_net * net < 0:
            assert previous_spot is not None
            weight = abs(previous_net) / (abs(previous_net) + abs(net))
            return Decimal(str(previous_spot + (hypothetical - previous_spot) * weight))
        previous_spot, previous_net = hypothetical, net
    return None


def _black_scholes_gamma(
    *, spot: float, strike: float, maturity: float, volatility: float
) -> float:
    if min(spot, strike, maturity, volatility) <= 0:
        return 0.0
    root = math.sqrt(maturity)
    d1 = (math.log(spot / strike) + 0.5 * volatility**2 * maturity) / (volatility * root)
    density = math.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)
    return density / (spot * volatility * root)


def _quality(
    context: OptionsGammaContext,
    usable: tuple[OptionContractSnapshot, ...],
    coverage: Decimal,
) -> Decimal:
    coverage_points = min(Decimal("80"), coverage * Decimal("80"))
    oi_dates = tuple(item.open_interest_date for item in usable if item.open_interest_date)
    oi_points = (
        Decimal("10")
        if oi_dates and (context.generated_at.date() - max(oi_dates)).days <= 3
        else ZERO
    )
    snapshots = tuple(item.snapshot_at for item in usable if item.snapshot_at is not None)
    snapshot_points = (
        Decimal("10")
        if snapshots
        and context.generated_at - max(snapshots) <= timedelta(minutes=30)
        else ZERO
    )
    return _rounded(min(HUNDRED, coverage_points + oi_points + snapshot_points))


def _warnings(
    contracts: tuple[OptionContractSnapshot, ...],
    usable: tuple[OptionContractSnapshot, ...],
    *,
    provider_warnings: tuple[str, ...] = (),
) -> tuple[str, ...]:
    output = list(provider_warnings)
    if not contracts:
        output.append("empty_chain")
    if not usable:
        output.append("no_usable_contracts")
    if any(item.open_interest is None or item.open_interest <= ZERO for item in contracts):
        output.append("missing_open_interest")
    if any(item.gamma is None or item.gamma <= ZERO for item in contracts):
        output.append("missing_gamma")
    if contracts and len(usable) < len(contracts):
        output.append("incomplete_chain")
    return tuple(dict.fromkeys(output))


def _directional_bias(
    spot: Decimal, max_pain: Decimal | None, quality: Decimal
) -> GammaDirectionalBias:
    if quality < Decimal("70") or max_pain is None:
        return "UNRELIABLE"
    distance = (max_pain - spot) / spot * HUNDRED
    if distance >= Decimal("1"):
        return "UP"
    if distance <= Decimal("-1"):
        return "DOWN"
    return "NEUTRAL"


def _near(spot: Decimal, level: Decimal, percent: Decimal) -> bool:
    return abs((spot - level) / level * HUNDRED) <= percent


def _ttl(days_to_expiration: int) -> timedelta:
    if days_to_expiration == 0:
        return timedelta(minutes=5)
    if days_to_expiration <= 7:
        return timedelta(minutes=20)
    return timedelta(minutes=60)


def _context_hash(context: OptionsGammaContext) -> str:
    payload = {
        "symbol": context.symbol.strip().upper(),
        "spot": str(context.spot_price),
        "spot_as_of": context.spot_as_of.isoformat(),
        "provider_warnings": context.provider_warnings,
        "contracts": [
            (
                item.symbol,
                item.expiration_date.isoformat(),
                str(item.strike_price),
                item.option_type,
                str(item.open_interest),
                str(item.gamma),
                str(item.implied_volatility),
                str(item.bid_price),
                str(item.ask_price),
            )
            for item in context.contracts
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _optional_rounded(value: Decimal | None) -> Decimal | None:
    return _rounded(value) if value is not None else None
