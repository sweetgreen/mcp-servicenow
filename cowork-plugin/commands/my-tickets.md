---
name: snow mine
description: View your open ServiceNow tickets
---

# /snow mine

Show the user's open ServiceNow tickets. Use the `get_incidents_by_filter` tool with a filter for the current user's open incidents (state != Closed, state != Canceled).

Display results in a clean format:
- Ticket number (e.g., INC0045231)
- Short description
- Current status (New, In Progress, On Hold, Resolved)
- Priority
- Assigned team
- Last update time and most recent comment/work note

If no open tickets, say so and ask if they need to create one.
