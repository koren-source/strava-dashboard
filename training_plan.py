"""Deterministic cycling training-plan logic.

The planner owns the workout structure and arithmetic. It uses Strava's Relative
Effort when available, falls back to estimated power load, and compares real
seven-day windows. Presentation layers may rephrase the explanation, but they
must not invent a different session or physiological diagnosis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


Ride = dict[str, Any]
Athlete = dict[str, Any]


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _ride_date(ride: Ride) -> datetime:
    raw = str(ride.get("date") or ride.get("start_date_local") or "")
    if not raw:
        raise ValueError("Ride is missing a date")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _days_since(ride: Ride, now: datetime) -> int:
    return max((now.date() - _ride_date(ride).date()).days, 0)


def estimate_ride_load(ride: Ride, ftp: int) -> int:
    """Return comparable load points without pretending every ride has TSS.

    Strava Relative Effort is preferred because many outdoor rides do not have
    normalized power. When power is available, estimated TSS is the fallback.
    Duration is the final fallback so unmetered rides still influence the plan.
    """

    relative_effort = _as_number(ride.get("suffer_score"))
    if relative_effort is not None and relative_effort > 0:
        return round(relative_effort)

    watts = _as_number(ride.get("normalized_watts")) or _as_number(
        ride.get("avg_watts")
    )
    minutes = _as_number(ride.get("moving_mins")) or 0
    if watts and minutes and ftp > 0:
        intensity_factor = watts / ftp
        return round((minutes / 60) * intensity_factor**2 * 100)

    return round(minutes * 0.5)


def classify_ride_load(ride: Ride, ftp: int) -> str:
    load = estimate_ride_load(ride, ftp)
    minutes = _as_number(ride.get("moving_mins")) or 0
    if load >= 120 or minutes >= 180:
        return "high"
    if load >= 60 or minutes >= 90:
        return "moderate"
    return "low"


def compute_training_context(
    rides: list[Ride],
    athlete: Athlete,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not rides:
        raise ValueError("At least one ride is required")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    ftp = int(athlete.get("ftp") or 237)
    ordered = sorted(rides, key=_ride_date, reverse=True)
    last_ride = ordered[0]
    days_since_last_ride = _days_since(last_ride, current_time)
    last_meaningful_ride = next(
        (
            ride
            for ride in ordered
            if classify_ride_load(ride, ftp) in {"moderate", "high"}
        ),
        None,
    )
    days_since_last_meaningful_ride = (
        _days_since(last_meaningful_ride, current_time)
        if last_meaningful_ride is not None
        else None
    )

    windows = {"recent": [], "prior": []}
    for ride in ordered:
        age = _days_since(ride, current_time)
        if 0 <= age < 7:
            windows["recent"].append(ride)
        elif 7 <= age < 14:
            windows["prior"].append(ride)

    recent_load = sum(estimate_ride_load(ride, ftp) for ride in windows["recent"])
    prior_load = sum(estimate_ride_load(ride, ftp) for ride in windows["prior"])

    if not windows["prior"] or prior_load < 40:
        load_trend = "insufficient history"
    elif recent_load > prior_load * 1.2:
        load_trend = "rising"
    elif recent_load < prior_load * 0.8:
        load_trend = "falling"
    else:
        load_trend = "stable"

    return {
        "days_since_last_ride": days_since_last_ride,
        "days_since_last_meaningful_ride": days_since_last_meaningful_ride,
        "last_meaningful_ride_load": (
            classify_ride_load(last_meaningful_ride, ftp)
            if last_meaningful_ride is not None
            else None
        ),
        "last_ride_load": classify_ride_load(last_ride, ftp),
        "last_ride_points": estimate_ride_load(last_ride, ftp),
        "recent_7d_load": recent_load,
        "prior_7d_load": prior_load,
        "recent_7d_rides": len(windows["recent"]),
        "prior_7d_rides": len(windows["prior"]),
        "high_load_rides_7d": sum(
            classify_ride_load(ride, ftp) == "high" for ride in windows["recent"]
        ),
        "load_trend": load_trend,
        "last_ride": last_ride,
    }


def _watts(ftp: int, low: int, high: int) -> tuple[int, int]:
    return round(ftp * low / 100), round(ftp * high / 100)


def _last_ride_summary(context: dict[str, Any]) -> str:
    ride = context["last_ride"]
    parts = [f"{int(ride.get('moving_mins') or 0)} min"]
    elevation = _as_number(ride.get("elev_ft"))
    if elevation:
        parts.append(f"{round(elevation):,} ft climbing")
    relative_effort = _as_number(ride.get("suffer_score"))
    if relative_effort:
        parts.append(f"Relative Effort {round(relative_effort)}")
    return " · ".join(parts)


def _basis(
    context: dict[str, Any],
    decision: str,
) -> dict[str, Any]:
    days = context["days_since_last_ride"]
    return {
        "decision": decision,
        "signals": [
            {
                "label": "Recovery",
                "value": f"{days} day{'s' if days != 1 else ''} since last ride",
            },
            {
                "label": "Last ride",
                "value": (
                    f"{context['last_ride_load'].title()} load · "
                    f"{context['last_ride_points']} points"
                ),
            },
            {
                "label": "Last 7 days",
                "value": (
                    f"{context['recent_7d_load']} load · "
                    f"{context['recent_7d_rides']} ride"
                    f"{'s' if context['recent_7d_rides'] != 1 else ''}"
                ),
            },
            {
                "label": "Load trend",
                "value": context["load_trend"].title(),
            },
        ],
    }


def _recovery_plan(ftp: int, context: dict[str, Any]) -> dict[str, Any]:
    target_low, target_high = _watts(ftp, 50, 65)
    meaningful_days = context["days_since_last_meaningful_ride"]
    if (
        context["last_ride_load"] == "low"
        and meaningful_days is not None
        and meaningful_days <= 1
    ):
        meaningful_load = context["last_meaningful_ride_load"]
        decision = (
            f"A {meaningful_load}-load ride is still only {meaningful_days} day"
            f"{'s' if meaningful_days != 1 else ''} old. The easy spin since then "
            "does not reset the recovery clock, so another interval day would stack "
            "intensity too soon."
        )
        reason = (
            f"Your latest ride was an easy {_last_ride_summary(context)}, but a "
            f"{meaningful_load}-load ride is still only {meaningful_days} day"
            f"{'s' if meaningful_days != 1 else ''} old. <strong>The easy spin does "
            "not erase the recovery need from that earlier work.</strong> Keep the "
            "next session genuinely easy so the following quality day is productive."
        )
    else:
        decision = (
            f"The last ride was {context['last_ride_load']} load and still recent, so "
            "recovery creates more adaptation than another interval day."
        )
        reason = (
            f"Your last ride was {_last_ride_summary(context)}, only "
            f"{context['days_since_last_ride']} day"
            f"{'s' if context['days_since_last_ride'] != 1 else ''} ago. "
            "<strong>Absorb that work before adding more intensity.</strong> Keep "
            "this ride genuinely easy; it should leave the legs better than it found them."
        )
    return {
        "workout_name": "Post-Ride Recovery Spin",
        "reasoning": reason,
        "focus": "Recovery and adaptation",
        "duration_minutes": 45,
        "target_power": {"low": target_low, "high": target_high},
        "hr_zone": "Zone 1–2",
        "suggested_sets": [
            {
                "name": "Easy Start",
                "duration_minutes": 10,
                "power_pct_ftp": [40, 55],
                "description": (
                    f"Easy spin at {_watts(ftp, 40, 55)[0]}–"
                    f"{_watts(ftp, 40, 55)[1]}W; light pressure on the pedals."
                ),
            },
            {
                "name": "Recovery Endurance",
                "duration_minutes": 25,
                "power_pct_ftp": [50, 65],
                "description": (
                    f"Smooth {_watts(ftp, 50, 65)[0]}–"
                    f"{_watts(ftp, 50, 65)[1]}W. No surges and no chasing speed."
                ),
            },
            {
                "name": "Cooldown",
                "duration_minutes": 10,
                "power_pct_ftp": [40, 50],
                "description": "Back the pressure off and finish fresher than you started.",
            },
        ],
        "weekly_focus": (
            "This week: absorb the recent ride first, then add one quality "
            "climbing session after at least 48 easy hours."
        ),
        "plan_basis": _basis(context, decision),
    }


def _endurance_plan(ftp: int, context: dict[str, Any]) -> dict[str, Any]:
    target_low, target_high = _watts(ftp, 56, 70)
    if context["days_since_last_ride"] >= 7:
        decision = (
            "A full week or more off the bike calls for aerobic re-entry before "
            "threshold or VO2 work."
        )
        reason = (
            f"It has been {context['days_since_last_ride']} days since your last ride. "
            "<strong>Re-establish frequency and aerobic rhythm before adding a hard "
            "session.</strong> This is steady base work, not a fitness test."
        )
        workout_name = "Aerobic Return Ride"
    else:
        decision = (
            "Recent load is already concentrated, so an endurance day keeps the "
            "week productive without stacking intensity."
        )
        reason = (
            f"The last seven days contain {context['recent_7d_load']} load points "
            f"across {context['recent_7d_rides']} rides. <strong>Add aerobic volume "
            "without stacking another hard day.</strong> Keep the power controlled "
            "from start to finish."
        )
        workout_name = "Aerobic Consolidation Ride"

    return {
        "workout_name": workout_name,
        "reasoning": reason,
        "focus": "Aerobic base",
        "duration_minutes": 60,
        "target_power": {"low": target_low, "high": target_high},
        "hr_zone": "Zone 2",
        "suggested_sets": [
            {
                "name": "Warmup",
                "duration_minutes": 10,
                "power_pct_ftp": [45, 60],
                "description": "Build gradually from an easy spin into endurance power.",
            },
            {
                "name": "Steady Endurance",
                "duration_minutes": 40,
                "power_pct_ftp": [56, 70],
                "description": (
                    f"Hold {_watts(ftp, 56, 70)[0]}–"
                    f"{_watts(ftp, 56, 70)[1]}W with no threshold surges."
                ),
            },
            {
                "name": "Cooldown",
                "duration_minutes": 10,
                "power_pct_ftp": [40, 50],
                "description": "Easy spin and let heart rate settle.",
            },
        ],
        "weekly_focus": (
            "This week: rebuild consistent Zone 2 volume, then earn the next "
            "quality session with fresh legs."
        ),
        "plan_basis": _basis(context, decision),
    }


def _quality_plan(ftp: int, context: dict[str, Any]) -> dict[str, Any]:
    target_low, target_high = _watts(ftp, 88, 92)
    days = context["days_since_last_ride"]
    decision = (
        f"{days} recovery days after a {context['last_ride_load']}-load ride support "
        "one controlled sweet-spot session, not a maximal threshold day."
    )
    return {
        "workout_name": "Sustained Climbing Power",
        "reasoning": (
            f"You have had {days} recovery days since a "
            f"{context['last_ride_load']}-load ride ({_last_ride_summary(context)}). "
            "<strong>That supports one controlled sweet-spot session without turning "
            "the week into a threshold test.</strong> Two 20-minute blocks build the "
            "sustained power and fatigue resistance your current goals require."
        ),
        "focus": "Sustained climbing power",
        "duration_minutes": 70,
        "target_power": {"low": target_low, "high": target_high},
        "hr_zone": "Zone 3–4",
        "suggested_sets": [
            {
                "name": "Warmup",
                "duration_minutes": 15,
                "power_pct_ftp": [50, 75],
                "description": (
                    "Easy spin into tempo, then add three 20-second high-cadence "
                    "openers with full easy recovery."
                ),
            },
            {
                "name": "Main Set",
                "duration_minutes": 45,
                "power_pct_ftp": [88, 92],
                "description": (
                    f"2 × 20 min at {target_low}–{target_high}W with 5 min easy at "
                    f"{_watts(ftp, 56, 65)[0]}–{_watts(ftp, 56, 65)[1]}W between."
                ),
            },
            {
                "name": "Cooldown",
                "duration_minutes": 10,
                "power_pct_ftp": [40, 55],
                "description": "Easy spin until breathing and heart rate settle.",
            },
        ],
        "weekly_focus": (
            "This week: one controlled sweet-spot session plus easy Zone 2 volume; "
            "do not stack hard days."
        ),
        "plan_basis": _basis(context, decision),
    }


def validate_plan(plan: dict[str, Any]) -> None:
    sets = plan.get("suggested_sets")
    if not isinstance(sets, list) or not sets:
        raise ValueError("Training plan must contain suggested sets")

    set_minutes = sum(int(item["duration_minutes"]) for item in sets)
    if set_minutes != plan.get("duration_minutes"):
        raise ValueError(
            f"Set durations total {set_minutes} minutes, expected "
            f"{plan.get('duration_minutes')}"
        )

    target = plan.get("target_power") or {}
    if target.get("low", 0) <= 0 or target.get("high", 0) < target.get("low", 0):
        raise ValueError("Training plan has an invalid target power range")

    for item in sets:
        low, high = item["power_pct_ftp"]
        if not (0 <= low <= high <= 130):
            raise ValueError(f"Invalid FTP range for {item.get('name')}")


def build_training_plan(
    rides: list[Ride],
    athlete: Athlete,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    context = compute_training_context(rides, athlete, now=now)
    ftp = int(athlete.get("ftp") or 237)
    days = context["days_since_last_ride"]
    meaningful_days = context["days_since_last_meaningful_ride"]

    if (
        days <= 1
        and (
            context["last_ride_load"] in {"moderate", "high"}
            or (meaningful_days is not None and meaningful_days <= 1)
        )
    ) or (
        days <= 2
        and (
            context["last_ride_load"] == "high"
            or context["recent_7d_load"] >= 300
        )
    ):
        plan = _recovery_plan(ftp, context)
    elif days >= 7 or (
        days <= 3
        and (
            context["recent_7d_load"] >= 400
            or context["high_load_rides_7d"] >= 2
        )
    ):
        plan = _endurance_plan(ftp, context)
    else:
        plan = _quality_plan(ftp, context)

    validate_plan(plan)
    return plan
