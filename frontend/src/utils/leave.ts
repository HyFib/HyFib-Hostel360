import type { Leave } from "@/api/types";
import type { TimelineStep } from "@/types/ui";
import { t } from "@/i18n";
import { formatDateTime } from "./format";

/** Progress of a leave request through parent and warden approval to return. */
export function leaveSteps(leave: Leave): TimelineStep[] {
	const steps: TimelineStep[] = [
		{
			key: "applied",
			label: t("leave.steps.applied"),
			state: "done",
			detail: leave.creation ? formatDateTime(leave.creation) : undefined,
		},
	];

	const parent = leave.parent_approval_status;
	if (parent === "Not Required") {
		steps.push({ key: "parent", label: t("leave.steps.parentNotRequired"), state: "skipped" });
	} else {
		steps.push({
			key: "parent",
			label: t("leave.steps.parent"),
			state:
				parent === "Approved"
					? "done"
					: parent === "Rejected"
						? "failed"
						: leave.status === "Pending Parent Approval"
							? "current"
							: "upcoming",
			detail: leave.parent_remarks || (parent && parent !== "Pending" ? t(`status.${parent}`) : undefined),
		});
	}

	const warden = leave.warden_approval_status;
	steps.push({
		key: "warden",
		label: t("leave.steps.warden"),
		state:
			warden === "Approved"
				? "done"
				: warden === "Rejected"
					? "failed"
					: leave.status === "Pending Warden Approval"
						? "current"
						: "upcoming",
		detail: leave.warden_remarks || (warden && warden !== "Pending" ? t(`status.${warden}`) : undefined),
	});

	if (leave.status === "Cancelled") {
		steps.push({ key: "cancelled", label: t("leave.steps.cancelled"), state: "failed" });
		return steps;
	}
	if (leave.status === "Rejected") return steps;

	steps.push({
		key: "out",
		label: t("leave.steps.checkedOut"),
		state: leave.checked_out_on ? "done" : leave.status === "Approved" ? "current" : "upcoming",
		detail: leave.checked_out_on ? formatDateTime(leave.checked_out_on) : undefined,
	});
	steps.push({
		key: "returned",
		label: leave.status === "Overdue" ? t("leave.steps.overdue") : t("leave.steps.returned"),
		state: leave.returned_on
			? "done"
			: leave.status === "Overdue"
				? "failed"
				: leave.status === "Checked Out"
					? "current"
					: "upcoming",
		detail: leave.returned_on ? formatDateTime(leave.returned_on) : undefined,
	});
	return steps;
}
