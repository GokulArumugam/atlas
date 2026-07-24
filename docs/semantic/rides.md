# Rides semantic notes

## table: rides.locations
One row per named pickup or drop-off location. Grain = 1 physical location.
- `id` is the location key used by `trips.start_location_id` and `trips.end_location_id`.
- `name` is the display location, such as Airport or Downtown; names are not globally unique without `city`.
- `city` is the city containing the location. Airport and Downtown are Bengaluru demo locations.

## table: rides.riders
One row per registered rider. Grain = 1 rider account.
- `id` joins to `trips.rider_id`.
- `full_name`, `phone`, and `email` are PII and must not be sampled or displayed to restricted users.
- `home_city` is the rider's declared home city, not necessarily the trip origin.

## table: rides.drivers
One row per driver. Grain = 1 driver account.
- `id` joins to `trips.driver_id`.
- `full_name` and `phone` are PII; use `id` or aggregate by `city` for operational analysis.
- `rating` is a current synthetic rating, not a rating at the time of a trip.

## table: rides.trips
One row per ride. Grain = 1 trip, including completed, cancelled, and ongoing trips.
- `rider_id` joins to `riders.id`; `driver_id` joins to `drivers.id`.
- `start_location_id` and `end_location_id` each join to `locations.id`; use two aliases when asking about a route.
- `rider_count` = passengers in the ride, NOT number of trips.
- `fare_amount` is the fare for this trip. Filter `status = 'completed'` for completed-ride revenue or averages.
- `trip_date` is the trip calendar date; this demo covers the last 120 days.
