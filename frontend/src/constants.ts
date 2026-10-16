/** Option values the server accepts (docs/SOLUTION-DESIGN.md §4). Labels come from i18n. */

export const REDUCTION_REASONS = [
	"Home Visit",
	"Medical",
	"Internship",
	"Sports or Cultural Event",
	"College Tour",
	"Other",
] as const;

export const GRIEVANCE_CATEGORIES = [
	"Room and Furniture",
	"Electrical",
	"Plumbing",
	"Cleanliness",
	"Mess and Food",
	"Water",
	"Internet",
	"Security",
	"Health",
	"Ragging or Harassment",
	"Staff Behaviour",
	"Other",
] as const;

/** Always confidential, urgent and sent straight to the chief warden. */
export const SENSITIVE_CATEGORY = "Ragging or Harassment";

/**
 * Numbers offered next to a ragging or harassment complaint. They are public, round-the-clock
 * services; the hostel office's own number (Hostel Settings) is shown first when it is set.
 * The software vendor's helpdesk is deliberately never offered here.
 */
export const EMERGENCY_NUMBER = "112";
export const ANTI_RAGGING_HELPLINE = "1800-180-5522";

export const GRIEVANCE_PRIORITIES = ["Low", "Medium", "High", "Urgent"] as const;

export const BONAFIDE_PURPOSES = [
	"Bank Loan",
	"Scholarship",
	"Passport",
	"Visa",
	"Internship",
	"Address Proof",
	"Other",
] as const;

export const FEEDBACK_TYPES = ["Mess", "Hostel", "Facilities", "General"] as const;

/** Rating questions shown for each feedback type ("overall" is always asked). */
export const FEEDBACK_QUESTIONS: Record<(typeof FEEDBACK_TYPES)[number], string[]> = {
	Mess: ["food_quality", "cleanliness", "staff_behaviour"],
	Hostel: ["cleanliness", "facilities", "staff_behaviour"],
	Facilities: ["facilities", "cleanliness"],
	General: [],
};

export const BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"] as const;

export const MAX_GRIEVANCE_ATTACHMENTS = 5;
