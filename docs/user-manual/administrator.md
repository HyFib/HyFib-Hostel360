# Administrator manual

For the **Hostel Administrator**: the person who sets Hostel360 up for ACGCET and keeps it
in order — institution settings, hostels and rooms, staff logins, fee and mess rules,
messaging, and access. Read the [staff introduction](README.md#before-you-start-staff)
first.

Your home is the **Hostel Admin** workspace (`/desk/hostel-admin`): shortcuts to the Room
Board, Approvals, Students, Fee Invoices, Grievances and *Get Support*; number cards for
beds, occupancy, leave, approvals, grievances, collections, dues, critical alerts and open
tickets; and links to every master, setting and report. You also see the *People and
Rooms*, *Fees*, *Mess*, *Student Services* and (read only) *HyFib Operations* workspaces.

The **Get started with Hostel360** onboarding panel on the workspace lists the set-up steps
below and ticks them off as they are done.

## 1. Institution settings

*Hostel Admin > Settings > Hostel Settings*

1. **Institution**: full name, short name (shown in the portal and messages, e.g. ACGCET),
   address, logo (shown on receipts, certificates and the portal), hostel website address.
2. **Portal**: the portal's public address (`portal_url`), the hostel office phone and e-mail
   (shown to students and parents for anything about their stay), and the switches *Student
   Portal Enabled* / *Parent Portal Enabled* (switching one off signs those users out at their
   next action).
3. **Leave and grievances**: *Leave Requires Parent Approval* (on by default), *Leave Return
   Grace Hours* (2: after this a student not back is marked Overdue), *Grievance Escalation
   Hours* (48) and *Urgent* (4).
4. **Bonafide certificate**: signatory name and designation, validity in days (90).
5. **Support**: HyFib helpdesk phone and e-mail (pre-filled).
6. **Privacy notice**: the text students and parents must accept before they see their data,
   and its *Version*. Change the version whenever the text changes: everyone is asked to
   accept again at their next sign-in.

## 2. Academic structure

*Hostel Admin > Academic*

1. **Academic Year**: create the year (e.g. `2026-2027`) with start and end dates, then press
   **Make Current**. Fee structures and new registrations use the current year.
2. **Department** and **Program**: departments with codes; programmes with department, degree
   level (UG/PG/Doctoral/Diploma) and duration.

## 3. Hostels, rooms and beds

*Hostel Admin > Hostels and Rooms*

1. **Hostel**: name, short code (e.g. BH1 — it becomes part of every room number, so choose
   it once), gender (Male/Female/Mixed — allotment refuses a student of another gender),
   warden (a desk user), facilities, photo.
2. **Hostel Block** and **Hostel Floor** for each hostel (optional but needed for block-level
   staff assignments).
3. **Hostel Room**: hostel, block, floor, room number, room type and capacity. Saving creates
   the beds (A, B, C…). Raising the capacity adds beds; lowering it is refused while those beds
   are occupied. Set a room *Under Maintenance* or *Blocked* to keep it out of allotment.
4. Check the result on the **Room Board** (shortcut): every bed by hostel, block and floor,
   coloured by status.

Occupancy figures on hostels and rooms are maintained automatically from the beds. After a
bulk correction, **Recalculate Occupancy** on the Hostel form rebuilds them.

## 4. Staff and desk logins

*People and Rooms > Staff > Hostel Staff > New*

1. Name, employee ID, **Designation** (Hostel Administrator, Warden, Deputy Warden, Office
   Staff, Mess Manager, Mess Staff, Security, Housekeeping, Other), mobile, e-mail, photo.
2. **Assignments**: the hostels this person works in, and optionally a block. Deputy
   wardens, office staff, mess staff and security staff see only their assigned hostels; a
   block assignment narrows that to the students living in that block. Wardens,
   administrators and mess managers see all hostels.
3. Tick **Create User** (and *Send Welcome Email*) and save: a desk login is created with the
   role for the designation and two-factor sign-in. The form shows the login and lets you
   **Open User** or **Resend Welcome Email**.
4. When someone leaves, untick **Is Active**: their login is disabled at once.

Institution officials who should only look (principal, accounts, auditors) get a desk user
with the **Institution Auditor** role: read-only dashboards, reports and monitoring. Create
them under *User* and give them only that role.

Review access every quarter: *User* list with roles, and Hostel Staff records still active.

## 5. Students

Registration, validation, imports and allotment are day-to-day work of the hostel office
(see the [office staff manual](office-staff.md)). As administrator you can do all of it,
and you decide:

- whether a student or guardian may use the portal (validation, rejection);
- corrections the office cannot make (medical notes are visible only to administrators and
  wardens);
- re-admission of a former resident into another hostel (scoped staff cannot see them).

## 6. Fees and online payment

*Fees* workspace.

1. **Fee Head**: defaults are installed (Hostel Rent, Establishment Charges, Electricity
   Charges, Caution Deposit (refundable), Mess Advance, Mess Charges, Mess Extras, Fine,
   Other). Add heads as needed; untick *Is Active* to retire one.
2. **Fee Structure**: name, academic year, due date, and optionally the hostel, year of study
   and programme it applies to (blank = all); the items with amounts. **Publish Dues** shows
   how many invoices will be created and creates one invoice per matching validated, active
   student who does not have one for this structure yet (in the background; the form shows
   progress). Students and parents are notified. Run it again after new admissions: only the
   new students get invoices.
3. **Hostel Payment Settings** (*Hostel Admin > Settings > Payment Settings*):
   - *Gateway*: Razorpay or PayU; the keys of **ACGCET's merchant account** (Razorpay key ID,
     key secret and webhook secret; or PayU merchant key and salt). Money goes straight to
     that account.
   - **Test Connection** checks the keys.
   - *Test Mode*: on while using the gateway's test keys. Only the students listed in **Test
     Payers** (and administrators) can then pay online; their receipts are marked as test
     receipts and left out of collection figures. Switch it off with the live keys.
   - Set up the gateway's webhook as shown on the form (Razorpay: events `payment.captured`,
     `payment.failed`, `order.paid`, `refund.processed`; PayU: the webhook URL shown).
   - *Transaction Expiry Minutes* (60) and fee **reminders**: on/off, days before the due date
     (3), interval (3 days), maximum reminders per invoice (10).
   - *Enabled*: turns online payment on in the portal.

## 7. Mess rules

*Hostel Admin > Settings > Mess Settings*: minimum days for a mess reduction (3), advance
notice (1 day), who approves reductions (Deputy Warden or Mess Manager; the Warden and
administrators always can), monthly cap on reduction days (0 = none), bill due days (10),
rounding to the nearest rupee, low-stock alerts, whether receipts need an approved indent,
and the indent amount above which the Warden or an administrator must approve.

Messes themselves (*Mess > Setup > Hostel Mess*) list the hostels they serve and their mess
manager. See the [mess manager manual](mess-manager.md).

## 8. Messages (SMS, WhatsApp, e-mail)

*Hostel Admin > Settings > Messaging Settings* (with HyFib):

1. **SMS**: provider (MSG91 or a generic HTTP gateway), API key, the DLT-approved sender ID
   (header) and Principal Entity ID.
2. **WhatsApp**: phone number ID, business account ID, permanent access token.
3. **E-mail**: uses the site's outgoing e-mail account.
4. **Sign-in codes**: channels (SMS, WhatsApp, or both), length, expiry and limits (keep the
   defaults); **Trusted Networks** must list the campus's public address(es) so students on
   hostel Wi-Fi are not limited as one person. Tick **Allow E-mail Sign-in Codes** to offer
   **E-mail** on the sign-in page as well: students and parents still sign in with their
   mobile number, and the code goes to the e-mail address on their student or guardian record
   (never to the placeholder login of someone without an address). It needs **Enable Email**
   and a default outgoing Email Account that sends by SMTP; the code is sent straight to the
   mail server and is never kept in the Email Queue or the Message Log. Off by default.
5. **Test Mode**: messages are logged, not sent. Switch it off once the DLT and WhatsApp
   templates are approved, after **Send Test Message** to a known number on each channel.

**Message Template** (*Hostel Admin > Communication*): one template per event, channel and
language (English and Tamil). For SMS enter the **DLT Template ID** (and MSG91 flow ID) of
the approved template; for WhatsApp the approved **template name** and language. **Preview**
shows the message with sample values. Edit wording carefully: an SMS must match its DLT
template exactly.

**Message Log**: every message with its status (Queued, Sent, Failed, Skipped), masked
recipient, attempts and error; **Retry Now** on a failed one. Message texts are visible only
to administrators.

## 9. Reports and dashboards

All reports export to Excel and PDF (*Export* / *Print* on the report page).

| Area | Reports |
|---|---|
| Rooms and people | Hostel Occupancy, Student Register, Room Allotment History |
| Fees | Fee Collection, Fee Dues, Daily Collection Summary, Gateway Reconciliation |
| Mess | Mess Stock Balance, Mess Consumption (daily, monthly, yearly), Mess Cost Summary, Mess Reduction Register |
| Student services | Leave Register, Students On Leave, Grievance Summary, Feedback Summary, Bonafide Register |
| Operations | Alert History, Uptime and Incidents, Monthly Performance Report |

## 10. Institution officials (auditor role)

Users with the **Institution Auditor** role see the *Hostel Admin*, *People and Rooms*,
*Fees*, *Mess*, *Student Services* and *HyFib Operations* workspaces read-only:

- dashboards, every report above, with export;
- **Operations Status** (health of the installation), Alert History, Uptime and Incidents,
  backup records, security incidents and the **Monthly Performance Report**, which is also
  e-mailed to the institution officials listed by HyFib by the 6th of each month;
- the backup repository, read-only, through ACGCET's OCI account (see
  [BACKUP-AND-DR.md](../BACKUP-AND-DR.md) §5).

Auditors cannot change records and are never assigned grievances.

## 11. Year-round tasks

| When | Task |
|---|---|
| Before each academic year | New academic year and **Make Current**; update fee structures; check room capacities and statuses |
| Admissions | Office imports and validates students; allot rooms; **Publish Dues** again for the new students |
| Every month | Check Fee Dues and Daily Collection Summary; mess bill run published by the mess manager; review grievances and feedback summaries; attend the monthly performance review with HyFib |
| Each term | Review staff assignments and logins; review messaging and payment settings |
| On request | Personal data requests (DPDP Act): the desk's *Personal Data Download Request* and *Personal Data Deletion Request* cover student and guardian records |

## 12. Getting help

*Hostel Admin > Get Support* opens a **Support Ticket** to the HyFib helpdesk: subject,
category (Outage, Bug, Data Correction, Access or Login, How To, Change Request, Other),
priority and description. The helpdesk responds within 1 hour and resolves within 4 hours;
you see the status on the ticket and get notified of updates. When it is resolved, **Confirm
and Close**, or **Re-open** with what is still wrong. For an outage, also phone
**+91 96060 60596**.
