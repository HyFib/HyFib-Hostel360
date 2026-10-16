# Training plan

Tender clause 5.2 and T&C 27: training for all identified users, on-site at ACGCET Hostels,
Karaikudi, or virtual, as ACGCET prefers, with at least two sessions within the
implementation cost. This plan follows the bid (document 11 §3) and the user manuals in
[user-manual/](user-manual/README.md).

## 1. Sessions

| Session | When | Audience | Length | Format |
|---|---|---|---|---|
| 1 — Initial | Implementation days 21–27, before go-live | Administrators, wardens, deputy wardens, office staff, mess manager and mess staff, security staff, institution officials (auditor role) | One sitting per role group (below) | Hands-on, on the UAT site with demonstration data (`h360-demo-data`), each participant signed in with their own two-factor login |
| 2 — Refresher | Within 30 days after go-live | All staff users | 1–2 hours per group | Questions from live use, advanced reports, the first month-end mess billing, anything the helpdesk saw people struggle with |
| Self-help | At go-live | Students and parents | — | One-page guides in English and Tamil, short videos for sign-in, payment, leave and mess reduction, the portal's Help page |
| Operations handover | Days 26–30 | ACGCET IT contact, HyFib helpdesk rota | 2 hours | Operations Status, alerts, tickets, monthly report, backup access |

Every new staff member after go-live gets the role's manual and a 30-minute walk-through from
the hostel office or the HyFib helpdesk; HyFib repeats a full session on request at no cost.

## 2. Content per role group

| Group | Initial session (session 1) | Refresher (session 2) | Manual |
|---|---|---|---|
| Hostel Administrator | Two-factor sign-in; Hostel Settings, privacy notice; hostels, blocks, rooms and beds; staff and desk logins; fee heads, structures and **Publish Dues**; payment, messaging and mess settings; reports and dashboards; support tickets | Month-end review of dues and collections; academic year change; user and access review; exporting reports | [administrator.md](user-manual/administrator.md) |
| Warden and deputy wardens | **Approvals** inbox: leave, mess reductions, bonafide requests, indents, escalated grievances; grievance handling and confidentiality; Room Board; Students On Leave; Gate Pass oversight | Escalation patterns, grievance SLA performance, feedback summary | [warden.md](user-manual/warden.md) |
| Hostel office staff | Student registration and validation, bulk import, guardians, portal logins and **Unlock Sign-in**; room allotment, transfer, vacate; counter receipts and invoices; bonafide hand-over; reports | Re-admissions, corrections, reconciliation of online payments, daily collection summary | [office-staff.md](user-manual/office-staff.md) |
| Mess manager and mess staff | Items and suppliers; stock receipts and issues; indents; weekly menu and **Publish**; mess reductions; extras and expenses | **Month-end billing**: Mess Bill Run compute, check, publish; consumption and cost reports | [mess-manager.md](user-manual/mess-manager.md) |
| Security staff | Gate Pass page on the gate tablet: checking students out and back in, overdue returns | — (included in the walk-through for new gate staff) | [security-staff.md](user-manual/security-staff.md) |
| Institution officials (auditor role) | Dashboards, Operations Status, reports, Monthly Performance Report, backup repository access | — | [administrator.md](user-manual/administrator.md) §10 |
| HyFib operations | Runbook, alert catalogue, escalation, tickets, monthly report and review, security incidents, backups and restore test, deployment | Lessons from the first month | [hyfib-operations.md](user-manual/hyfib-operations.md), [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) |
| Students and parents | Self-help: sign-in with OTP, privacy notice, confirm details, fees and payment, menu and mess bills, mess reduction, leave and parent approval, grievances, certificates | — | [student.md](user-manual/student.md), [parent.md](user-manual/parent.md), [Tamil quick guide](user-manual/student-parent-quick-guide-ta.md) |

## 3. Materials

| Material | Form | Language |
|---|---|---|
| Role manuals | `docs/user-manual/*.md`, printed as PDF for each participant | English |
| Student and parent quick guide | One page, printed and on the website | English and Tamil |
| Short videos (sign-in, pay fees, apply for leave, mess reduction) | 1–2 minutes each, linked from the portal's Help page and the hostel website | English and Tamil |
| UAT site with demonstration data | `bench --site <uat> h360-demo-data --students 120` | — |
| Exercise sheet per role | The manual's step lists, done on the UAT site during the session | English |

## 4. Records

For each session HyFib keeps: date, place or meeting link, trainer, attendees with role and
signature (or the meeting's attendance export), topics covered, and questions raised with
their answers. The attendance record is the output of the training phase in the
implementation plan and is handed to ACGCET with the completion certificate.
