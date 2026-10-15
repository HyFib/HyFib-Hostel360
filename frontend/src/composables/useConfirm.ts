/** Promise-based confirmation dialog: `if (await confirm({...})) { … }`. */
import { reactive } from "vue";

interface ConfirmRequest {
	title: string;
	message?: string;
	confirmLabel?: string;
	cancelLabel?: string;
	danger?: boolean;
}

export const confirmState = reactive<ConfirmRequest & { open: boolean }>({ open: false, title: "" });
let resolver: ((value: boolean) => void) | null = null;

export function settleConfirm(value: boolean): void {
	confirmState.open = false;
	resolver?.(value);
	resolver = null;
}

export function confirm(request: ConfirmRequest): Promise<boolean> {
	if (resolver) settleConfirm(false);
	Object.assign(
		confirmState,
		{ message: undefined, confirmLabel: undefined, cancelLabel: undefined, danger: false },
		request,
		{
			open: true,
		},
	);
	return new Promise<boolean>((resolve) => {
		resolver = resolve;
	});
}
