---
name: snow update
description: Add a comment or update a ServiceNow ticket
---

# /snow update

Update an existing ServiceNow ticket. The user can:
- Add a comment (visible to IT and the user)
- Provide additional information
- Request closure ("it's fixed now")

## Usage

- `/snow update INC0045231 it's working now, you can close it`
- `/snow update` (will prompt for ticket number and update)

## Workflow

1. If no ticket number provided, show their open tickets (like `/snow mine`) and ask which one
2. Fetch the current ticket details with `get_incident_details`
3. Show the current state to the user
4. Apply the requested update (comment, additional details, etc.)
5. Confirm the update was applied

## Notes

- Users can add comments but cannot directly close tickets (only IT agents can)
- If the user says "close it" or "it's fixed", add a comment saying the issue is resolved and let them know IT will formally close it
- Users cannot change the assigned team or priority after creation
