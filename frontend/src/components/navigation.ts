import {
	Award,
	Bell,
	CalendarDays,
	ClipboardCheck,
	Ellipsis,
	House,
	LifeBuoy,
	MessageSquareWarning,
	Receipt,
	ShieldCheck,
	Star,
	User,
	Utensils,
	UtensilsCrossed,
	Wallet,
	CalendarOff,
} from "@lucide/vue";
import type { Component } from "vue";
import type { PortalKind } from "@/api/types";

export interface NavItem {
	name: string;
	label: string;
	icon: Component;
	/** Only for this kind of user. */
	kind?: PortalKind;
	/** Shown in the phone's bottom bar (max five). */
	primary?: boolean;
	/** Other route names that should highlight this item. */
	match?: string[];
}

export const NAV_ITEMS: NavItem[] = [
	{ name: "home", label: "nav.home", icon: House, primary: true },
	{ name: "fees", label: "nav.fees", icon: Wallet, primary: true, match: ["pay"] },
	{ name: "receipts", label: "nav.receipts", icon: Receipt },
	{ name: "menu", label: "nav.mess", icon: Utensils, primary: true, kind: "student" },
	{ name: "menu", label: "nav.menu", icon: Utensils, kind: "parent" },
	{ name: "mess-bills", label: "nav.messBills", icon: UtensilsCrossed },
	{ name: "mess-reduction", label: "nav.messReduction", icon: CalendarOff, match: ["mess-reduction-new"] },
	{ name: "leave-approvals", label: "nav.approvals", icon: ClipboardCheck, primary: true, kind: "parent" },
	{ name: "leave", label: "nav.leave", icon: CalendarDays, primary: true, match: ["leave-new"] },
	{ name: "grievances", label: "nav.grievances", icon: MessageSquareWarning, match: ["grievance-new", "grievance"] },
	{ name: "certificates", label: "nav.certificates", icon: Award },
	{ name: "feedback", label: "nav.feedback", icon: Star, kind: "student" },
	{ name: "notifications", label: "nav.notifications", icon: Bell },
	{ name: "profile", label: "nav.profile", icon: User },
	{ name: "help", label: "nav.help", icon: LifeBuoy },
	{ name: "privacy", label: "nav.privacy", icon: ShieldCheck },
];

export const MORE_ITEM: NavItem = { name: "more", label: "nav.more", icon: Ellipsis, primary: true };

export function itemsFor(kind: PortalKind | null): NavItem[] {
	return NAV_ITEMS.filter((item) => !item.kind || item.kind === kind);
}

export function primaryItemsFor(kind: PortalKind | null): NavItem[] {
	return [...itemsFor(kind).filter((item) => item.primary), MORE_ITEM];
}

export function isActive(item: NavItem, routeName: string | null | undefined): boolean {
	if (!routeName) return false;
	return item.name === routeName || (item.match ?? []).includes(routeName);
}
