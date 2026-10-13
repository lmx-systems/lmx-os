// Which controls an order's row offers, by its status. The server is the judge
// of every one of these; the lists only keep the console from offering a
// button that can only come back 409.

// Statuses dispatch can still cancel from (app/orders/cancellation.py): before
// the driver has the parts.
export const DISPATCH_CANCELLABLE = ['received', 'classified', 'held', 'queued', 'assigned', 'en_route_pickup']

// Still waiting for a driver: the only statuses an assign takes (app/orders/assignment.py).
export const ASSIGNABLE = ['held', 'queued']
