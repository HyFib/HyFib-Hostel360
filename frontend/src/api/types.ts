/** Shapes of the portal API (docs/SOLUTION-DESIGN.md §6). Optional fields may be absent. */

export type PortalKind = "student" | "parent";
/** "email" only while Messaging Settings allows e-mail codes (boot `otp_email`). */
export type OtpChannel = "sms" | "whatsapp" | "email";

export interface SendOtpResult {
	sent: boolean;
	masked: string;
	resend_after: number;
	expires_in: number;
}

export interface VerifyOtpResult {
	ok: boolean;
	kind?: PortalKind;
	redirect?: string;
	choose?: PortalKind[];
}

export interface Ward {
	student: string;
	full_name: string;
	register_number?: string;
	photo?: string | null;
	hostel?: string | null;
	hostel_name?: string | null;
	room?: string | null;
	room_number?: string | null;
	bed?: string | null;
	bed_label?: string | null;
	/** Hostel Student status: Active, Applied, Suspended, Vacated, Alumni */
	status?: string | null;
	/** Validation of this ward by the hostel office (a parent's own status says nothing about each ward). */
	validation_status?: string | null;
	/** This ward's dues may be paid online now (in test mode only designated test payers may pay). */
	payment_enabled?: boolean;
}

export interface UploadRules {
	max_bytes: number;
	extensions: string[];
}

export interface PortalSettings {
	institution_name?: string;
	institution_short_name?: string;
	logo?: string | null;
	support_phone?: string;
	support_email?: string;
	office_phone?: string | null;
	office_email?: string | null;
	payment_enabled?: boolean | number;
	mess_reduction_min_days?: number;
	/** A departure at or after this hour (or a return before it) does not count that mess day. */
	mess_day_cutoff_hour?: number;
	leave_requires_parent_approval?: boolean | number;
	/** The site's time zone; leave and mess dates are wall-clock times in it. */
	time_zone?: string;
	upload?: UploadRules;
	/** A demonstration site: every record is fictitious and nothing is sent or charged. */
	demo?: boolean;
}

export interface PortalSession {
	user: string;
	kind: PortalKind;
	display_name: string;
	mobile_masked?: string;
	wards: Ward[];
	validation_status?: "Pending" | "Validated" | "Rejected" | string;
	profile_confirmed?: boolean | number;
	consent_required?: boolean | number;
	privacy_notice_version?: string;
	/** Language the server writes this user's messages in (User.language). */
	language?: string | null;
	settings: PortalSettings;
}

export interface PrivacyNotice {
	version: string | null;
	notice: string;
	accepted_version?: string | null;
	accepted_on?: string | null;
	consent_required: boolean | number;
}

export interface RoomInfo {
	hostel?: string | null;
	hostel_name?: string | null;
	block?: string | null;
	block_name?: string | null;
	floor_label?: string | null;
	room?: string | null;
	room_number?: string | null;
	room_type?: string | null;
	bed?: string | null;
	bed_label?: string | null;
	from_date?: string | null;
}

export interface Meal {
	meal: string;
	dishes: string;
	is_special?: boolean | number;
}

export interface TodayMenu {
	mess?: string;
	date?: string;
	day?: string;
	meals: Meal[];
}

export interface PortalNotification {
	name: string;
	subject: string;
	/** The in-app body (plain text), with the details the subject leaves out. */
	message?: string | null;
	created: string;
	read: boolean | number;
	link?: string | null;
	/** Which of a parent's wards this is about, when it is about one. */
	student?: string | null;
}

export type DashboardSection = "profile" | "fees" | "menu" | "pending";

export interface Dashboard {
	student: string;
	student_name?: string | null;
	/** Hostel Student status; anything but Active means only dues and the basics are open. */
	student_status?: string | null;
	restricted?: boolean;
	room: RoomInfo | null;
	/** null when the fee figures could not be loaded (see `unavailable`), never "no dues". */
	fees: { outstanding: number; overdue: number; next_due_date?: string | null; payment_enabled?: boolean } | null;
	today_menu: TodayMenu | null;
	pending: { leaves?: number; reductions?: number; grievances?: number; certificates?: number; approvals?: number };
	notifications: PortalNotification[];
	/** Sections that failed to load on the server. */
	unavailable?: DashboardSection[];
}

export interface GuardianInfo {
	guardian: string;
	guardian_name: string;
	relation?: string;
	mobile_masked?: string;
	is_primary?: boolean | number;
	can_approve_leave?: boolean | number;
}

export const PROFILE_EDITABLE_FIELDS = [
	"email",
	"address_line1",
	"address_line2",
	"city",
	"district",
	"state",
	"pincode",
	"emergency_contact_name",
	"emergency_contact_phone",
	"blood_group",
] as const;
export type EditableField = (typeof PROFILE_EDITABLE_FIELDS)[number];
export type ProfileUpdates = Partial<Record<EditableField, string>>;

export interface Profile extends ProfileUpdates {
	name: string;
	register_number?: string;
	roll_number?: string;
	full_name?: string;
	first_name?: string;
	last_name?: string;
	gender?: string;
	date_of_birth?: string | null;
	photo?: string | null;
	program?: string;
	department?: string;
	year_of_study?: string;
	academic_year?: string;
	mobile_masked?: string;
	status?: string;
	validation_status?: string;
	profile_confirmed?: boolean | number;
	room?: RoomInfo | null;
	guardians?: GuardianInfo[];
	editable_fields?: string[];
	/** Parent profiles */
	guardian_name?: string;
	relation?: string;
	occupation?: string;
	address?: string;
	wards?: Ward[];
}

export interface InvoiceItem {
	fee_head: string;
	description?: string | null;
	amount: number;
}

export interface Invoice {
	name: string;
	invoice_type: string;
	academic_year?: string;
	posting_date: string;
	due_date?: string | null;
	total_amount: number;
	paid_amount: number;
	outstanding_amount: number;
	status: string;
	items: InvoiceItem[];
	payment_in_progress?: boolean;
}

export interface PaymentRecord {
	name: string;
	payment_date: string;
	amount: number;
	mode_of_payment: string;
	reference_no?: string | null;
}

export interface FeesSummary {
	outstanding: number;
	overdue: number;
	overdue_count?: number;
	next_due_date?: string | null;
	invoices: Invoice[];
	payments: PaymentRecord[];
	payment_enabled?: boolean;
	/** Online payment uses the gateway's test credentials: no real money is taken. */
	payment_test_mode?: boolean;
	/** Why online payment is off, when the server says so (a demonstration site). */
	payment_note?: string;
}

export interface CheckoutOptions {
	key: string;
	order_id: string;
	amount: number;
	currency: string;
	name: string;
	description: string;
	image?: string;
	prefill: { name?: string; email?: string; contact?: string };
	notes?: Record<string, string>;
}

/** A gateway's hosted payment page, reached by posting a signed form to it (PayU). */
export interface RedirectCheckout {
	action_url: string;
	method: "POST" | "GET" | string;
	fields: Record<string, string | number>;
}

interface PaymentOrderBase {
	transaction: string;
	amount?: number;
	test_mode?: number;
}

export interface RazorpayPaymentOrder extends PaymentOrderBase {
	gateway: "razorpay";
	checkout: CheckoutOptions;
}

export interface RedirectPaymentOrder extends PaymentOrderBase {
	gateway: "payu";
	checkout: RedirectCheckout;
}

export type CreatePaymentResult = RazorpayPaymentOrder | RedirectPaymentOrder;

export interface VerifyPaymentResult {
	status: "Success" | "Pending" | "Failed" | string;
	payment: string | null;
	receipt_number: string | null;
	transaction?: string;
	message?: string;
	/** Paid with the gateway's test credentials (the receipt is marked as a test receipt). */
	test_mode?: number | boolean;
}

export interface MenuDay {
	date: string;
	day: string;
	is_today?: boolean;
	meals: Meal[];
}

export interface WeekMenu {
	mess: string | null;
	week_start: string;
	week_end: string;
	published?: boolean;
	notes?: string | null;
	days: MenuDay[];
}

export interface MessBill {
	name: string;
	from_date: string;
	to_date: string;
	enrolled_days?: number;
	reduction_days?: number;
	chargeable_days: number;
	daily_rate: number;
	mess_charge: number;
	extras_amount: number;
	total_amount: number;
	fee_invoice?: string | null;
	invoice_status?: string | null;
	outstanding_amount?: number;
	due_date?: string | null;
}

export interface MessReduction {
	name: string;
	from_date: string;
	to_date: string;
	number_of_days: number;
	reason: string;
	description?: string | null;
	status: string;
	approval_remarks?: string | null;
	decided_on?: string | null;
	home_visit_leave?: string | null;
	applied_on?: string;
	can_cancel?: boolean;
}

export interface MessReductionInput {
	from_date: string;
	to_date: string;
	reason: string;
	description?: string;
}

export interface Leave {
	name: string;
	student?: string;
	student_name?: string;
	from_datetime: string;
	to_datetime: string;
	number_of_days?: number;
	destination: string;
	reason: string;
	contact_during_leave?: string;
	applied_by?: string;
	status: string;
	parent_approval_status?: string;
	parent_remarks?: string | null;
	warden_approval_status?: string;
	warden_remarks?: string | null;
	apply_mess_reduction?: boolean | number;
	/** Full mess days the leave covers (the server's noon rule). */
	mess_reduction_days?: number | null;
	mess_reduction?: string | null;
	checked_out_on?: string | null;
	returned_on?: string | null;
	creation?: string;
	can_cancel?: boolean;
}

export interface LeaveInput {
	from_datetime: string;
	to_datetime: string;
	destination: string;
	reason: string;
	contact_during_leave: string;
	apply_mess_reduction: 0 | 1;
}

export interface GrievanceSummary {
	name: string;
	subject: string;
	category: string;
	priority: string;
	status: string;
	is_confidential?: boolean | number;
	creation: string;
	modified?: string;
	resolved_on?: string | null;
	student_rating?: number | null;
}

export interface TimelineEntry {
	id?: string;
	on: string;
	by: string;
	is_staff?: boolean;
	/** Written by the person viewing (only then is it shown as "You"). */
	is_mine?: boolean;
	/** As stored: what people typed, and system entries in English. */
	message: string;
	/** The entry to show, in the reader's language (typed text is left as written). */
	display_message?: string | null;
	status_change?: string | null;
}

export interface Attachment {
	name: string;
	file_name: string;
	file_size?: number;
	/** Served by the portal after the grievance's own checks (the private file URL is not usable). */
	url: string;
}

export interface Grievance extends GrievanceSummary {
	description: string;
	sla_due_by?: string | null;
	assigned?: boolean;
	resolution?: string | null;
	student_feedback?: string | null;
	can_comment?: boolean;
	can_reopen?: boolean;
	can_rate?: boolean;
	attachments?: Attachment[];
	timeline: TimelineEntry[];
}

export interface GrievanceInput {
	category: string;
	subject: string;
	description: string;
	priority: string;
	is_confidential: 0 | 1;
	attachments?: string[];
}

export interface UploadResult {
	file_url: string;
	file_name: string;
}

export interface FeedbackInput {
	feedback_type: string;
	period?: string;
	food_quality?: number;
	cleanliness?: number;
	staff_behaviour?: number;
	facilities?: number;
	overall: number;
	comments?: string;
	is_anonymous: 0 | 1;
}

export interface Certificate {
	name: string;
	purpose: string;
	purpose_details?: string | null;
	status: string;
	creation?: string;
	approved_on?: string | null;
	rejection_reason?: string | null;
	certificate_number?: string | null;
	issue_date?: string | null;
	valid_till?: string | null;
	can_download?: boolean;
	is_expired?: boolean;
}

export interface Ok {
	ok: boolean;
	status?: string;
}

export interface Named {
	name: string;
	status?: string;
}
