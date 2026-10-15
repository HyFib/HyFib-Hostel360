"""Time series stored as Ops Metric Sample rows, and the aggregates reports need.

Samples arrive from the health checks (every five minutes), the host agent (every minute)
and the uptime probe (``uptime_probe`` = 1 when the portal answered, 0 when it did not, one
sample a minute). An off-site monitor may also report ``uptime_external`` samples through
the ingest API. Rows are inserted in bulk because a host agent batch can carry dozens of
samples.
"""

from datetime import timedelta
from uuid import uuid7

import frappe
from frappe.utils import add_days, add_to_date, flt, get_datetime, getdate, now_datetime

from hostel360.hostel_operations.constants import METRIC_DOCTYPE

MAX_METRIC_NAME = 140
MAX_BATCH = 500

# (metric, minutes one sample stands for, label), most trusted first. A minute is judged by
# the first source that has a sample for it; readiness samples (every five minutes, from
# inside the application) only fill minutes the probes did not cover.
UPTIME_SOURCES = (
	("uptime_external", 1, "Off-site uptime monitor"),
	("uptime_probe", 1, "External uptime probe"),
	("app_ready", 5, "Application readiness checks"),
)
NO_DATA_SOURCE = "No availability data"
# Probe samples reach the server in batches of a few minutes; the most recent minutes are
# not judged until they have had time to arrive.
INGEST_LAG_MINUTES = 10


def record_metrics(samples: list[dict], source: str, host: str | None = None) -> int:
	"""Insert samples ``[{metric, value, unit?, recorded_at?, host?}]``. Returns rows written."""
	now = now_datetime()
	user = frappe.session.user if getattr(frappe.local, "session", None) else "Administrator"
	rows = []
	for sample in samples[:MAX_BATCH]:
		metric = str(sample.get("metric") or "").strip()[:MAX_METRIC_NAME]
		if not metric:
			continue
		try:
			value = float(sample.get("value"))
		except (TypeError, ValueError):
			continue
		recorded_at = _clamp_time(sample.get("recorded_at"), now)
		rows.append(
			(
				str(uuid7()),
				metric,
				value,
				str(sample.get("unit") or "")[:40],
				(source or "")[:140],
				str(sample.get("host") or host or "")[:140],
				recorded_at,
				now,
				now,
				user,
				user,
			)
		)
	if rows:
		frappe.db.bulk_insert(
			METRIC_DOCTYPE,
			fields=[
				"name",
				"metric",
				"value",
				"unit",
				"source",
				"host",
				"recorded_at",
				"creation",
				"modified",
				"owner",
				"modified_by",
			],
			values=rows,
		)
	return len(rows)


def record_metric(metric: str, value, unit: str = "", source: str = "Health Check", host: str | None = None) -> None:
	record_metrics([{"metric": metric, "value": value, "unit": unit}], source=source, host=host)


def _clamp_time(value, now):
	"""Agents sometimes run with a skewed clock; never accept samples from the future."""
	if not value:
		return now
	try:
		recorded = get_datetime(value)
	except Exception:
		return now
	if recorded.tzinfo is not None:
		from frappe.utils.data import convert_utc_to_system_timezone

		recorded = convert_utc_to_system_timezone(recorded).replace(tzinfo=None)
	return min(recorded, now)


def metric_stats(metric: str, start, end) -> dict:
	"""Average, peak, latest value and sample count for a metric in [start, end)."""
	table = frappe.qb.DocType(METRIC_DOCTYPE)
	from frappe.query_builder.functions import Avg, Count, Max

	row = (
		frappe.qb.from_(table)
		.select(Avg(table.value).as_("avg"), Max(table.value).as_("peak"), Count("*").as_("samples"))
		.where((table.metric == metric) & (table.recorded_at >= start) & (table.recorded_at < end))
	).run(as_dict=True)[0]
	latest = (
		frappe.qb.from_(table)
		.select(table.value)
		.where((table.metric == metric) & (table.recorded_at >= start) & (table.recorded_at < end))
		.orderby(table.recorded_at, order=frappe.qb.desc)
		.limit(1)
	).run()
	return {
		"avg": flt(row.avg, 2) if row.samples else None,
		"peak": flt(row.peak, 2) if row.samples else None,
		"latest": flt(latest[0][0], 2) if latest else None,
		"samples": int(row.samples or 0),
	}


def metric_sum(metric: str, start, end) -> float:
	table = frappe.qb.DocType(METRIC_DOCTYPE)
	from frappe.query_builder.functions import Sum

	value = (
		frappe.qb.from_(table)
		.select(Sum(table.value))
		.where((table.metric == metric) & (table.recorded_at >= start) & (table.recorded_at < end))
	).run()[0][0]
	return flt(value)


def monitoring_started_at():
	"""When availability was first measured on this installation (None: never)."""
	table = frappe.qb.DocType(METRIC_DOCTYPE)
	from frappe.query_builder.functions import Min

	value = (
		frappe.qb.from_(table)
		.select(Min(table.recorded_at))
		.where(table.metric.isin([metric for metric, _minutes, _label in UPTIME_SOURCES]))
	).run()[0][0]
	return get_datetime(value) if value else None


def compute_uptime(start, end, now=None, monitoring_start=None) -> dict:
	"""Availability for [start, end) measured against the wall clock.

	Every minute of the period counts. A minute is up or down according to the best source
	that measured it; a minute nobody measured (the server itself was down, the probe was
	stopped, its messages were lost) is *unmonitored* and is counted as unavailable, because
	the report must never claim availability nobody observed. Minutes before monitoring first
	started on this installation, and the last few minutes whose samples may still be on the
	way, are left out of the period.

	Returns ``{uptime_percent, downtime_minutes, measured_downtime_minutes,
	unmonitored_minutes, coverage_percent, expected_minutes, samples, source}``;
	``uptime_percent`` is None when the period is entirely before monitoring began.
	"""
	start = get_datetime(start)
	end = get_datetime(end)
	now = get_datetime(now) if now else now_datetime()
	monitoring_start = monitoring_start or monitoring_started_at()
	if not monitoring_start:
		return _no_uptime_data()

	period_start = _floor_minute(max(start, get_datetime(monitoring_start)))
	period_end = _floor_minute(min(end, add_to_date(now, minutes=-INGEST_LAG_MINUTES)))
	expected = int((period_end - period_start).total_seconds() // 60)
	if expected <= 0:
		return _no_uptime_data()

	table = frappe.qb.DocType(METRIC_DOCTYPE)
	minutes: dict[int, tuple[bool, str]] = {}
	used = []
	samples = 0
	for metric, span, label in UPTIME_SOURCES:
		rows = (
			frappe.qb.from_(table)
			.select(table.recorded_at, table.value)
			.where(
				(table.metric == metric)
				& (table.recorded_at >= add_to_date(period_start, minutes=-(span - 1)))
				& (table.recorded_at < period_end)
			)
		).run()
		covered_here = False
		for recorded_at, value in rows:
			samples += 1
			first = int((get_datetime(recorded_at) - period_start).total_seconds() // 60)
			up = flt(value) >= 0.5
			for minute in range(first, first + span):
				if minute < 0 or minute >= expected:
					continue
				seen = minutes.get(minute)
				if seen is None:
					minutes[minute] = (up, metric)
					covered_here = True
				elif seen[1] == metric and not up:
					# Two samples from one source in the same minute: any failure counts.
					minutes[minute] = (False, metric)
		if covered_here:
			used.append(label)

	covered = len(minutes)
	down = sum(1 for up, _metric in minutes.values() if not up)
	unmonitored = expected - covered
	downtime = down + unmonitored
	return {
		"uptime_percent": flt((expected - downtime) * 100.0 / expected, 3),
		"downtime_minutes": downtime,
		"measured_downtime_minutes": down,
		"unmonitored_minutes": unmonitored,
		"coverage_percent": flt(covered * 100.0 / expected, 2),
		"expected_minutes": expected,
		"samples": samples,
		"source": " + ".join(used) if used else NO_DATA_SOURCE,
	}


def _no_uptime_data() -> dict:
	return {
		"uptime_percent": None,
		"downtime_minutes": 0,
		"measured_downtime_minutes": 0,
		"unmonitored_minutes": 0,
		"coverage_percent": 0.0,
		"expected_minutes": 0,
		"samples": 0,
		"source": NO_DATA_SOURCE,
	}


def _floor_minute(value):
	value = get_datetime(value)
	return value - timedelta(seconds=value.second, microseconds=value.microsecond)


def daily_uptime(from_date, to_date, now=None) -> list[dict]:
	"""One row per day: date plus the ``compute_uptime`` figures for that day."""
	rows = []
	day = getdate(from_date)
	last = getdate(to_date)
	monitoring_start = monitoring_started_at()
	while day <= last:
		start = get_datetime(day)
		end = get_datetime(add_days(day, 1))
		rows.append({"date": day, **compute_uptime(start, end, now=now, monitoring_start=monitoring_start)})
		day = add_days(day, 1)
	return rows
