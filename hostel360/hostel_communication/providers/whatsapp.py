"""WhatsApp Business messages through the Meta Cloud API.

Business-initiated messages must use templates approved in WhatsApp Manager, so every
send is a template message whose body parameters come from the Message Template's
``template_parameters``. Sign-in codes use an authentication template with a copy-code
button; Meta requires the code both as the body parameter and as the button parameter.
"""

import requests
from frappe.utils import cint, cstr

from hostel360.hostel_communication.providers.base import (
	DeliveryResult,
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
from hostel360.hostel_communication.rendering import plain_text

PROVIDER = "WhatsApp Cloud API"
GRAPH_URL = "https://graph.facebook.com/{version}/{phone_number_id}/messages"
DEFAULT_API_VERSION = "v21.0"
PARAMETER_LIMIT = 1000

# Graph API error codes that are throttling or transient platform trouble.
TRANSIENT_ERROR_CODES = frozenset({1, 2, 4, 17, 341, 80007, 130429, 131000, 131016, 131048, 131056})


def messages_url(settings) -> str:
	return GRAPH_URL.format(
		version=(settings.get("whatsapp_api_version") or DEFAULT_API_VERSION).strip(),
		phone_number_id=cstr(settings.get("whatsapp_phone_number_id")).strip(),
	)


def clean_parameter(value) -> str:
	"""WhatsApp rejects empty parameters and parameters with new lines or tabs."""
	text = plain_text(value)
	return (text[: PARAMETER_LIMIT - 3] + "...") if len(text) > PARAMETER_LIMIT else (text or "-")


def build_template_payload(
	mobile: str, template_name: str, language: str | None, parameters: list[str]
) -> dict:
	template = {"name": template_name.strip(), "language": {"code": (language or "en").strip()}}
	if parameters:
		template["components"] = [
			{
				"type": "body",
				"parameters": [{"type": "text", "text": clean_parameter(p)} for p in parameters],
			}
		]
	return {
		"messaging_product": "whatsapp",
		"recipient_type": "individual",
		"to": f"91{mobile}",
		"type": "template",
		"template": template,
	}


def build_authentication_payload(mobile: str, template_name: str, language: str | None, code: str) -> dict:
	payload = build_template_payload(mobile, template_name, language, [code])
	payload["template"]["components"].append(
		{
			"type": "button",
			"sub_type": "url",
			"index": "0",
			"parameters": [{"type": "text", "text": code}],
		}
	)
	return payload


def send_template(
	mobile: str,
	template_name: str | None,
	language: str | None = None,
	parameters: list[str] | None = None,
	*,
	settings=None,
) -> DeliveryResult:
	settings = settings or get_settings()
	if not cint(settings.get("whatsapp_enabled")):
		return DeliveryResult.skip(PROVIDER, "WhatsApp is not enabled in Messaging Settings.")
	if not mobile:
		return DeliveryResult.skip(PROVIDER, "No mobile number.")
	if not template_name:
		return DeliveryResult.failed(
			PROVIDER, "The WhatsApp template name is not set on this message template."
		)
	if is_test_mode(settings):
		return test_mode_result(PROVIDER)
	return _post(settings, build_template_payload(mobile, template_name, language, parameters or []))


def send_authentication_code(
	mobile: str,
	template_name: str | None,
	language: str | None,
	code: str,
	*,
	settings=None,
	timeout: tuple[float, float] | None = None,
) -> DeliveryResult:
	settings = settings or get_settings()
	if not cint(settings.get("whatsapp_enabled")):
		return DeliveryResult.skip(PROVIDER, "WhatsApp is not enabled in Messaging Settings.")
	if not mobile:
		return DeliveryResult.skip(PROVIDER, "No mobile number.")
	if not template_name:
		return DeliveryResult.failed(PROVIDER, "The WhatsApp authentication template name is not set.")
	if is_test_mode(settings):
		return test_mode_result(PROVIDER)
	return _post(settings, build_authentication_payload(mobile, template_name, language, code), timeout)


def _post(settings, payload: dict, timeout: tuple[float, float] | None = None) -> DeliveryResult:
	token = get_secret(settings, "whatsapp_access_token")
	if not token or not settings.get("whatsapp_phone_number_id"):
		return DeliveryResult.failed(PROVIDER, "The WhatsApp phone number ID or access token is not set.")

	headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
	try:
		response = requests.post(
			messages_url(settings), json=payload, headers=headers, timeout=timeout or get_timeout(settings)
		)
	except requests.RequestException as e:
		return request_failure(PROVIDER, e)

	data = json_or_empty(response)
	if response.ok:
		messages = data.get("messages") or []
		message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
		return DeliveryResult.sent(PROVIDER, message_id)

	error = data.get("error") if isinstance(data.get("error"), dict) else None
	if not error:
		return http_failure(PROVIDER, response)

	code = cint(error.get("code"))
	details = (
		(error.get("error_data") or {}).get("details") if isinstance(error.get("error_data"), dict) else None
	)
	message = truncate(" ".join(cstr(part) for part in (error.get("message"), details) if part), 300)
	retryable = code in TRANSIENT_ERROR_CODES or response.status_code >= 500 or response.status_code == 429
	return DeliveryResult.failed(PROVIDER, f"WhatsApp error {code}: {message}", retryable=retryable)
