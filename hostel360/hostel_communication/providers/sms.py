"""Transactional SMS through DLT-compliant gateways.

MSG91 is used in one of two ways, per message template:

* with a MSG91 Flow template ID, through the Flow API: the text lives in the MSG91
  template (linked there to the DLT template) and only the variables are sent. Every
  scalar context variable goes under its own name and the template parameters as VAR1,
  VAR2, ..., so a flow may use ``##student_name##`` or ``##VAR1##`` placeholders;
* with only a DLT template ID, through MSG91's SendHTTP API: the rendered text is sent
  with the DLT template ID and the sender ID, and the operator's DLT scrubbing matches it
  against the registered template.

The generic HTTP provider sends the fully rendered text with the DLT template ID, entity
ID and sender ID under configurable parameter names, which covers most other Indian
DLT vendors.

Tamil text is sent as Unicode: MSG91's SendHTTP API is told so with ``unicode=1``, and the
generic provider adds its configured Unicode parameter, only for messages that need it.
"""

import json

import requests
from frappe.utils import cint, cstr

from hostel360.hostel_communication.providers.base import (
	DeliveryResult,
	demo_mode_result,
	get_secret,
	get_settings,
	get_timeout,
	http_failure,
	is_test_mode,
	json_or_empty,
	request_failure,
	test_mode_result,
	truncate,
)
from hostel360.hostel_communication.rendering import is_unicode, short

MSG91 = "MSG91"
GENERIC_HTTP = "Generic HTTP"
MSG91_FLOW_URL = "https://control.msg91.com/api/v5/flow"
MSG91_SENDHTTP_URL = "https://api.msg91.com/api/sendhttp.php"
MSG91_TRANSACTIONAL_ROUTE = "4"

# DLT allows at most 30 characters for each {#var#}.
DLT_VARIABLE_LIMIT = 30


def send_sms(
	mobile: str,
	body: str,
	*,
	dlt_template_id: str | None = None,
	msg91_template_id: str | None = None,
	variables: dict | None = None,
	parameters: list[str] | None = None,
	settings=None,
	timeout: tuple[float, float] | None = None,
) -> DeliveryResult:
	"""Send one SMS. ``timeout`` (connect, read) overrides Messaging Settings for callers
	that must answer quickly, such as sign-in codes."""
	settings = settings or get_settings()
	provider = settings.get("sms_provider") or MSG91
	timeout = timeout or get_timeout(settings)

	if muted := demo_mode_result(provider):
		return muted
	if not cint(settings.get("sms_enabled")):
		return DeliveryResult.skip(provider, "SMS is not enabled in Messaging Settings.")
	if not mobile:
		return DeliveryResult.skip(provider, "No mobile number.")
	if not (body or msg91_template_id):
		return DeliveryResult.failed(provider, "The message is empty.")
	if is_test_mode(settings):
		return test_mode_result(provider)

	if provider == GENERIC_HTTP:
		return _send_generic(settings, mobile, body, dlt_template_id, timeout)
	if msg91_template_id:
		return _send_msg91_flow(
			settings, mobile, msg91_template_id, variables or {}, parameters or [], timeout
		)
	if dlt_template_id:
		return _send_msg91_text(settings, mobile, body, dlt_template_id, timeout)
	return DeliveryResult.failed(
		MSG91, "Set the MSG91 Flow template ID or the DLT template ID on this message template."
	)


def _msg91_variables(variables: dict, parameters: list[str]) -> dict:
	values = {}
	for key, value in variables.items():
		if isinstance(value, str | int | float) and not isinstance(value, bool):
			values[cstr(key)] = short(value, DLT_VARIABLE_LIMIT)
	for index, value in enumerate(parameters, start=1):
		values[f"VAR{index}"] = short(value, DLT_VARIABLE_LIMIT)
	return values


def build_msg91_payload(
	settings, mobile: str, template_id: str, variables: dict, parameters: list[str]
) -> dict:
	recipient = {**_msg91_variables(variables, parameters), "mobiles": f"91{mobile}"}
	payload = {"template_id": template_id, "short_url": "0", "recipients": [recipient]}
	if settings.get("sms_sender_id"):
		payload["sender"] = settings.sms_sender_id.strip()
	return payload


def _send_msg91_flow(settings, mobile, template_id, variables, parameters, timeout=None) -> DeliveryResult:
	auth_key = get_secret(settings, "sms_auth_key")
	if not auth_key:
		return DeliveryResult.failed(MSG91, "The MSG91 auth key is not set in Messaging Settings.")

	url = (settings.get("sms_api_url") or "").strip() or MSG91_FLOW_URL
	payload = build_msg91_payload(settings, mobile, template_id, variables, parameters)
	headers = {"authkey": auth_key, "accept": "application/json", "content-type": "application/json"}
	try:
		response = requests.post(url, json=payload, headers=headers, timeout=timeout or get_timeout(settings))
	except requests.RequestException as e:
		return request_failure(MSG91, e)

	return _msg91_result(response)


def build_msg91_text_request(settings, mobile: str, body: str, dlt_template_id: str, auth_key: str) -> dict:
	data = {
		"authkey": auth_key,
		"mobiles": f"91{mobile}",
		"message": body,
		"route": MSG91_TRANSACTIONAL_ROUTE,
		"country": "91",
		"DLT_TE_ID": dlt_template_id.strip(),
		"response": "json",
	}
	if settings.get("sms_sender_id"):
		data["sender"] = settings.sms_sender_id.strip()
	if is_unicode(body):
		data["unicode"] = "1"
	return data


def _send_msg91_text(settings, mobile, body, dlt_template_id, timeout=None) -> DeliveryResult:
	if not body:
		return DeliveryResult.failed(MSG91, "The message is empty.")
	auth_key = get_secret(settings, "sms_auth_key")
	if not auth_key:
		return DeliveryResult.failed(MSG91, "The MSG91 auth key is not set in Messaging Settings.")
	data = build_msg91_text_request(settings, mobile, body, dlt_template_id, auth_key)
	try:
		response = requests.post(
			MSG91_SENDHTTP_URL,
			data=data,
			headers={"accept": "application/json"},
			timeout=timeout or get_timeout(settings),
		)
	except requests.RequestException as e:
		return request_failure(MSG91, e)
	return _msg91_result(response)


def _msg91_result(response) -> DeliveryResult:
	if not response.ok:
		return http_failure(MSG91, response)
	data = json_or_empty(response)
	if cstr(data.get("type")).lower() == "success":
		return DeliveryResult.sent(MSG91, cstr(data.get("message") or data.get("request_id")) or None)
	# MSG91 reports problems such as low balance or an unapproved template with HTTP 200.
	return DeliveryResult.failed(
		MSG91, f"MSG91: {truncate(data.get('message') or response.text, 300)}", retryable=True
	)


def format_mobile(mobile: str, style: str | None) -> str:
	if style == "+91XXXXXXXXXX":
		return f"+91{mobile}"
	if style == "XXXXXXXXXX":
		return mobile
	return f"91{mobile}"


def build_generic_request(
	settings, mobile: str, body: str, dlt_template_id: str | None, auth_key: str | None
) -> dict:
	"""Method, URL, headers and parameters for the generic HTTP provider."""
	try:
		params = json.loads(settings.get("sms_extra_params") or "{}")
	except ValueError:
		params = {}
	if not isinstance(params, dict):
		params = {}

	def put(param_field: str, value):
		name = (settings.get(param_field) or "").strip()
		if name and value not in (None, ""):
			params[name] = value

	put("sms_param_mobile", format_mobile(mobile, settings.get("sms_mobile_format")))
	put("sms_param_message", body)
	put("sms_param_sender", (settings.get("sms_sender_id") or "").strip())
	put("sms_param_template_id", (dlt_template_id or "").strip())
	put("sms_param_entity_id", (settings.get("dlt_entity_id") or "").strip())
	if is_unicode(body):
		put("sms_param_unicode", (settings.get("sms_unicode_value") or "1").strip())

	headers = {"accept": "application/json"}
	header_name = (settings.get("sms_auth_header") or "").strip()
	if auth_key:
		if header_name:
			headers[header_name] = auth_key
		else:
			params[(settings.get("sms_auth_param") or "apikey").strip()] = auth_key

	method = (settings.get("sms_http_method") or "POST").upper()
	request = {"method": method, "url": (settings.get("sms_api_url") or "").strip(), "headers": headers}
	if method == "GET":
		request["params"] = params
	elif (settings.get("sms_payload_format") or "JSON") == "Form":
		request["data"] = params
	else:
		request["json"] = params
	return request


def _send_generic(settings, mobile, body, dlt_template_id, timeout=None) -> DeliveryResult:
	if not settings.get("sms_api_url"):
		return DeliveryResult.failed(GENERIC_HTTP, "The SMS API URL is not set in Messaging Settings.")
	if not body:
		return DeliveryResult.failed(GENERIC_HTTP, "The message is empty.")

	request = build_generic_request(
		settings, mobile, body, dlt_template_id, get_secret(settings, "sms_auth_key")
	)
	try:
		response = requests.request(timeout=timeout or get_timeout(settings), **request)
	except requests.RequestException as e:
		return request_failure(GENERIC_HTTP, e)

	if not response.ok:
		return http_failure(GENERIC_HTTP, response)

	keyword = (settings.get("sms_success_keyword") or "").strip()
	if keyword and keyword.lower() not in response.text.lower():
		return DeliveryResult.failed(
			GENERIC_HTTP, f"Unexpected response: {truncate(response.text, 300)}", retryable=True
		)

	data = json_or_empty(response)
	message_id = None
	for key in ("message_id", "messageId", "msgid", "request_id", "requestId", "id"):
		if data.get(key):
			message_id = cstr(data[key])
			break
	if not message_id and isinstance(data.get("data"), dict):
		message_id = cstr(data["data"].get("id") or data["data"].get("message_id") or "") or None
	return DeliveryResult.sent(GENERIC_HTTP, message_id)


def send_alert_sms(mobile: str, subject: str | None, body: str, settings=None) -> DeliveryResult:
	"""Operations alert SMS: a registered alert template with the text split into variables."""
	settings = settings or get_settings()
	text = " ".join(part for part in (subject, body) if part)
	chunks = [
		text[i : i + DLT_VARIABLE_LIMIT]
		for i in range(0, min(len(text), DLT_VARIABLE_LIMIT * 4), DLT_VARIABLE_LIMIT)
	]
	return send_sms(
		mobile,
		text[:300],
		dlt_template_id=settings.get("alert_dlt_template_id"),
		msg91_template_id=settings.get("alert_sms_template_id"),
		variables={"subject": subject or "", "message": body or ""},
		parameters=chunks,
		settings=settings,
	)
