"""Time series stored as Ops Metric Sample rows, and the aggregates reports need.

Samples arrive from the health checks (every five minutes), the host agent (every minute)
and the external uptime probe (``uptime_probe`` = 1 when the portal answered, 0 when it did
not, one sample a minute). Rows are inserted in bulk because a host agent batch can carry
dozens of samples.
"""

from uuid import uuid7

import frappe
from frappe.utils import add_days, flt, get_datetime, getdate, now_datetime

from hostel360.hostel_operations.constants import METRIC_DOCTYPE

MAX_METRIC_NAME = 140
MAX_BATCH = 500

# Each probe sample stands for one minute; health check readiness samples for five.
UPTIME_SOURCES = (("uptime_probe", 1, "External uptime probe"), ("app_ready", 5, "Application readiness checks"))


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


def compute_uptime(start, end) -> dict:
	"""Uptime for [start, end) from probe samples, falling back to readiness checks.

	Returns ``{uptime_percent, downtime_minutes, samples, source}``; ``uptime_percent`` is
	None when there is no data at all.
	"""
	table = frappe.qb.DocType(METRIC_DOCTYPE)
	from frappe.query_builder.functions import Count, Sum
	from pypika.terms import Case

	for metric, minutes_per_sample, label in UPTIME_SOURCES:
		row = (
			frappe.qb.from_(table)
			.select(
				Count("*").as_("samples"),
				Sum(Case().when(table.value < 0.5, 1).else_(0)).as_("down"),
			)
			.where((table.metric == metric) & (table.recorded_at >= start) & (table.recorded_at < end))
		).run(as_dict=True)[0]
		samples = int(row.samples or 0)
		if not samples:
			continue
		down = int(row.down or 0)
		return {
			"uptime_percent": flt((samples - down) * 100.0 / samples, 3),
			"downtime_minutes": down * minutes_per_sample,
			"samples": samples,
			"source": label,
		}
	return {"uptime_percent": None, "downtime_minutes": 0, "samples": 0, "source": "No availability data"}


def daily_uptime(from_date, to_date) -> list[dict]:
	"""One row per day: date, uptime_percent (or None), downtime_minutes, samples, source."""
	rows = []
	day = getdate(from_date)
	last = getdate(to_date)
	while day <= last:
		start = get_datetime(day)
		end = get_datetime(add_days(day, 1))
		rows.append({"date": day, **compute_uptime(start, end)})
		day = add_days(day, 1)
	return rows
