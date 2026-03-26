---
name: snow create
description: File a new ServiceNow ticket
---

# /snow create

Create a new ServiceNow incident ticket. Ask the user to describe their issue, then:

1. Determine the appropriate **category** and **subcategory** using the IT Triage Playbook skill
2. Set an appropriate **priority** based on impact
3. Write a clear **short description** (one line, under 100 characters)
4. Write a detailed **description** with all relevant context
5. Use the `similar_incidents_for_text` tool to check for existing/related tickets first
6. If no duplicates, use the ServiceNow connector to create the incident
7. Confirm creation with the ticket number (e.g., INC0045231)
8. Let the user know what to expect next (SLA, email notifications)

## Example Interaction

User: /snow create my laptop screen is cracked