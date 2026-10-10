"""Allowlisted listing display columns persisted from a collection pass.

Request, action and session fields are never copied into saved listings.
"""

LISTING_FIELDS = {
    "Id": "id",
    "JobTitle": "title",
    "Organization": "organization",
    "Division": "division",
    "Openings": "openings",
    "City": "city",
    "Level": "level",
    "ApplicationCount": "application_count",
    "Deadline": "deadline",
}
