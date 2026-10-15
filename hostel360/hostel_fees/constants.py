"""Names shared by the fee module, its reports and its tests."""

INVOICE = "Hostel Fee Invoice"
INVOICE_ITEM = "Hostel Fee Invoice Item"
PAYMENT = "Hostel Payment"
ALLOCATION = "Hostel Payment Allocation"
TRANSACTION = "Hostel Payment Transaction"
TRANSACTION_INVOICE = "Hostel Payment Transaction Invoice"
STRUCTURE = "Hostel Fee Structure"
FEE_HEAD = "Hostel Fee Head"
SETTINGS = "Hostel Payment Settings"

# Invoice statuses
DRAFT = "Draft"
UNPAID = "Unpaid"
PARTLY_PAID = "Partly Paid"
PAID = "Paid"
OVERDUE = "Overdue"
CANCELLED = "Cancelled"
PAYABLE_STATUSES = (UNPAID, PARTLY_PAID, OVERDUE)

INVOICE_TYPES = ("Hostel Fee", "Mess Bill", "Fine", "Other")

# Modes of payment
ONLINE = "Online"
CASH = "Cash"
OFFLINE_MODES = ("Cash", "UPI", "Demand Draft", "Cheque", "Bank Transfer")
MODES_OF_PAYMENT = (ONLINE, *OFFLINE_MODES)

# Payment transaction statuses
TX_INITIATED = "Initiated"
TX_PENDING = "Pending"
TX_SUCCESS = "Success"
TX_FAILED = "Failed"
TX_EXPIRED = "Expired"
TX_REFUNDED = "Refunded"
LIVE_TX_STATUSES = (TX_INITIATED, TX_PENDING)

CURRENCY = "INR"

# Fee heads other modules bill against; they must never be deleted or renamed.
SYSTEM_FEE_HEADS = ("Mess Charges", "Mess Extras", "Fine")

# An online payment can cover at most this many invoices; keeps gateway notes and receipts readable.
MAX_INVOICES_PER_PAYMENT = 25
