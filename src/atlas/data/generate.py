"""Create the reproducible synthetic DuckDB warehouse used by the demo."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import random

import duckdb


LOCATIONS = [
    (1, "Airport", "Bengaluru"),
    (2, "Downtown", "Bengaluru"),
    (3, "Indiranagar", "Bengaluru"),
    (4, "Whitefield", "Bengaluru"),
    (5, "Koramangala", "Bengaluru"),
    (6, "Railway Station", "Bengaluru"),
    (7, "Bandra", "Mumbai"),
    (8, "Andheri", "Mumbai"),
    (9, "Powai", "Mumbai"),
    (10, "Connaught Place", "Delhi"),
    (11, "Hauz Khas", "Delhi"),
    (12, "Hitech City", "Hyderabad"),
]

DEPARTMENTS = ["Engineering", "HR", "Marketing", "Finance", "Operations"]
FIRST_NAMES = [
    "Aarav", "Aditi", "Akash", "Ananya", "Arjun", "Deepa", "Divya", "Isha",
    "Karthik", "Kavya", "Manish", "Meera", "Nikhil", "Pooja", "Rahul", "Riya",
    "Rohan", "Sana", "Siddharth", "Tanvi", "Vikram", "Yash",
]
LAST_NAMES = [
    "Agarwal", "Bhat", "Chatterjee", "Gupta", "Iyer", "Jain", "Kapoor", "Kulkarni",
    "Mehta", "Nair", "Patel", "Rao", "Reddy", "Shah", "Sharma", "Singh", "Verma",
]
CITIES = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune"]


def _person_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _phone(rng: random.Random) -> str:
    return "9" + "".join(str(rng.randrange(10)) for _ in range(9))


def _pan(rng: random.Random) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(rng.choice(letters) for _ in range(5)) + "".join(
        str(rng.randrange(10)) for _ in range(4)
    ) + rng.choice(letters)


def _query_history(rng: random.Random, reference_date: date) -> list[tuple[int, str, str, str, datetime]]:
    """Return varied, executable historical SQL with explicit real-key joins."""
    # Keep these as complete queries instead of manufacturing strings by replacing
    # literals.  The history is deliberately broad enough to teach a join miner
    # several real analytical patterns, rather than merely repeating nine examples.
    rides_patterns = [
        """SELECT s.name AS start_location, d.name AS end_location,
                  AVG(t.rider_count) AS average_riders
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE t.status = 'completed'
           GROUP BY s.name, d.name""",
        """SELECT r.home_city, COUNT(*) AS trip_count, ROUND(SUM(t.fare_amount), 2) AS revenue
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           WHERE t.status = 'completed'
           GROUP BY r.home_city""",
        """SELECT dr.city, COUNT(*) AS completed_trips, AVG(t.fare_amount) AS avg_fare
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           WHERE t.status = 'completed'
           GROUP BY dr.city""",
        """SELECT s.city, COUNT(*) AS airport_trips
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE s.name = 'Airport' AND d.name = 'Downtown'
           GROUP BY s.city""",
        """SELECT r.full_name, COUNT(*) AS cancelled_trips
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           WHERE t.status = 'cancelled'
           GROUP BY r.full_name
           ORDER BY cancelled_trips DESC""",
        """SELECT dr.full_name, AVG(t.rider_count) AS avg_passengers
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           GROUP BY dr.full_name""",
        """SELECT s.city, AVG(t.fare_amount) AS average_fare
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           WHERE t.status = 'completed'
           GROUP BY s.city""",
        """SELECT d.city, SUM(t.fare_amount) AS revenue
           FROM rides.trips t
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE t.status = 'completed'
           GROUP BY d.city""",
        """SELECT s.name, COUNT(*) AS departure_count
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           GROUP BY s.name
           ORDER BY departure_count DESC""",
        """SELECT d.name, COUNT(*) AS arrival_count
           FROM rides.trips t
           JOIN rides.locations d ON t.end_location_id = d.id
           GROUP BY d.name
           ORDER BY arrival_count DESC""",
        """SELECT s.name, d.name, SUM(t.fare_amount) AS route_revenue
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE t.status = 'completed'
           GROUP BY s.name, d.name""",
        """SELECT s.city, d.city, AVG(t.rider_count) AS avg_passengers
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           JOIN rides.locations d ON t.end_location_id = d.id
           GROUP BY s.city, d.city""",
        """SELECT r.home_city, AVG(t.rider_count) AS avg_group_size
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           WHERE t.status = 'completed'
           GROUP BY r.home_city""",
        """SELECT r.home_city, SUM(t.fare_amount) AS rider_revenue
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           WHERE t.trip_date >= DATE '2025-01-01'
           GROUP BY r.home_city""",
        """SELECT r.home_city, COUNT(*) AS cancellation_count
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           WHERE t.status = 'cancelled'
           GROUP BY r.home_city""",
        """SELECT r.id, COUNT(*) AS trip_count
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           GROUP BY r.id
           ORDER BY trip_count DESC""",
        """SELECT r.home_city, MAX(t.fare_amount) AS highest_fare
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           GROUP BY r.home_city""",
        """SELECT dr.city, SUM(t.fare_amount) AS driver_revenue
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           WHERE t.status = 'completed'
           GROUP BY dr.city""",
        """SELECT dr.city, AVG(t.rider_count) AS average_passengers
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           GROUP BY dr.city""",
        """SELECT dr.id, COUNT(*) AS assigned_trips
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           GROUP BY dr.id
           ORDER BY assigned_trips DESC""",
        """SELECT dr.city, COUNT(*) AS cancelled_trips
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           WHERE t.status = 'cancelled'
           GROUP BY dr.city""",
        """SELECT dr.rating, AVG(t.fare_amount) AS average_fare
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           WHERE t.status = 'completed'
           GROUP BY dr.rating""",
        """SELECT s.name, COUNT(*) AS rides_to_downtown
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE d.name = 'Downtown'
           GROUP BY s.name""",
        """SELECT d.name, COUNT(*) AS rides_from_airport
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE s.name = 'Airport'
           GROUP BY d.name""",
        """SELECT r.home_city, s.city, COUNT(*) AS trips
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           JOIN rides.locations s ON t.start_location_id = s.id
           GROUP BY r.home_city, s.city""",
        """SELECT dr.city, d.city, COUNT(*) AS trips
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           JOIN rides.locations d ON t.end_location_id = d.id
           GROUP BY dr.city, d.city""",
        """SELECT s.city, COUNT(*) AS completed_rides
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           WHERE t.status = 'completed' AND t.rider_count >= 3
           GROUP BY s.city""",
        """SELECT d.city, AVG(t.fare_amount) AS premium_ride_fare
           FROM rides.trips t
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE t.fare_amount >= 500
           GROUP BY d.city""",
        """SELECT r.home_city, COUNT(*) AS airport_departures
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           JOIN rides.locations s ON t.start_location_id = s.id
           WHERE s.name = 'Airport'
           GROUP BY r.home_city""",
        """SELECT dr.city, COUNT(*) AS downtown_arrivals
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE d.name = 'Downtown'
           GROUP BY dr.city""",
        """SELECT s.name, d.name, MAX(t.fare_amount) AS maximum_fare
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           JOIN rides.locations d ON t.end_location_id = d.id
           GROUP BY s.name, d.name""",
        """SELECT r.home_city, dr.city, AVG(t.rider_count) AS average_riders
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           JOIN rides.drivers dr ON t.driver_id = dr.id
           GROUP BY r.home_city, dr.city""",
        """SELECT s.city, COUNT(DISTINCT t.rider_id) AS active_riders
           FROM rides.trips t
           JOIN rides.locations s ON t.start_location_id = s.id
           WHERE t.status = 'completed'
           GROUP BY s.city""",
        """SELECT d.city, COUNT(DISTINCT t.driver_id) AS active_drivers
           FROM rides.trips t
           JOIN rides.locations d ON t.end_location_id = d.id
           WHERE t.status = 'completed'
           GROUP BY d.city""",
        """SELECT r.home_city, MIN(t.trip_date) AS first_trip_date
           FROM rides.trips t
           JOIN rides.riders r ON t.rider_id = r.id
           GROUP BY r.home_city""",
        """SELECT dr.city, MAX(t.trip_date) AS latest_trip_date
           FROM rides.trips t
           JOIN rides.drivers dr ON t.driver_id = dr.id
           GROUP BY dr.city""",
    ]
    hr_patterns = [
        """SELECT d.name AS department, COUNT(*) AS employee_count, AVG(e.salary) AS average_salary
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           GROUP BY d.name""",
        """SELECT d.name AS department, COUNT(*) AS recent_hires
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           WHERE e.join_date >= DATE '2024-01-01'
           GROUP BY d.name""",
        """SELECT d.name AS department, MAX(e.salary) AS highest_salary
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           GROUP BY d.name""",
        """SELECT d.name AS department, MIN(e.salary) AS lowest_salary
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           GROUP BY d.name""",
        """SELECT d.name AS department, SUM(e.salary) AS payroll
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           GROUP BY d.name""",
        """SELECT d.name AS department, COUNT(*) AS long_tenure_employees
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           WHERE e.join_date < DATE '2020-01-01'
           GROUP BY d.name""",
        """SELECT d.name AS department, MAX(e.join_date) AS latest_hire
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           GROUP BY d.name""",
        """SELECT d.id AS department_id, COUNT(*) AS employee_count
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           GROUP BY d.id""",
        """SELECT d.name AS department, COUNT(*) AS high_salary_employees
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           WHERE e.salary >= 1000000
           GROUP BY d.name""",
        """SELECT d.name AS department, AVG(e.salary) AS recent_hire_salary
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           WHERE e.join_date >= DATE '2022-01-01'
           GROUP BY d.name""",
        """SELECT d.name AS department, COUNT(*) AS employees_since_2023
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           WHERE e.join_date >= DATE '2023-01-01'
           GROUP BY d.name""",
        """SELECT d.name AS department, MAX(e.salary) - MIN(e.salary) AS salary_range
           FROM hr.employees e
           JOIN hr.departments d ON e.department_id = d.id
           GROUP BY d.name""",
    ]
    users = [("gokul", "engineering"), ("mitra", "hr"), ("arjun", "marketing")]
    history = []
    for history_id in range(1, 241):
        # Marketing's historical questions remain ride focused; HR may ask either kind.
        user, team = rng.choice(users)
        candidates = rides_patterns if user != "mitra" else rides_patterns + hr_patterns
        executed = datetime.combine(
            reference_date - timedelta(days=rng.randrange(120)),
            datetime.min.time(),
        ) + timedelta(hours=rng.randrange(24), minutes=rng.randrange(60))
        history.append((history_id, user, team, rng.choice(candidates), executed))
    return history


def generate(db_path: str = "data/warehouse.duckdb") -> None:
    """Recreate a complete deterministic demo warehouse at ``db_path``."""
    rng = random.Random(42)
    reference_date = date.today()
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(path))
    try:
        for schema in ("meta", "hr", "rides"):
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        connection.execute("CREATE SCHEMA rides")
        connection.execute("CREATE SCHEMA hr")
        connection.execute("CREATE SCHEMA meta")

        connection.execute("CREATE TABLE rides.locations (id INTEGER PRIMARY KEY, name VARCHAR, city VARCHAR)")
        connection.execute(
            "CREATE TABLE rides.riders (id INTEGER PRIMARY KEY, full_name VARCHAR, phone VARCHAR, email VARCHAR, home_city VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE rides.drivers (id INTEGER PRIMARY KEY, full_name VARCHAR, phone VARCHAR, rating DOUBLE, city VARCHAR)"
        )
        connection.execute(
            """CREATE TABLE rides.trips (
                id INTEGER PRIMARY KEY, rider_id INTEGER, driver_id INTEGER,
                start_location_id INTEGER, end_location_id INTEGER, trip_date DATE,
                rider_count INTEGER, fare_amount DOUBLE, status VARCHAR,
                FOREIGN KEY (rider_id) REFERENCES rides.riders(id),
                FOREIGN KEY (driver_id) REFERENCES rides.drivers(id),
                FOREIGN KEY (start_location_id) REFERENCES rides.locations(id),
                FOREIGN KEY (end_location_id) REFERENCES rides.locations(id)
            )"""
        )
        connection.execute("CREATE TABLE hr.departments (id INTEGER PRIMARY KEY, name VARCHAR)")
        connection.execute(
            """CREATE TABLE hr.employees (
                id INTEGER PRIMARY KEY, full_name VARCHAR, department_id INTEGER,
                salary DOUBLE, pan VARCHAR, email VARCHAR, join_date DATE,
                FOREIGN KEY (department_id) REFERENCES hr.departments(id)
            )"""
        )
        connection.execute(
            """CREATE TABLE meta.query_history (
                id INTEGER PRIMARY KEY, user_name VARCHAR, team VARCHAR,
                sql_text VARCHAR, executed_at TIMESTAMP
            )"""
        )

        connection.executemany("INSERT INTO rides.locations VALUES (?, ?, ?)", LOCATIONS)
        riders = []
        for rider_id in range(1, 501):
            full_name = _person_name(rng)
            local_part = full_name.lower().replace(" ", ".")
            riders.append((rider_id, full_name, _phone(rng), f"{local_part}{rider_id}@example.in", rng.choice(CITIES)))
        connection.executemany("INSERT INTO rides.riders VALUES (?, ?, ?, ?, ?)", riders)

        drivers = []
        for driver_id in range(1, 121):
            drivers.append((driver_id, _person_name(rng), _phone(rng), round(rng.uniform(3.5, 5.0), 2), rng.choice(CITIES)))
        connection.executemany("INSERT INTO rides.drivers VALUES (?, ?, ?, ?, ?)", drivers)

        trips = []
        for trip_id in range(1, 20_001):
            if rng.random() < 0.18:
                start_location_id, end_location_id = 1, 2
            else:
                start_location_id = rng.randint(1, len(LOCATIONS))
                end_location_id = rng.randint(1, len(LOCATIONS))
                while end_location_id == start_location_id:
                    end_location_id = rng.randint(1, len(LOCATIONS))
            status = rng.choices(["completed", "cancelled", "ongoing"], weights=[88, 9, 3], k=1)[0]
            base_fare = rng.uniform(90, 850)
            trips.append((
                trip_id, rng.randint(1, 500), rng.randint(1, 120), start_location_id,
                end_location_id, reference_date - timedelta(days=rng.randrange(120)),
                rng.randint(1, 4), round(base_fare, 2), status,
            ))
        connection.executemany("INSERT INTO rides.trips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", trips)

        connection.executemany(
            "INSERT INTO hr.departments VALUES (?, ?)",
            list(enumerate(DEPARTMENTS, start=1)),
        )
        employees = []
        for employee_id in range(1, 301):
            full_name = _person_name(rng)
            local_part = full_name.lower().replace(" ", ".")
            employees.append((
                employee_id, full_name, rng.randint(1, len(DEPARTMENTS)),
                round(rng.uniform(400_000, 6_000_000), 2), _pan(rng),
                f"{local_part}{employee_id}@company.in",
                reference_date - timedelta(days=rng.randrange(365 * 8)),
            ))
        connection.executemany("INSERT INTO hr.employees VALUES (?, ?, ?, ?, ?, ?, ?)", employees)
        connection.executemany("INSERT INTO meta.query_history VALUES (?, ?, ?, ?, ?)", _query_history(rng, reference_date))

        counts = connection.execute(
            """SELECT 'rides.locations' AS table_name, COUNT(*) AS row_count FROM rides.locations
               UNION ALL SELECT 'rides.riders', COUNT(*) FROM rides.riders
               UNION ALL SELECT 'rides.drivers', COUNT(*) FROM rides.drivers
               UNION ALL SELECT 'rides.trips', COUNT(*) FROM rides.trips
               UNION ALL SELECT 'hr.departments', COUNT(*) FROM hr.departments
               UNION ALL SELECT 'hr.employees', COUNT(*) FROM hr.employees
               UNION ALL SELECT 'meta.query_history', COUNT(*) FROM meta.query_history
               ORDER BY table_name"""
        ).fetchall()
        print(f"Warehouse generated: {path}")
        print(f"{'table':<20} {'rows':>8}")
        for table_name, row_count in counts:
            print(f"{table_name:<20} {row_count:>8}")
    finally:
        connection.close()


if __name__ == "__main__":
    generate()
