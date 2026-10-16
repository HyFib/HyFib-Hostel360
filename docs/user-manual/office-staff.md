# Hostel office manual

For **Hostel Office Staff**: registrations, room allotment, fees and receipts, and the
paperwork students ask for. You see the hostels you are assigned to. Read the
[staff introduction](README.md#before-you-start-staff) first.

Your workspaces: **People and Rooms** (students, guardians, imports, Room Board, allotments,
reports) and **Fees** (invoices, receipts, structures, online payments, reports).

## 1. Register a student

*People and Rooms > Students > New* (or *Hostel Student > New*).

1. **Register Number** (it becomes the record's name, so check it before saving), roll
   number, first and last name, gender, date of birth, blood group, photo.
2. **Academic**: programme (the department fills in), year of study, admission year,
   academic year.
3. **Contact**: the student's **mobile number** (10 digits; it is their portal sign-in, and
   must be unique), e-mail (where sign-in codes go if the student chooses E-mail, when the
   hostel offers it), address, emergency contact.
4. **Guardians**: add rows linking **Hostel Guardian** records (create them from the row if
   new: name, relation, **mobile number** — the parent's portal sign-in — e-mail). Tick
   *Is Primary* for the main contact; *Can Approve Leave* (on by default) decides who is
   asked to approve leave.
5. Save. The status starts *Active* and validation *Pending*.

### Validate the registration

Check the details against the college's admission records, then on the student's form
**Validate** (or **Reject** with the reason). Only validated students can sign in to the
portal, and parents only once one of their wards is validated. The student and parents are
told by SMS/WhatsApp that the portal is open.

### Portal login

*Actions > Create Portal User* creates the student's portal account (the same on the
guardian's form for a parent). Students and parents never get a password: they sign in with
a code sent to their registered mobile. An account is also created automatically at their
first sign-in, so this step is optional.

**Locked out?** After too many wrong codes a number is locked for an hour. When the student
or parent calls, confirm who they are, then **Actions > Unlock Sign-in** on their record.

**Mobile number changed?** Correct it on the student's (or guardian's) record; the new number
is the sign-in from then on.

## 2. Import many students at once

*People and Rooms > Bulk Import > New*

1. **Download** the *Excel Template* (or *CSV Template*), fill one student per row (with
   guardian and, if known, hostel/room/bed columns), save it.
2. Attach the file, choose the academic year and the options: *Update Existing* (students
   already registered are updated, matched by register number), *Allot Rooms* (allot the
   beds given in the file), *Create Portal Users*. Save.
3. **Validate File**: every row is checked and nothing is written. The preview shows rows to
   create, update, skip, and errors and warnings per row.
4. Fix the file and attach it again, or **Start Import**: it runs in the background ("you can
   leave this page"); rows with errors are skipped.
5. When it finishes, the form shows how many rows were imported, skipped and failed.
   **Download > Rows to Fix** gives a file of the failed rows with the reason; correct them
   and import that file.

You see only the imports you created.

## 3. Rooms

### Allot a bed

Either:

- **Room Board** (*People and Rooms > Room Board*): choose the hostel (and block/floor),
  click a vacant bed, **Allot**, pick the student (only students without a bed whose gender
  matches the hostel are offered), from date and optional to date; or
- on the student's form, **Create > Allot Room**, or *Room Allotment > New*.

The allotment is checked (bed vacant, gender, no other active allotment) and submitted; the
bed turns occupied and the student and parents are notified.

### Transfer or vacate

On the Room Board click the occupied bed (or open the allotment):

- **Transfer**: choose the new bed and transfer date. The old allotment is kept as
  *Transferred* and a new one is made.
- **Vacate**: date and reason (course completed, discontinued, moved to day scholar…). The bed
  becomes vacant; history is never deleted.

Allotments with an end date are vacated automatically the day after.

**Reserve** or **Maintenance** on a vacant bed keeps it out of allotment; **Mark Vacant**
releases it.

## 4. Fees

### Dues

The administrator publishes dues from a **Fee Structure** (**Publish Dues**); each validated
student then has a **Fee Invoice**. You can also create an invoice by hand (*Fees > Fee
Invoices > New*): student, type (Hostel Fee, Mess Bill, Fine, Other), due date, items. Submit
it to make it payable. Mess bills arrive as invoices from the mess bill run.

Students and parents see their dues and pay online in the portal; a reminder goes out
automatically before the due date and while overdue (at most every 3 days). To remind one
student now: open the invoice, **Send Reminder**.

### Money received at the counter

On the invoice: **Record Payment**, or *Fees > Receipts > New*:

1. Student, payment date, **mode** (Cash, UPI, Demand Draft, Cheque, Bank Transfer), amount,
   reference number and date, bank.
2. **Get Outstanding Invoices** fills the allocations, oldest due first; adjust the amounts if
   the payment covers only some dues.
3. Submit. The invoices are updated (Partly Paid / Paid) and the student and parents are
   notified. **Print Receipt** prints it.

A wrong receipt is **cancelled** (and amended if needed); the invoices go back to unpaid.
Never cancel a receipt of an online payment without checking with the administrator.

### Online payments

*Fees > Online Payment Transaction*: every online payment attempt with its status
(Initiated, Pending, Success, Failed, Expired, Refunded) and the receipt it produced. Receipts
for online payments are made automatically once the gateway confirms; nothing to do by hand.

If a student says money was taken but there is no receipt: open their transaction and press
**Check with Gateway**. Payments are also checked automatically every 15 minutes and every
night. The **Gateway Reconciliation** report lists anything that does not match.

### Reports

| Report | Use |
|---|---|
| Daily Collection Summary | Money received per day and mode (cash book) |
| Fee Collection | Receipts in a period, by student, hostel, mode |
| Fee Dues | Who owes what, overdue first |
| Gateway Reconciliation | Online payments against receipts |

The *Fees* workspace shows today's and this month's collection, outstanding dues and overdue
invoices.

## 5. Certificates and requests

- **Bonafide certificates**: students request them in the portal; the warden approves. When
  the student collects a printed copy, open the request and **Mark Handed Over**. You can
  enter a request on the desk for a student who asks at the counter.
- **Leave** and **grievances** can be entered on the desk for a student who comes to the office
  (*Student Services*); approvals stay with the wardens.

## 6. Reports on residents

**Student Register** (filters by hostel, programme, year, status; export to Excel), **Room
Allotment History**, **Hostel Occupancy**, **Students On Leave**.
