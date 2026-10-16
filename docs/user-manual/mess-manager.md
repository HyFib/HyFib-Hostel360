# Mess manager manual

For the **Mess Manager** and **Mess Staff**. Mess staff record stock receipts and issues and
prepare menus; the mess manager also approves indents and reductions (as configured), and
runs the monthly billing. Read the [staff introduction](README.md#before-you-start-staff)
first.

Your workspace is **Mess**: shortcuts to Stock Entry, Menu and Reductions; number cards for
pending reductions, indents pending approval and unbilled extras; the daily-rate chart; and
the mess reports.

## 1. Set up

- **Hostel Mess** (*Mess > Setup*): the mess, the hostels it serves, its mess manager. Students
  of those hostels are billed by this mess.
- **Mess Item**: name (and local/Tamil name), code, category, unit (Kg, Litre, Nos, Packet,
  Cylinder…), **reorder level** and reorder quantity, perishable or not.
- **Mess Supplier**: name, GSTIN, contact person, mobile, e-mail, address.
- **Mess Settings** are kept by the administrator (reduction rules, bill due days, rounding,
  indent rules).

## 2. Stock

*Mess > Stock Entry > New*

| Entry type | When |
|---|---|
| Receipt | Goods received from a supplier: supplier, supplier invoice number and date, items with quantity and **rate** |
| Issue | Provisions issued to the kitchen: items and quantity (the rate is the current average cost) |
| Adjustment | Stock count differences (positive or negative), with remarks |
| Return to Supplier | Goods sent back |

Pick the mess, posting date and time, add items (the form shows the quantity available and
the average rate), then **Submit**. Submitted entries update the **Stock Ledger** with a
running balance and moving-average cost. An issue cannot take more than the stock on hand.
A wrong entry is cancelled (and amended); entries dated on or before the last published bill
period cannot be changed.

When *Require Indent for Receipts* is on in Mess Settings, a receipt must refer to an approved
indent.

## 3. Indents (demand of commodities)

*Mess > Indent > New*

1. Mess, required-by date, and optionally the week's **Mess Menu** it is for (the approver sees
   the planned meals).
2. **Get Items Below Reorder Level** fills items at or below their reorder level with a
   suggested quantity; add or change items and estimated rates.
3. Submit: status *Pending Approval*. Approvers (mess manager, warden, administrator) are
   notified and see it in **Approvals**. The person who raised an indent cannot approve it,
   and indents above the warden threshold need the Warden or an administrator.
4. After approval: **Mark as Ordered** (with the supplier), then **Receive Goods**, which
   prepares the stock Receipt from the indent; check quantities and rates and submit it. The
   indent becomes *Received*.

## 4. Weekly menu

*Mess > Menu > New*

1. Mess and the week's **Monday**.
2. **Copy Previous Week** brings last week's meals onto this week's dates, or **Add Days and
   Meals** adds a row per day and meal (Breakfast, Lunch, Snacks, Dinner). Type the dishes;
   tick *Is Special* for a special meal.
3. **Publish**: students and parents see the menu in the portal (today's menu on their home
   screen) and get a notification. Only published menus are shown.

## 5. Mess reductions

Students apply in the portal (or through an approved home-visit leave) for days they will be
away. The rules come from Mess Settings: at least the minimum days (3), applied the advance
notice ahead (1 day), no overlap, the monthly cap.

If you are the configured approver: **Approvals > Mess Reduction**, or *Mess > Reductions*,
open the application and **Approve** or **Reject** with remarks. The student is notified.
Approved days are deducted automatically in the bill run.

Report: **Mess Reduction Register**.

## 6. Extras and expenses

- **Mess Extra Charge**: a charge for one student (guest meal, special food, extra item,
  breakage, other) with date, amount and description. It is added to that student's next
  bill and marked *Billed*.
- **Mess Expense**: costs other than provisions (gas, fuel, wages, electricity, water,
  maintenance, other) with amount, supplier, bill number and the bill attached; submit it.
  Submitted expenses of the period are part of the month's total cost.

## 7. Monthly bill run (dividing system)

*Mess > Bill Run > New*, after the month's stock issues and expenses are all entered.

1. Mess, **from date** and **to date** (the month), and *Other Credits* if any income reduces
   the cost (with remarks).
2. **Compute Bills**. Hostel360 works out:
   - total cost = value of issues in the period + submitted expenses − other credits;
   - for each student, chargeable days = days enrolled in the mess (while living in a hostel
     the mess serves, and while *Mess Enrolled* was on) − approved reduction days;
   - **daily rate** = total cost ÷ all chargeable days (rounded to paise);
   - each bill = chargeable days × daily rate + the student's unbilled extras.
   Large runs compute in the background; the form refreshes when done and shows the daily
   rate, student-days and total billed. *View > Bills* lists every student's bill;
   *View > Consumption* shows what was used.
3. Check the figures (the **Mess Cost Summary** report helps). If something was missing, add
   it and **Compute Bills** again: the run is recomputed, never duplicated.
4. **Submit** to publish: one fee invoice per bill (*Mess Charges* and *Mess Extras*), due
   after the bill due days; students and parents are notified and can pay online. The
   period is then closed for stock and expenses.
5. A published run can be **cancelled** only while none of its invoices has been paid; its
   invoices are withdrawn. Make the correction and run it again.

## 8. Reports

| Report | Use |
|---|---|
| Mess Stock Balance | Quantity and value of each item on any date |
| Mess Consumption | Item-wise consumption by day, month or year, with a chart |
| Mess Cost Summary | Cost, student-days and daily rate per bill run |
| Mess Reduction Register | Reductions by status, student and period |

All export to Excel and PDF. A weekly **low-stock digest** lists items at or below their
reorder level (if *Low Stock Alert* is on).

## 9. Grievances about food

Grievances in the *Mess and Food* category are visible to you (*Student Services >
Grievances*). Read them and tell the warden what was done; the warden assigns and resolves
them.
