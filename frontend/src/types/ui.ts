export interface TimelineStep {
	key: string;
	label: string;
	state: "done" | "current" | "upcoming" | "failed" | "skipped";
	detail?: string;
}
