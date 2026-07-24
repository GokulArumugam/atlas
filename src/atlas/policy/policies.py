"""Literal, dependency-free policy configuration for the demo users."""

POLICY_CONFIG = {
    "tables": {
        "rides": {
            "locations": {"id", "name", "city"},
            "riders": {"id", "full_name", "phone", "email", "home_city"},
            "drivers": {"id", "full_name", "phone", "rating", "city"},
            "trips": {
                "id", "rider_id", "driver_id", "start_location_id", "end_location_id",
                "trip_date", "rider_count", "fare_amount", "status",
            },
        },
        "hr": {
            "departments": {"id", "name"},
            "employees": {"id", "full_name", "department_id", "salary", "pan", "email", "join_date"},
        },
    },
    "pii_columns": {
        ("rides", "riders", "phone"), ("rides", "riders", "email"), ("rides", "riders", "full_name"),
        ("rides", "drivers", "phone"), ("rides", "drivers", "full_name"),
        ("hr", "employees", "pan"), ("hr", "employees", "email"),
        ("hr", "employees", "full_name"), ("hr", "employees", "salary"),
    },
    "users": {
        "gokul": {
            "team": "engineering",
            "visible_tables": {("rides", "locations"), ("rides", "riders"), ("rides", "drivers"), ("rides", "trips")},
            "unmasked_pii": set(),
        },
        "priya": {
            "team": "hr",
            "visible_tables": {
                ("rides", "locations"), ("rides", "riders"), ("rides", "drivers"), ("rides", "trips"),
                ("hr", "departments"), ("hr", "employees"),
            },
            # HR may use HR PII, apart from PAN which is deliberately absent.
            "unmasked_pii": {
                ("hr", "employees", "full_name"), ("hr", "employees", "email"), ("hr", "employees", "salary"),
            },
        },
        "arjun": {
            "team": "marketing",
            "visible_tables": {("rides", "trips"), ("rides", "locations")},
            "unmasked_pii": set(),
        },
        "auditor": {
            "team": "audit",
            "visible_tables": set(),
            "unmasked_pii": set(),
        },
    },
    "masking_expression": "'***MASKED***'",
}
