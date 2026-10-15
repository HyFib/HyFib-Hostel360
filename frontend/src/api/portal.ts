/**
 * Student and parent portal endpoints (hostel360.api.portal).
 *
 * For parents, `student` picks the ward being viewed; students may omit it (the server
 * always uses the signed-in student). Every function maps one endpoint of the design §6.
 */
import { call, callFile, upload, type DownloadedFile } from "./client";
import type {
	Certificate,
	CreatePaymentResult,
	Dashboard,
	FeedbackInput,
	FeesSummary,
	Grievance,
	GrievanceInput,
	GrievanceSummary,
	Leave,
	LeaveInput,
	MessBill,
	MessReduction,
	MessReductionInput,
	Named,
	Ok,
	PortalNotification,
	PortalSession,
	Profile,
	ProfileUpdates,
	UploadResult,
	VerifyPaymentResult,
	WeekMenu,
} from "./types";

const PORTAL = "hostel360.api.portal";
const m = (name: string) => `${PORTAL}.${name}`;

type StudentArg = string | null | undefined;
const forStudent = (student: StudentArg) => (student ? { student } : {});

// Session and consent

export const getSession = () => call<PortalSession>(m("get_session"));

export const acceptPrivacyNotice = (version: string) => call<Ok>(m("accept_privacy_notice"), { version });

export const getDashboard = (student?: StudentArg) => call<Dashboard>(m("get_dashboard"), forStudent(student));

export const getProfile = (student?: StudentArg) => call<Profile>(m("get_profile"), forStudent(student));

export const confirmProfile = (updates: ProfileUpdates) => call<Ok>(m("confirm_profile"), { updates });

// Fees and payments

export const getFees = (student?: StudentArg) => call<FeesSummary>(m("get_fees"), forStudent(student));

export const createPayment = (invoices: string[], student?: StudentArg) =>
	call<CreatePaymentResult>(m("create_payment"), { invoices, ...forStudent(student) });

export interface CheckoutResponse {
	razorpay_payment_id: string;
	razorpay_order_id: string;
	razorpay_signature: string;
}

export const verifyPayment = (transaction: string, response: CheckoutResponse) =>
	call<VerifyPaymentResult>(m("verify_payment"), {
		transaction,
		razorpay_payment_id: response.razorpay_payment_id,
		razorpay_order_id: response.razorpay_order_id,
		razorpay_signature: response.razorpay_signature,
	});

export const downloadReceipt = (payment: string): Promise<DownloadedFile> =>
	callFile(m("download_receipt"), { payment }, `${payment}.pdf`);

export const downloadFeeStructure = (student?: StudentArg): Promise<DownloadedFile> =>
	callFile(m("download_fee_structure"), forStudent(student), "fee-structure.pdf");

// Mess

export const getMenu = (weekStart?: string | null, student?: StudentArg) =>
	call<WeekMenu>(m("get_menu"), { ...(weekStart ? { week_start: weekStart } : {}), ...forStudent(student) });

export const getMessBills = (student?: StudentArg) => call<MessBill[]>(m("get_mess_bills"), forStudent(student));

export const listMessReductions = (student?: StudentArg) =>
	call<MessReduction[]>(m("list_mess_reductions"), forStudent(student));

export const applyMessReduction = (input: MessReductionInput, student?: StudentArg) =>
	call<Named>(m("apply_mess_reduction"), { ...input, ...forStudent(student) });

export const cancelMessReduction = (name: string) => call<Ok>(m("cancel_mess_reduction"), { name });

// Leave

export const listLeaves = (student?: StudentArg) => call<Leave[]>(m("list_leaves"), forStudent(student));

export const applyLeave = (input: LeaveInput, student?: StudentArg) =>
	call<Named>(m("apply_leave"), { ...input, ...forStudent(student) });

export const cancelLeave = (name: string) => call<Ok>(m("cancel_leave"), { name });

export const pendingLeaveApprovals = () => call<Leave[]>(m("pending_leave_approvals"));

export const decideLeave = (name: string, action: "approve" | "reject", remarks?: string) =>
	call<Ok>(m("decide_leave"), { name, action, remarks: remarks || undefined });

// Grievances

export const listGrievances = (student?: StudentArg) =>
	call<GrievanceSummary[]>(m("list_grievances"), forStudent(student));

export const getGrievance = (name: string) => call<Grievance>(m("get_grievance"), { name });

export const raiseGrievance = (input: GrievanceInput, student?: StudentArg) =>
	call<Named>(m("raise_grievance"), { ...input, ...forStudent(student) });

export const commentGrievance = (name: string, comment: string) => call<Ok>(m("comment_grievance"), { name, comment });

export const reopenGrievance = (name: string, reason: string) => call<Ok>(m("reopen_grievance"), { name, reason });

export const rateGrievance = (name: string, rating: number, feedback?: string) =>
	call<Ok>(m("rate_grievance"), { name, rating, feedback: feedback || undefined });

export function uploadAttachment(file: File, purpose: "grievance" = "grievance"): Promise<UploadResult> {
	const form = new FormData();
	form.append("file", file, file.name);
	form.append("purpose", purpose);
	return upload<UploadResult>(m("upload_attachment"), form);
}

// Feedback and certificates

export const submitFeedback = (input: FeedbackInput, student?: StudentArg) =>
	call<Named>(m("submit_feedback"), { ...input, ...forStudent(student) });

export const listCertificates = (student?: StudentArg) =>
	call<Certificate[]>(m("list_certificates"), forStudent(student));

export const requestBonafide = (purpose: string, purposeDetails: string, student?: StudentArg) =>
	call<Named>(m("request_bonafide"), { purpose, purpose_details: purposeDetails, ...forStudent(student) });

export const downloadBonafide = (name: string): Promise<DownloadedFile> =>
	callFile(m("download_bonafide"), { name }, `${name}.pdf`);

// Notifications

export const listNotifications = (limit = 50) => call<PortalNotification[]>(m("list_notifications"), { limit });

export const markNotificationsRead = (names?: string[]) =>
	call<Ok>(m("mark_notifications_read"), names && names.length ? { names } : {});
