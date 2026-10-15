import type { RouteRecordRaw } from "vue-router";

/** Routes under /hostel (design §7). Views load on demand to keep the first download small. */
export const routes: RouteRecordRaw[] = [
	{
		path: "/login",
		name: "login",
		component: () => import("@/views/LoginView.vue"),
		meta: { public: true, guestOnly: true, bare: true, title: "login.title" },
	},
	{ path: "/", name: "home", component: () => import("@/views/HomeView.vue"), meta: { title: "nav.home" } },
	{
		path: "/privacy",
		name: "privacy",
		component: () => import("@/views/PrivacyView.vue"),
		meta: { public: true, firstRun: true, title: "privacy.title" },
	},
	{
		path: "/help",
		name: "help",
		component: () => import("@/views/HelpView.vue"),
		meta: { public: true, firstRun: true, title: "help.title" },
	},
	{
		path: "/confirm-details",
		name: "confirm-details",
		component: () => import("@/views/ConfirmDetailsView.vue"),
		meta: { kind: "student", firstRun: true, title: "confirm.title" },
	},
	{ path: "/profile", name: "profile", component: () => import("@/views/ProfileView.vue"), meta: { title: "profile.title" } },
	{ path: "/more", name: "more", component: () => import("@/views/MoreView.vue"), meta: { title: "nav.more" } },
	{
		path: "/fees",
		name: "fees",
		component: () => import("@/views/FeesView.vue"),
		meta: { requiresValidated: true, title: "fees.title" },
	},
	{
		path: "/fees/pay/:invoice?",
		name: "pay",
		component: () => import("@/views/PayView.vue"),
		meta: { requiresValidated: true, title: "pay.title" },
	},
	{
		path: "/receipts",
		name: "receipts",
		component: () => import("@/views/ReceiptsView.vue"),
		meta: { requiresValidated: true, title: "receipts.title" },
	},
	{
		path: "/menu",
		name: "menu",
		component: () => import("@/views/MenuView.vue"),
		meta: { requiresValidated: true, title: "menu.title" },
	},
	{
		path: "/mess-bills",
		name: "mess-bills",
		component: () => import("@/views/MessBillsView.vue"),
		meta: { requiresValidated: true, title: "messBills.title" },
	},
	{
		path: "/mess-reduction",
		name: "mess-reduction",
		component: () => import("@/views/MessReductionView.vue"),
		meta: { requiresValidated: true, title: "reduction.title" },
	},
	{
		path: "/mess-reduction/new",
		name: "mess-reduction-new",
		component: () => import("@/views/MessReductionNewView.vue"),
		meta: { requiresValidated: true, title: "reduction.new" },
	},
	{
		path: "/leave",
		name: "leave",
		component: () => import("@/views/LeaveView.vue"),
		meta: { requiresValidated: true, title: "leave.title" },
	},
	{
		path: "/leave/new",
		name: "leave-new",
		component: () => import("@/views/LeaveNewView.vue"),
		meta: { requiresValidated: true, title: "leave.new" },
	},
	{
		path: "/leave/approvals",
		name: "leave-approvals",
		component: () => import("@/views/LeaveApprovalsView.vue"),
		meta: { kind: "parent", requiresValidated: true, title: "approvals.title" },
	},
	{
		path: "/grievances",
		name: "grievances",
		component: () => import("@/views/GrievancesView.vue"),
		meta: { requiresValidated: true, title: "grievances.title" },
	},
	{
		path: "/grievances/new",
		name: "grievance-new",
		component: () => import("@/views/GrievanceNewView.vue"),
		meta: { requiresValidated: true, title: "grievances.new" },
	},
	{
		path: "/grievances/:name",
		name: "grievance",
		component: () => import("@/views/GrievanceDetailView.vue"),
		meta: { requiresValidated: true, title: "grievances.detailTitle" },
	},
	{
		path: "/feedback",
		name: "feedback",
		component: () => import("@/views/FeedbackView.vue"),
		meta: { kind: "student", requiresValidated: true, title: "feedback.title" },
	},
	{
		path: "/certificates",
		name: "certificates",
		component: () => import("@/views/CertificatesView.vue"),
		meta: { requiresValidated: true, title: "certificates.title" },
	},
	{
		path: "/notifications",
		name: "notifications",
		component: () => import("@/views/NotificationsView.vue"),
		meta: { title: "notifications.title" },
	},
	{
		path: "/:pathMatch(.*)*",
		name: "not-found",
		component: () => import("@/views/NotFoundView.vue"),
		meta: { public: true, title: "notFound.title" },
	},
];
