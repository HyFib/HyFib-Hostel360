# Copyright (c) 2026, HyFib Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

# CERT-In directions (28 April 2022) require at least 180 days of logs.
MINIMUM_RETENTION_DAYS = 180


class OpsMetricSample(Document):
	def validate(self):
		self.metric = (self.metric or "").strip()
		self.recorded_at = self.recorded_at or now_datetime()

	@staticmethod
	def clear_old_logs(days: int = 400) -> None:
		"""Called by Log Settings; never keeps less than the statutory minimum."""
		from hostel360.hostel_operations.maintenance import purge_old_metric_samples

		purge_old_metric_samples(retention_days=max(int(days or 0), MINIMUM_RETENTION_DAYS))


def on_doctype_update():
	# Every report and check reads one metric over a time range.
	frappe.db.add_index("Ops Metric Sample", ["metric", "recorded_at"])
