// Hostel office staff on Frappe Desk: open a list view (rows and count), and now and then a
// record from it, using a staff user's API key.
import { check, sleep } from "k6";

import { SHAPE } from "./config.js";
import { deskCall, message } from "./http.js";

// The lists the office works from all day. Filters use only fields every one of these
// DocTypes has, so the test does not depend on optional columns.
const LISTS = [
  { doctype: "Hostel Student", weight: 25, filters: [["Hostel Student", "status", "=", "Active"]] },
  { doctype: "Room Allotment", weight: 15, filters: [["Room Allotment", "docstatus", "=", 1]] },
  { doctype: "Hostel Fee Invoice", weight: 20, filters: [["Hostel Fee Invoice", "docstatus", "=", 1]] },
  { doctype: "Hostel Payment", weight: 10, filters: [] },
  { doctype: "Home Visit Leave", weight: 15, filters: [] },
  { doctype: "Hostel Grievance", weight: 15, filters: [] },
];

function pickList() {
  let roll = Math.random() * 100;
  for (const list of LISTS) {
    roll -= list.weight;
    if (roll < 0) return list;
  }
  return LISTS[0];
}

function field(doctype, name) {
  return `\`tab${doctype}\`.\`${name}\``;
}

export function deskUser() {
  const list = pickList();
  const page = Math.random() < 0.8 ? 0 : 20;

  const rows = deskCall(
    "frappe.desk.reportview.get",
    {
      doctype: list.doctype,
      fields: [field(list.doctype, "name"), field(list.doctype, "modified"), field(list.doctype, "docstatus")],
      filters: list.filters,
      order_by: `${field(list.doctype, "creation")} desc`,
      start: page,
      page_length: 20,
      view: "List",
      group_by: "",
      with_comment_count: 1,
    },
    { name: "list_view" },
  );
  const result = message(rows);
  check(rows, { "desk list loaded": () => result !== undefined });

  const count = deskCall(
    "frappe.desk.reportview.get_count",
    { doctype: list.doctype, filters: list.filters, fields: [], distinct: false, limit: 1001 },
    { name: "list_count" },
  );
  check(count, { "desk list counted": () => message(count) !== undefined });

  // Open the first record half of the time, as staff do from the list.
  const names = result && Array.isArray(result.values) ? result.values.map((row) => row[0]) : [];
  if (names.length && Math.random() < 0.5) {
    const form = deskCall("frappe.desk.form.load.getdoc", { doctype: list.doctype, name: names[0] }, { get: true, name: "open_form" });
    check(form, { "desk form opened": (r) => r.status === 200 });
  }

  sleep(Math.max(2, SHAPE.thinkMin / 2) + Math.random() * 6);
}
