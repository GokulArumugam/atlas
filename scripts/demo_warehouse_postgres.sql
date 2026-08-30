-- ============================================================
-- Atlas demo warehouse — Postgres edition (small, video-friendly)
--
-- Mirrors src/atlas/data/generate.py exactly (schema names, column
-- names, PII columns) so the policy engine, semantic notes, mind map,
-- and deterministic generator all work unchanged.
--
-- Load with:  psql -f scripts/demo_warehouse_postgres.sql
-- (or mount as /docker-entrypoint-initdb.d/init.sql on first boot)
-- ============================================================

CREATE SCHEMA IF NOT EXISTS rides;
CREATE SCHEMA IF NOT EXISTS hr;
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS rides.locations (
    id   INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    city VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS rides.riders (
    id        INTEGER PRIMARY KEY,
    full_name VARCHAR NOT NULL,
    phone     VARCHAR NOT NULL,          -- PII: masked for everyone
    email     VARCHAR NOT NULL,          -- PII: masked for everyone
    home_city VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS rides.drivers (
    id        INTEGER PRIMARY KEY,
    full_name VARCHAR NOT NULL,          -- PII: masked for everyone
    phone     VARCHAR NOT NULL,          -- PII: masked for everyone
    rating    DOUBLE PRECISION,
    city      VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS rides.trips (
    id               INTEGER PRIMARY KEY,
    rider_id         INTEGER REFERENCES rides.riders(id),
    driver_id        INTEGER REFERENCES rides.drivers(id),
    start_location_id INTEGER REFERENCES rides.locations(id),
    end_location_id  INTEGER REFERENCES rides.locations(id),
    trip_date        DATE NOT NULL,
    rider_count      INTEGER NOT NULL,
    fare_amount      DOUBLE PRECISION NOT NULL,
    status           VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS hr.departments (
    id   INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS hr.employees (
    id            INTEGER PRIMARY KEY,
    full_name     VARCHAR NOT NULL,     -- PII
    department_id INTEGER REFERENCES hr.departments(id),
    salary        DOUBLE PRECISION NOT NULL,  -- PII (HR-only, unmasked for mitra)
    pan           VARCHAR NOT NULL,     -- PII: masked even for HR
    email         VARCHAR NOT NULL,     -- PII
    join_date     DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS meta.query_history (
    id          INTEGER PRIMARY KEY,
    user_name   VARCHAR NOT NULL,
    team        VARCHAR NOT NULL,
    sql_text    TEXT NOT NULL,
    executed_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Data — small and hand-checkable for the demo
-- ============================================================

TRUNCATE rides.trips, rides.riders, rides.drivers, rides.locations,
         hr.employees, hr.departments, meta.query_history;

INSERT INTO rides.locations VALUES
    (1, 'Airport',      'Bengaluru'),
    (2, 'Downtown',     'Bengaluru'),
    (3, 'Central Park', 'Bengaluru'),
    (4, 'Tech Park',    'Bengaluru'),
    (5, 'Stadium',      'Bengaluru'),
    (6, 'Mall',         'Bengaluru');

INSERT INTO rides.riders VALUES
    (1, 'Aarav Sharma',   '+91 98450 11223', 'aarav.sharma@example.in',  'Bengaluru'),
    (2, 'Diya Patel',     '+91 98450 23344', 'diya.patel@example.in',    'Bengaluru'),
    (3, 'Rohan Mehta',    '+91 98450 34455', 'rohan.mehta@example.in',   'Bengaluru'),
    (4, 'Isha Reddy',     '+91 98450 45566', 'isha.reddy@example.in',    'Bengaluru'),
    (5, 'Karan Nair',     '+91 98450 56677', 'karan.nair@example.in',    'Bengaluru'),
    (6, 'Meera Iyer',     '+91 98450 67788', 'meera.iyer@example.in',    'Bengaluru'),
    (7, 'Vikram Singh',   '+91 98450 78899', 'vikram.singh@example.in',  'Bengaluru'),
    (8, 'Ananya Rao',     '+91 98450 89900', 'ananya.rao@example.in',    'Bengaluru'),
    (9, 'Aditya Kulkarni','+91 98450 91011', 'aditya.k@example.in',      'Bengaluru'),
    (10, 'Nisha Verma',   '+91 98450 10202', 'nisha.verma@example.in',   'Bengaluru');

INSERT INTO rides.drivers VALUES
    (1, 'Suresh Kumar',   '+91 90080 11111', 4.85, 'Bengaluru'),
    (2, 'Ramesh Gowda',   '+91 90080 22222', 4.62, 'Bengaluru'),
    (3, 'Mahesh Reddy',   '+91 90080 33333', 4.91, 'Bengaluru'),
    (4, 'Prakash Naik',   '+91 90080 44444', 4.40, 'Bengaluru'),
    (5, 'Ganesh Prasad',  '+91 90080 55555', 4.75, 'Bengaluru'),
    (6, 'Umesh Hegde',    '+91 90080 66666', 4.55, 'Bengaluru');

INSERT INTO rides.trips VALUES
    (1,  1, 1, 1, 2, CURRENT_DATE - 10, 1, 420.00, 'completed'),
    (2,  2, 3, 2, 1, CURRENT_DATE - 10, 2, 510.00, 'completed'),
    (3,  3, 2, 4, 2, CURRENT_DATE - 9,  1, 260.00, 'completed'),
    (4,  4, 1, 3, 5, CURRENT_DATE - 9,  3, 340.00, 'completed'),
    (5,  5, 5, 6, 2, CURRENT_DATE - 8,  1, 180.00, 'cancelled'),
    (6,  6, 4, 1, 4, CURRENT_DATE - 8,  2, 480.00, 'completed'),
    (7,  7, 6, 5, 3, CURRENT_DATE - 7,  1, 230.00, 'completed'),
    (8,  8, 2, 2, 6, CURRENT_DATE - 7,  4, 290.00, 'completed'),
    (9,  9, 3, 1, 2, CURRENT_DATE - 6,  2, 445.00, 'completed'),
    (10, 10, 1, 4, 1, CURRENT_DATE - 6, 1, 505.00, 'completed'),
    (11, 1, 4, 2, 3, CURRENT_DATE - 5,  2, 210.00, 'completed'),
    (12, 2, 5, 1, 2, CURRENT_DATE - 5,  1, 430.00, 'cancelled'),
    (13, 3, 6, 5, 2, CURRENT_DATE - 4,  3, 385.00, 'completed'),
    (14, 4, 2, 3, 1, CURRENT_DATE - 4,  1, 275.00, 'completed'),
    (15, 5, 1, 6, 4, CURRENT_DATE - 3,  2, 360.00, 'completed'),
    (16, 6, 3, 2, 1, CURRENT_DATE - 3,  1, 495.00, 'completed'),
    (17, 7, 5, 4, 2, CURRENT_DATE - 2,  2, 255.00, 'completed'),
    (18, 8, 4, 1, 2, CURRENT_DATE - 2,  1, 470.00, 'completed'),
    (19, 9, 6, 3, 5, CURRENT_DATE - 1,  2, 320.00, 'completed'),
    (20, 10, 2, 2, 1, CURRENT_DATE - 1, 1, 515.00, 'completed'),
    (21, 1, 3, 1, 2, CURRENT_DATE,      2, 425.00, 'completed'),
    (22, 2, 1, 4, 1, CURRENT_DATE,      1, 500.00, 'completed'),
    (23, 3, 5, 2, 6, CURRENT_DATE,      3, 195.00, 'completed'),
    (24, 4, 6, 5, 2, CURRENT_DATE,      1, 370.00, 'completed'),
    (25, 5, 2, 1, 4, CURRENT_DATE - 12, 2, 460.00, 'completed'),
    (26, 6, 4, 2, 1, CURRENT_DATE - 12, 1, 485.00, 'completed'),
    (27, 7, 1, 6, 2, CURRENT_DATE - 11, 4, 205.00, 'cancelled'),
    (28, 8, 3, 5, 3, CURRENT_DATE - 11, 1, 310.00, 'completed'),
    (29, 9, 5, 1, 2, CURRENT_DATE - 14, 2, 450.00, 'completed'),
    (30, 10, 6, 2, 1, CURRENT_DATE - 14, 1, 520.00, 'completed'),
    (31, 1, 2, 4, 2, CURRENT_DATE - 15, 1, 265.00, 'completed'),
    (32, 2, 4, 3, 5, CURRENT_DATE - 15, 2, 330.00, 'completed'),
    (33, 3, 6, 1, 2, CURRENT_DATE - 16, 2, 440.00, 'completed'),
    (34, 4, 1, 2, 1, CURRENT_DATE - 16, 1, 530.00, 'completed'),
    (35, 5, 3, 5, 2, CURRENT_DATE - 17, 3, 390.00, 'completed'),
    (36, 6, 5, 6, 1, CURRENT_DATE - 17, 1, 190.00, 'completed'),
    (37, 7, 2, 1, 4, CURRENT_DATE - 18, 2, 475.00, 'completed'),
    (38, 8, 6, 2, 5, CURRENT_DATE - 18, 1, 240.00, 'completed'),
    (39, 9, 4, 5, 1, CURRENT_DATE - 19, 2, 365.00, 'completed'),
    (40, 10, 1, 3, 2, CURRENT_DATE - 19, 1, 285.00, 'completed');

INSERT INTO hr.departments VALUES
    (1, 'Engineering'),
    (2, 'HR'),
    (3, 'Marketing'),
    (4, 'Finance');

INSERT INTO hr.employees VALUES
    (1,  'Gokul Arumugam',  1, 1650000, 'ABCPX1234L', 'gokul@example.in',     DATE '2022-03-14'),
    (2,  'Mitra Joshi',     2, 1250000, 'BBCPX2345M', 'mitra@example.in',     DATE '2021-07-01'),
    (3,  'Arjun Desai',     3,  980000, 'CBCPX3456N', 'arjun@example.in',     DATE '2023-01-09'),
    (4,  'Priya Menon',     1, 1720000, 'DBCPX4567P', 'priya@example.in',     DATE '2020-11-23'),
    (5,  'Sneha Kulkarni',  4, 1100000, 'EBCPX5678Q', 'sneha@example.in',     DATE '2022-08-15'),
    (6,  'Rahul Bhatt',     1, 1420000, 'FBCPX6789R', 'rahul@example.in',     DATE '2023-05-02'),
    (7,  'Kavya Pillai',    2, 1180000, 'GBCPX7890S', 'kavya@example.in',     DATE '2021-12-06'),
    (8,  'Dev Malhotra',    3,  890000, 'HBCPX8901T', 'dev@example.in',       DATE '2024-02-19'),
    (9,  'Tara Krishnan',   4, 1260000, 'IBCPX9012U', 'tara@example.in',      DATE '2020-06-30'),
    (10, 'Nikhil Chandra',  1, 1550000, 'JBCPX0123V', 'nikhil@example.in',    DATE '2022-10-10');

-- Realistic past queries so the mind map learns joins the way real
-- deployments do (source #3 in the README's map story).
INSERT INTO meta.query_history VALUES
    (1,  'gokul', 'engineering', 'SELECT AVG(t.rider_count) FROM rides.trips t JOIN rides.locations s ON t.start_location_id = s.id JOIN rides.locations d ON t.end_location_id = d.id WHERE s.name = ''Airport'' AND d.name = ''Downtown''', NOW() - INTERVAL '9 days'),
    (2,  'gokul', 'engineering', 'SELECT t.trip_date, COUNT(*) FROM rides.trips t GROUP BY t.trip_date ORDER BY t.trip_date', NOW() - INTERVAL '8 days'),
    (3,  'mitra', 'hr',          'SELECT d.name, AVG(e.salary) FROM hr.employees e JOIN hr.departments d ON e.department_id = d.id GROUP BY d.name', NOW() - INTERVAL '7 days'),
    (4,  'gokul', 'engineering', 'SELECT t.status, COUNT(*) FROM rides.trips t GROUP BY t.status', NOW() - INTERVAL '6 days'),
    (5,  'gokul', 'engineering', 'SELECT dr.full_name, COUNT(*) AS trips FROM rides.trips t JOIN rides.drivers dr ON t.driver_id = dr.id GROUP BY dr.full_name ORDER BY trips DESC LIMIT 10', NOW() - INTERVAL '5 days'),
    (6,  'arjun', 'marketing',   'SELECT l.name, COUNT(*) FROM rides.trips t JOIN rides.locations l ON t.start_location_id = l.id GROUP BY l.name', NOW() - INTERVAL '4 days'),
    (7,  'mitra', 'hr',          'SELECT e.full_name, e.email FROM hr.employees e WHERE e.department_id = 1', NOW() - INTERVAL '3 days'),
    (8,  'gokul', 'engineering', 'SELECT r.full_name, COUNT(*) FROM rides.trips t JOIN rides.riders r ON t.rider_id = r.id GROUP BY r.full_name ORDER BY COUNT(*) DESC LIMIT 5', NOW() - INTERVAL '2 days'),
    (9,  'arjun', 'marketing',   'SELECT AVG(t.fare_amount) FROM rides.trips t WHERE t.status = ''completed''', NOW() - INTERVAL '1 day'),
    (10, 'gokul', 'engineering', 'SELECT t.fare_amount, t.trip_date FROM rides.trips t WHERE t.status = ''completed'' ORDER BY t.fare_amount DESC LIMIT 5', NOW());
