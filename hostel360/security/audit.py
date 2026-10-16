"""Security event log.

One line per event in ``logs/hostel360_security.log`` (and the site's log folder), in
``key=value`` form so fail2ban and the log shipper can parse it. Mobile numbers are
masked and secrets are never written.
"""

import logging

import frappe

from hostel360.utils.validation import mask_mobile

LOGGER_NAME = "hostel360_security"


def security_log(
	event: str, *, mobile: str | None = None, ip: str | None = None, level: str = "info", **fields
) -> None:
	parts = [f"event={event}"]
	if mobile:
		parts.append(f"mobile={mask_mobile(mobile)}")
	if ip:
		parts.append(f"ip={ip}")
	for key, value in fields.items():
		if value is None or value == "":
			continue
		# Keep each value a single token so the line splits cleanly on spaces.
		parts.append(f"{key}={'_'.join(str(value).split())}")
	logger = frappe.logger(LOGGER_NAME, allow_site=True)
	# Frappe loggers default to ERROR outside the dev server; security events are an audit
	# trail (CERT-In asks for 180 days of them), so this one always records INFO.
	if logger.level != logging.INFO:
		logger.setLevel(logging.INFO)
	getattr(logger, level, logger.info)(" ".join(parts))
