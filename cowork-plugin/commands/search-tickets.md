---
name: snow search
description: Search for ServiceNow tickets by keyword or number
---

# /snow search

Search for ServiceNow tickets. Supports:
- Ticket number lookup: `/snow search INC0045231`
- Keyword search: `/snow search printer floor 3`
- Natural language: `/snow search WiFi issues this week`

## Workflow

1. If input looks like a ticket number (starts with INC, CHG, RITM, REQ), use `get_incident_details` for a direct lookup
2. If input is natural language, use `intelligent_search` or `similar_incidents_for_text` to find matching tickets
3. Display results with:
   - Ticket number
   - Summary
   - Status
   - Created date
   - Assigned team
4. If multiple results, show the top 5 most relevant
5. Ask if they want more details on any specific ticket

## Access Notes

Results are filtered by the user's ServiceNow permissions. ESS users will only see their own tickets. ITIL users can see all tickets.
