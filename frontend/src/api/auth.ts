/** Sign-in endpoints (hostel360.api.auth). */
import { call, refreshCsrfToken } from "./client";
import type { OtpChannel, PortalKind, SendOtpResult, VerifyOtpResult } from "./types";

const AUTH = "hostel360.api.auth";

export function sendOtp(mobile: string, channel: OtpChannel): Promise<SendOtpResult> {
	return call<SendOtpResult>(`${AUTH}.send_otp`, { mobile, channel });
}

export function verifyOtp(mobile: string, otp: string, kind?: PortalKind): Promise<VerifyOtpResult> {
	return call<VerifyOtpResult>(`${AUTH}.verify_otp`, { mobile, otp, kind });
}

export function getCsrfToken(): Promise<string> {
	return refreshCsrfToken();
}

/** Frappe's own logout endpoint clears the session cookie. */
export function logout(): Promise<unknown> {
	return call("logout", undefined, { method: "POST" });
}
